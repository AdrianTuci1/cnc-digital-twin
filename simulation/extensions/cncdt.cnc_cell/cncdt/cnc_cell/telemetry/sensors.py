"""Citirea senzorilor din scena Isaac Sim.

Aici se rezolvă „de unde luăm datele". Sunt două niveluri:

**Nivel 1 — citire reală din scenă.** Se caută primii cunoscuți și se citesc atribute.
Articulațiile se citesc prin API-ul de articulații, care s-a mutat între versiunile
Isaac Sim. Importul este izolat în :func:`load_articulation_class`, deci codul funcționează
pe ambele variante.

**Nivel 2 — rezervă sintetică.** Dacă scena nu are primii așteptați (de exemplu înainte de
a fi autorată), se folosește :class:`~cncdt.cnc_cell.telemetry.synthetic.SyntheticCell`.
Astfel pipeline-ul de date poate fi validat complet înainte de a exista modelul 3D.

Căile primilor sunt configurabile, pentru că depind de modul în care este construită scena.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..degradation.model import DegradationState
from .synthetic import SyntheticCell

# Căi candidate pentru fiecare sursă de date, în ordinea de încercare.
# Se potrivesc scenelor uzuale pentru celule CNC; ajustați-le pentru scena dumneavoastră.
CANDIDATE_PATHS: dict[str, tuple[str, ...]] = {
    "cnc_spindle": (
        "/World/CNC/Spindle",
        "/World/CNC_5Axis/Spindle",
        "/World/Machines/CNC/Spindle",
    ),
    "robot1": (
        "/World/Robot1",
        "/World/Robots/Robot1",
        "/World/UR10",
    ),
    "conveyor_infeed": (
        "/World/Conveyor1",
        "/World/Conveyors/Infeed",
    ),
    "conveyor_qa": (
        "/World/Conveyor2",
        "/World/Conveyors/QA",
    ),
    "inspection_camera": (
        "/World/QA/Camera",
        "/World/Camera_QA",
        "/World/InspectionCamera",
    ),
    # Camera de la zona lamei/piston. Cadrul ei este cel care declanșează decizia de
    # sortare, deci este căutată separat de camera de inspecție de la ieșirea din mașină.
    "diverter_camera": (
        "/World/QA/DiverterCamera",
        "/World/Diverter/Camera",
        "/World/Sorter/Camera",
    ),
    "reject_piston": (
        "/World/Scrap/Piston",
        "/World/Actuators/RejectPiston",
    ),
    # Efectorul final al manipulatorului. Stă pe flanșa brațului, al cărei nume vine din
    # asset-ul UR10, deci are nevoie de mai multe căi candidate.
    "robot_gripper": (
        "/World/Robot1/wrist_3_link/Gripper",
        "/World/Robot1/tool0/Gripper",
        "/World/Robot1/ee_link/Gripper",
        "/World/Robot1/Gripper",
    ),
    # Piesa prinsă de griper. Nu este citită ca senzor; calea ei este ținta articulației de
    # prindere (`cncdt/cnc_cell/grasping.py`).
    "part": (
        "/World/Part",
        "/World/Parts/Current",
    ),
}

# Atributele USD folosite pentru semnalele de proces, când primul există.
# Acestea sunt valori sintetice autorate pe prim, nu măsurători din solverul de fizică.
PROCESS_ATTRIBUTES: dict[str, str] = {
    "spindle_load": "cncdt:process:spindleLoad",
    "vibration": "cncdt:process:vibration",
    "temp_c": "cncdt:process:temperature",
    "coolant_flow_lpm": "cncdt:process:coolantFlow",
    "axis_feed": "cncdt:process:axisFeed",
}


def log(message: str, level: str = "info") -> None:
    try:  # pragma: no cover - disponibil doar în Isaac Sim
        import carb  # type: ignore

        getattr(carb.log_info if level == "info" else carb.log_warn)(f"[cnc_cell] {message}")
    except Exception:
        print(f"[cnc_cell][{level}] {message}", flush=True)


def load_articulation_class() -> type | None:
    """Întoarce clasa de articulație potrivită versiunii de Isaac Sim instalate.

    Isaac Sim 5.x a mutat pachetele sub prefixul ``isaacsim``; 4.x folosea ``omni.isaac``.
    """
    for module_path, class_name in (
        ("isaacsim.core.api.articulations", "Articulation"),
        ("omni.isaac.core.articulations", "Articulation"),
        ("omni.isaac.core.articulations.articulation", "Articulation"),
    ):
        try:
            module = __import__(module_path, fromlist=[class_name])
            return getattr(module, class_name)
        except (ImportError, AttributeError):
            continue
    return None


def get_stage() -> Any:
    """Întoarce stage-ul USD curent, sau ``None`` în afara Isaac Sim."""
    try:
        import omni.usd  # type: ignore

        return omni.usd.get_context().get_stage()
    except Exception:
        return None


def resolve_prim_path(stage: Any, candidates: Iterable[str]) -> str | None:
    """Întoarce prima cale existentă în scenă."""
    if stage is None:
        return None
    for path in candidates:
        try:
            if stage.GetPrimAtPath(path).IsValid():
                return path
        except Exception:
            continue
    return None


@dataclass
class SceneSensors:
    """Citește un eșantion de senzori din scenă, cu rezervă sintetică.

    ``degradation`` este starea partajată cu restul extensiei: uzura și frecarea se aplică
    atât în scenă, cât și în semnalul publicat.
    """

    degradation: DegradationState
    synthetic: SyntheticCell | None = None
    cell_id: str = "cell-a"
    device_id: str = "cnc-cell-01"

    _stage: Any = field(default=None, init=False)
    _paths: dict[str, str] = field(default_factory=dict, init=False)
    _articulation: Any = field(default=None, init=False)
    _use_synthetic: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.synthetic is None:
            self.synthetic = SyntheticCell(degradation=self.degradation)

    # ------------------------------------------------------------ inițializare
    def bind(self) -> dict[str, str]:
        """Leagă senzorii de scenă. Întoarce căile găsite.

        Se apelează după ce scena este încărcată. Dacă nu se găsește niciun prim de proces,
        citirea trece pe semnal sintetic.
        """
        self._stage = get_stage()
        if self._stage is None:
            log("nu pot accesa stage-ul USD; folosesc semnal sintetic", level="warn")
            self._use_synthetic = True
            return {}

        found: dict[str, str] = {}
        for name, candidates in CANDIDATE_PATHS.items():
            path = resolve_prim_path(self._stage, candidates)
            if path is not None:
                found[name] = path

        self._paths = found

        articulation_class = load_articulation_class()
        robot_path = found.get("robot1")
        if articulation_class is not None and robot_path is not None:
            try:
                self._articulation = articulation_class(prim_path=robot_path, name="cnc_robot1")
                log(f"articulație legată la {robot_path}")
            except Exception as exc:
                log(f"nu am putut lega articulația de la {robot_path}: {exc}", level="warn")
                self._articulation = None

        # Fără niciun prim de proces, semnalul sintetic este singura sursă coerentă.
        has_process_prim = any(
            self._read_attribute(name) is not None for name in PROCESS_ATTRIBUTES
        )
        self._use_synthetic = not has_process_prim

        if self._use_synthetic:
            log(
                "nu am găsit primii de senzori în scenă; telemetria este generată sintetic. "
                "Adăugați atributele cncdt:process:* pe primii mașinii sau ajustați "
                "CANDIDATE_PATHS pentru scena dumneavoastră.",
                level="warn",
            )
        else:
            log(f"senzori legați: {', '.join(sorted(found))}")
        return found

    @property
    def using_synthetic(self) -> bool:
        return self._use_synthetic

    @property
    def bound_paths(self) -> dict[str, str]:
        return dict(self._paths)

    # ------------------------------------------------------------ citire
    def _read_attribute(self, sensor: str) -> float | None:
        """Citește valoarea unui atribut de proces de pe primul mașinii."""
        if self._stage is None:
            return None
        path = self._paths.get("cnc_spindle")
        if path is None:
            return None
        attribute_name = PROCESS_ATTRIBUTES.get(sensor)
        if attribute_name is None:
            return None
        try:
            prim = self._stage.GetPrimAtPath(path)
            attribute = prim.GetAttribute(attribute_name)
            if not attribute or not attribute.IsValid():
                return None
            value = attribute.Get()
            return float(value) if value is not None else None
        except Exception:
            return None

    def _read_joint_efforts(self) -> list[float]:
        """Eforturile per articulație, când articulația este legată."""
        if self._articulation is None:
            return []
        try:
            efforts = self._articulation.get_measured_joint_efforts()
            return [float(value) for value in efforts]
        except Exception as exc:
            log(f"citirea eforturilor a eșuat: {exc}", level="warn")
            self._articulation = None
            return []

    def read(self, dt_seconds: float = 0.1) -> dict[str, Any]:
        """Produce un eșantion de telemetrie, din scenă sau sintetic."""
        if self._use_synthetic or self.synthetic is None:
            sample = dict(self.synthetic.sample(dt_seconds)) if self.synthetic else {}
        else:
            sample = self._read_from_scene(dt_seconds)

        sample.update(
            {
                "cell_id": self.cell_id,
                "device_id": self.device_id,
            }
        )
        return sample

    def _read_from_scene(self, dt_seconds: float) -> dict[str, Any]:
        """Compune eșantionul din valorile citite plus modelul de degradare."""
        self.degradation.advance(
            dt_seconds,
            cutting=True,
            moving=True,
        )

        # Valorile citite din atribut au prioritate; restul se completează sintetic,
        # ca lotul să aibă întotdeauna aceleași chei.
        fallback = self.synthetic.sample(dt_seconds) if self.synthetic else {}
        sample: dict[str, Any] = dict(fallback)

        for sensor in PROCESS_ATTRIBUTES:
            value = self._read_attribute(sensor)
            if value is not None:
                sample[sensor] = round(value, 5)

        efforts = self._read_joint_efforts()
        if efforts:
            average = sum(efforts) / len(efforts)
            sample["joint_effort"] = round(average, 4)
            sample["joint_effort_max"] = round(max(efforts), 4)
            sample["joint_count"] = len(efforts)

        tool = self.degradation.tool
        joint = self.degradation.joint
        sample.update(
            {
                "tool_wear_index": round(tool.index, 5),
                "joint_friction_index": round(joint.index, 5),
                # Celula Isaac are un singur model de frecare, aplicat ambelor brațe: coloanele
                # per braț există în contract pentru geamănul MuJoCo (ADR 0026), deci aici poartă
                # aceeași valoare.
                "joint_friction_index_arm1": round(joint.index, 5),
                "joint_friction_index_arm2": round(joint.index, 5),
                "surface_quality": round(tool.surface_quality, 5),
            }
        )
        return sample

    # ------------------------------------------------------------ aplicare în scenă
    def apply_degradation_to_scene(self) -> list[str]:
        """Scrie degradarea în proprietățile fizice ale scenei.

        Uzura sculei nu are echivalent fizic direct: se reflectă în atributele de proces,
        pe care le folosește și citirea. Frecarea articulațiilor are echivalent fizic și se
        scrie pe primul robotului.

        Întoarce lista modificărilor aplicate, pentru jurnal.
        """
        applied: list[str] = []
        if self._stage is None:
            return applied

        tool = self.degradation.tool
        joint = self.degradation.joint

        # Atribute de proces: consumate de read() și vizibile în scenă.
        spindle_path = self._paths.get("cnc_spindle")
        if spindle_path is not None:
            for sensor, base in (("spindle_load", 0.58), ("vibration", 1.35)):
                multiplier = (
                    tool.load_multiplier if sensor == "spindle_load" else tool.vibration_multiplier
                )
                self._set_attribute(spindle_path, PROCESS_ATTRIBUTES[sensor], round(base * multiplier, 5))
            applied.append(f"proces@{spindle_path}")

        # Frecarea articulațiilor: proprietate fizică reală, în două dialecte USD.
        robot_path = self._paths.get("robot1")
        if robot_path is not None:
            friction = round(0.02 + 0.8 * joint.index, 5)
            written = False
            for attribute_name in (
                "physxJoint:jointFriction",
                "physics:jointFriction",
            ):
                if self._set_attribute(robot_path, attribute_name, friction):
                    written = True
            if written:
                applied.append(f"frecare@{robot_path}={friction}")

        return applied

    def _set_attribute(self, prim_path: str, attribute_name: str, value: Any) -> bool:
        try:
            from pxr import Sdf  # type: ignore

            prim = self._stage.GetPrimAtPath(prim_path)
            if not prim.IsValid():
                return False
            attribute = prim.GetAttribute(attribute_name)
            if not attribute or not attribute.IsValid():
                attribute = prim.CreateAttribute(attribute_name, Sdf.ValueTypeNames.Float)
            attribute.Set(value)
            return True
        except Exception as exc:
            log(f"nu am putut scrie {attribute_name} pe {prim_path}: {exc}", level="warn")
            return False


def build_sensor_matrix(
    sensors_with_values: dict[str, float],
    required: Iterable[str],
) -> tuple[dict[str, float], list[str]]:
    """Separă valorile disponibile de cele lipsă.

    Lambda-ul de feature engineering folosește aceeași logică de completare cu zero, astfel
    încât lipsa unui senzor să nu blocheze pipeline-ul. Întoarce (valori, chei lipsă).
    """
    values: dict[str, float] = {}
    missing: list[str] = []
    for name in required:
        if name in sensors_with_values and sensors_with_values[name] is not None:
            values[name] = float(sensors_with_values[name])
        else:
            values[name] = 0.0
            missing.append(name)
    return values, missing


# Semnătura callback-ului folosit de panoul de control pentru a acționa pistonul.
PistonCallback = Callable[[bool], None]
