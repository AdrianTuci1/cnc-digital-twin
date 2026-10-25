"""Ciclul complet al celulei: banda 1 → brațul 1 → CNC → banda 2 → brațul 2 → raft.

Pînă acum brațele atingeau punctele de lucru, iar piesa rămînea pe bandă. Aici falcile se închid
**pe** piesă, o leagă rigid de mînă, o ridică, o cară și o lasă pe suprafața următoare. Fiecare
prindere are un report (`grasp.GraspReport`), iar ciclul se oprește cu eroare dacă falcile nu
ating piesa.

Ordinea este cea a fluxului real: blocul brut vine pe banda 1, brațul 1 îl prinde și îl lasă în
universal; mașina taie (fereastră de timp, ca în `run.py`), iar schimbul de material mută blocul
pe masa de descărcare și piesa mașinată în universal; brațul 1 o ia și o pune pe banda 2; banda o
duce spre vest, brațul 2 o prinde și o lasă în compartimentul familiei. Pozițiile de așezare au
variație deterministică din sămînță: piesa nu ajunge niciodată exact în același loc, nici pe
bandă, nici pe raft.

Verdictul de calitate alege ruta: dacă modelul refuză piesa, brațul 2 coboară peste ea doar cît
să ia cadrul de sortare, apoi se retrage, iar banda 2 o duce mai departe de linia de preluare
pînă cade de la capătul ei în lada de rebut. Niciun braț nu ridică rebutul.

Modulul atinge motorul (ca `scene.py`, `arms.py`, `grasp.py`), deci se validează din venv-ul
geamănului (ADR 0012). Părțile pure — pozițiile nominale, rutele, grosimile — rămîn în `layout`,
`moves` și `part`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

import mujoco
import numpy as np

from . import arms, cell, checks, grasp, inspection, layout, moves, part, scene, wear

#: Ce se cheamă după fiecare pas de simulare: sesiunea de colectare are nevoie de el pentru
#: eșantioane, cadre și uzură, viewer-ul pentru desenare.
StepHook = Callable[[mujoco.MjModel, mujoco.MjData], None]

#: Corpurile celor două stări de material, așa cum le numește scena.
BLANK_BODY = "blank_01"
PART_BODY = "part_01"


@dataclass(frozen=True)
class Routing:
    """Unde merge piesa de pe banda 2: compartimentul familiei ori lada de rebut.

    ``family`` este familia citită de model, nu cea din eticheta sesiunii: în demonstrație
    compartimentul îl alege verdictul, iar eticheta rămîne doar în dataset (ADR 0013).
    """

    destination: str = inspection.ROUTE_RACK
    family: str | None = None
    reason: str = "implicit"

    def as_dict(self) -> dict[str, Any]:
        """Rândul care intră în `session.json`, sub ciclul lui."""
        return {"destination": self.destination, "family": self.family, "reason": self.reason}

    @property
    def rack_family(self) -> str:
        """Familia compartimentului; o rută de raft fără familie nu trece de `_decide`."""
        if self.destination != inspection.ROUTE_RACK or self.family is None:
            raise ValueError(f"ruta {self.destination} nu are compartiment de familie")
        return self.family


#: Ce decide ruta unei piese: primește cadrul de sortare (ori ``None`` dacă nu s-a scris) și
#: întoarce :class:`Routing`. Fără router, ciclul lasă piesa în compartimentul familiei.
Router = Callable[[Path | None], Routing]

#: Cît se ridică punctul de strîngere deasupra centrului piesei cînd vîrfurile falcilor ar intra
#: în suprafața de dedesubt. Vîrful unei falci coboară ~8,5 mm sub centrul feței de prindere; o
#: piesă sub 17 mm grosime lasă falca să atingă banda ori universalul, iar prinderea raportată ar
#: fi de fapt o coliziune. Ridicarea nu schimbă piesa din falci, doar cota la care se închid.
FINGER_TIP_DROP_M = 0.0085
PICK_CLEARANCE_M = 0.003

#: În plus, la universal: fălcile lui țin piesa cu 20 mm deasupra feței (`layout`), deci vîrful
#: falcelor brațului are loc să închidă pe piesă fără să atingă oțelul. Marja rămasă acoperă
#: scufundarea servo-urilor, măsurată în scenă la ~6 mm la întinderea asta.
CHUCK_PICK_CLEARANCE_M = 0.007

#: Cît de aproape trebuie să cadă punctul de strângere de centrul piesei înainte de închidere.
#: Falcile sînt cuplate: dacă una atinge piesa, cealaltă nu mai înaintează. La o piesă îngustă,
#: o abatere de 2 mm lasă o falcă în aer, deci centrarea se măsoară și se corectează în scenă.
CENTER_TOLERANCE_M = 0.0008
CENTER_ATTEMPTS = 2

#: Cît de mult se poate abate piesa de la locul cerut cînd o lasă brațul. Variația este mică, ca
#: piesa să rămînă pe suprafață, dar destul ca fiecare ciclu să arate altfel.
DROP_JITTER_M = 0.02
RACK_JITTER_M = 0.018

#: Toleranța la verificarea finală: piesa trebuie să fie la locul ei, nu doar „pe undeva".
PLACE_TOLERANCE_M = 0.04
#: La raft toleranța este mai mare: compartimentul are 0,5 m, iar scula lasă piesa la eroarea ei
#: de regim plus variația de așezare. 60 mm o țin în compartimentul ei și prind o așezare
#: ratată — înainte de corectură, o piesă ajungea la 72-152 mm ori cădea sub raft.
RACK_TOLERANCE_M = 0.06

#: Cîți pași se lasă piesele să se așeze după ce le lasă brațul, înainte de măsurătoare.
SETTLE_AFTER_RELEASE = 120

#: Cîți pași se lasă piesa refuzată să cadă de la capătul benzii în ladă: 0,8 m de cădere liberă
#: înseamnă ~0,4 s, iar marja acoperă și o săritură pe peretele lăzii.
SETTLE_AFTER_FALL = 600

#: Cît poate intra piesa în podeaua lăzii la impact, măsurat de la fața de sus a tablei. La
#: `solref`-ul lăzii (0,004 s) scufundarea la 4 m/s este de ordinul milimetrilor; pragul de aici
#: prinde o piesă care a trecut prin tablă, nu una care s-a lăsat cîțiva milimetri în ea.
SCRAP_FLOOR_TOLERANCE_M = 0.02


class CycleRecorder(Protocol):
    """Ce are nevoie ciclul de la sesiunea de colectare: faze, momente și cadre.

    `run.Recorder` îl satisface ca atare. Viewer-ul nu are recorder: primește doar `reporter` și
    `hook`, fiindcă acolo nu se scrie niciun dataset.
    """

    parts: tuple[str, ...]

    @property
    def belt_scales(self) -> Mapping[str, float]: ...

    def begin_phase(self, phase: str | None, work: wear.Work) -> None: ...

    def enter(self, stage: str) -> None: ...

    def leave(self) -> None: ...

    def after_stage(self) -> None: ...

    def after_substage(self, point: str, substage: str) -> None: ...

    def sorting_frame(self) -> Path | None: ...

    def __call__(self, model: mujoco.MjModel, data: mujoco.MjData) -> None: ...


@dataclass
class CycleResult:
    """Ce a ieșit dintr-un ciclu: prinderile, mișcările, erorile și locurile finale."""

    grasps: list[dict[str, Any]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    final: dict[str, tuple[float, float, float]] = field(default_factory=dict)

    #: Ruta piesei de pe banda 2, așa cum a ieșit din verdict; ``None`` pînă la pasul de sortare.
    routing: Routing | None = None

    @property
    def success(self) -> bool:
        """Adevărat cînd nicio verificare a ciclului nu a căzut."""
        return not self.failures

    @property
    def grasp_success(self) -> bool:
        """Adevărat cînd toate prinderile au găsit piesa."""
        return bool(self.grasps) and all(bool(row["success"]) for row in self.grasps)

    def as_dict(self) -> dict[str, Any]:
        """Rîndul care intră în `session.json`, sub ciclul lui."""
        return {
            "grasps": list(self.grasps),
            "grasp_success": self.grasp_success,
            "failures": list(self.failures),
            "route": self.routing.as_dict() if self.routing is not None else None,
            "final": {body: [round(value, 6) for value in position]
                      for body, position in self.final.items()},
        }


def pick_lift(half_thickness_m: float) -> float:
    """Cît se ridică punctul de strîngere, ca vîrful falcii să nu intre în suprafață."""
    return max(0.0, FINGER_TIP_DROP_M - float(half_thickness_m) + PICK_CLEARANCE_M)


def _body_position(model: mujoco.MjModel, data: mujoco.MjData,
                   body: str) -> tuple[float, float, float]:
    """Poziția unui corp în lume, ca tuple de floate."""
    return tuple(float(value) for value in data.xpos[grasp.body_id(model, body)])


def _body_quat(model: mujoco.MjModel, data: mujoco.MjData, body: str) -> tuple[float, ...]:
    """Cuaternarul unui corp, ca tuple de floate."""
    return tuple(float(value) for value in data.xquat[grasp.body_id(model, body)])


def varied_xy(seed: int, stream: str, nominal: tuple[float, float],
              bounds: tuple[float, float, float, float], radius_m: float,
              jitter_m: float) -> tuple[float, float]:
    """O poziție de așezare, abătută din sămînță și ținută întreagă pe suprafață.

    Variația este ceea ce face ciclurile utile pentru antrenare: poziția de așezare nu se repetă,
    dar rămîne destul de aproape de țintă ca brațul să o atingă. Verificarea de suprafață este
    aceeași ca la așezarea inițială (`part.on_surface_xy`), deci o abatere prea mare este
    refuzată, nu strecurată.
    """
    rng = random.Random(f"{seed}:grasp:{stream}")
    x = float(nominal[0]) + rng.uniform(-jitter_m, jitter_m)
    y = float(nominal[1]) + rng.uniform(-jitter_m, jitter_m)
    return part.on_surface_xy(x, y, bounds, radius_m, surface="suprafeței de așezare")


class Cycle:
    """Un ciclu de lucru, cu starea lui: brațele, piesele și raportul de pînă acum."""

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, family: str, *,
                 seed: int = 0, settle: int = 300, machining_s: float = 0.0,
                 recorder: CycleRecorder | None = None,
                 hook: StepHook | None = None,
                 reporter: Callable[[dict[str, Any]], None] | None = None,
                 router: Router | None = None) -> None:
        self.model = model
        self.data = data
        self.family = family
        self.seed = int(seed)
        self.settle = int(settle)
        self.machining_s = max(0.0, float(machining_s))
        self.recorder = recorder
        self.hook = hook
        self.reporter = reporter
        self.router = router
        self.result = CycleResult()
        self.tool = {arm: arms.tool_length(model, data, arm) for arm in arms.ARMS}
        self.blank_half = part.blank_half_thickness_m(family)
        self.part_half = part.gear_half_thickness_m(family)
        self.part_radius = part.gear_radius_m(family)
        self.scales = (recorder.belt_scales if recorder is not None else 1.0)

    # ------------------------------------------------------------ faze și cadre
    def _phase(self, phase: str | None, work: wear.Work) -> None:
        if self.recorder is not None:
            self.recorder.begin_phase(phase, work)

    def _enter(self, stage: str) -> None:
        if self.recorder is not None:
            self.recorder.enter(stage)

    def _leave(self) -> None:
        if self.recorder is not None:
            self.recorder.leave()

    def _after_stage(self) -> None:
        if self.recorder is not None:
            self.recorder.after_stage()

    def _after_substage(self, point: str, substage: str) -> None:
        if self.recorder is not None:
            self.recorder.after_substage(point, substage)

    def _step(self) -> None:
        if self.hook is not None:
            self.hook(self.model, self.data)

    def _settle(self, steps: int, *, belts: bool = False) -> None:
        """Pași fără mișcare de braț: piesa se așază, benzile pot rămîne pornite."""
        for _ in range(steps):
            if belts:
                cell.apply_belt_forces(self.model, self.data, self.scales,
                                       (BLANK_BODY, PART_BODY))
            mujoco.mj_step(self.model, self.data)
            self._step()

    # ------------------------------------------------------------ mișcări
    def _reach(self, arm: str, item: moves.Move, stage: str,
               point: tuple[float, float, float]) -> dict[str, float]:
        """Duce scula în punctul dat și raportează eroarea, contactele și drumul."""
        report = arms.reach_point(self.model, self.data, arm, point, item.approach,
                                  item.spin_deg, settle=self.settle, hook=self.hook)
        pairs = scene.contact_pairs(self.model, self.data)
        prefixes = tuple(f"{prefix}_" for prefix, _ in scene.ARM_MOUNTS)
        links = checks.arm_link_contacts(pairs, prefixes)
        fingers = checks.arm_foreign_contacts(pairs, prefixes)
        row: dict[str, Any] = {
            "arm": arm,
            "point": item.name,
            "label": item.label,
            "stage": stage,
            "target": [round(float(value), 6) for value in point],
            "position_m": round(float(report["position_m"]), 6),
            "angle_deg": round(float(report["angle_deg"]), 4),
            "path_residual_m": round(float(report["path_residual_m"]), 6),
            "steps": int(report["steps"]),
            "link_contacts": list(links),
            "finger_contacts": list(fingers),
        }
        self.result.stages.append(row)
        self._after_substage(item.name, stage)
        if stage == "jos":
            self._after_stage()
        if self.reporter is not None:
            self.reporter(row)
        if report["position_m"] > checks.MAX_POSE_ERROR_M:
            self._fail(f"scula rămîne {report['position_m'] * 1000:.0f} mm de "
                       f"{item.name} ({stage}), peste pragul de "
                       f"{checks.MAX_POSE_ERROR_M * 1000:.0f} mm")
        elif report["angle_deg"] > checks.MAX_POSE_ANGLE_DEG:
            self._fail(f"scula rămîne cu {report['angle_deg']:.1f}° răsuciți la {item.name} "
                       f"({stage}), peste pragul de {checks.MAX_POSE_ANGLE_DEG:.0f}°")
        for pair in links:
            self._fail(f"o verigă a brațului atinge ceva la {item.name} {stage}: {pair}")
        return report

    def _travel(self, arm: str, item: moves.Move) -> None:
        """Trecerea prin fața obstacolului, urcarea deasupra și coborîrea pe punct."""
        for index, point in enumerate(item.via, start=1):
            self._reach(arm, item, f"trecere {index}", point)
        self._reach(arm, item, "sus", item.hover)
        self._reach(arm, item, "jos", item.point)

    def _lift(self, arm: str, item: moves.Move) -> None:
        self._reach(arm, item, "ridicare", item.hover)

    # ------------------------------------------------------------ prinderea
    def _grip(self, action: str, arm: str, body: str, point: str,
              steps: int | None = None, hold: bool = False) -> grasp.GraspReport:
        """Închide ori deschide falcile, cu raportul scris în ciclu."""
        function = grasp.pick if action == "pick" else grasp.place
        kwargs: dict[str, Any] = {"hook": self.hook, "point": point}
        if steps is not None:
            kwargs["steps"] = steps
        if action == "place":
            kwargs["hold"] = hold
        report = function(self.model, self.data, arm, body, **kwargs)
        self.result.grasps.append(report.as_dict())
        if self.reporter is not None:
            self.reporter({"grasp": report.as_dict()})
        if not report.success:
            self._fail(f"prinderea {body} la {point} a ratat: contact stînga/dreapta "
                       f"{report.left_contacts}/{report.right_contacts}, deschidere "
                       f"{report.finger_gap_m * 1000:.0f} mm")
        return report

    def _approach(self, arm: str, name: str, body: str,
                  half_thickness_m: float, clearance_m: float = 0.0,
                  align: bool = False) -> moves.Move:
        """Coboară scula pe piesa din locul unde se află acum, fără să o prindă.

        Cu ``align=True``, scula se rotește în jurul verticalei pînă cînd falcile închid pe
        direcția cea mai îngustă a piesei (`part.narrow_axes_deg`). Altfel yaw-ul piesei este
        aleator, iar falcile apucă o coardă oarecare: prinderea reușește, dar piesa poate fi
        împinsă ori rotită, iar cadrul de pe flanșă arată altă față la fiecare ciclu.

        Apropierea se face și cînd piesa nu este ridicată: cadrul de sortare se ia de pe flanșă
        cînd scula stă pe punctul de jos, iar abia verdictul decide dacă falcile se închid.
        """
        item = moves.move(name, self.tool[arm], self.family)
        x, y, _ = _body_position(self.model, self.data, body)
        z = (moves.SURFACE_Z[name] + half_thickness_m + pick_lift(half_thickness_m)
             + clearance_m)
        item = replace(item, point=(x, y, z))
        if align:
            item = self._aligned(item, body)
        self._enter(name)
        self._reach(arm, item, "sus", item.hover)
        self._reach(arm, item, "jos", item.point)
        return self._centered(arm, item, body)

    def _grasp_and_lift(self, arm: str, name: str, body: str,
                        item: moves.Move) -> grasp.GraspReport:
        """Închide falcile pe piesă, o ridică și iese din punctul de lucru."""
        report = self._grip("pick", arm, body, name)
        self._lift(arm, item)
        self._leave()
        if report.success and not grasp.is_attached(self.model, self.data, arm, body):
            self._fail(f"piesa {body} nu a rămas legată de {arm} după prindere")
        return report

    def _pick_from(self, arm: str, name: str, body: str,
                   half_thickness_m: float, clearance_m: float = 0.0,
                   align: bool = False) -> grasp.GraspReport:
        """Prinde piesa de pe suprafața ei, în locul unde se află acum."""
        item = self._approach(arm, name, body, half_thickness_m, clearance_m, align=align)
        return self._grasp_and_lift(arm, name, body, item)

    def _withdraw(self, arm: str, item: moves.Move) -> None:
        """Scoate scula din punctul de jos fără să închidă falcile pe piesă."""
        self._reach(arm, item, "retragere", item.hover)
        self._leave()

    # ------------------------------------------------------------ ruta
    def _decide(self) -> Routing:
        """Ruta piesei de pe banda 2, din cadrul de sortare.

        Fără router, piesa merge în compartimentul familiei. Cu router, verdictul modelului
        hotărăște; un apel căzut nu inventează un verdict, ci lasă piesa pe ruta de siguranță a
        rebutului (ADR 0009), iar ciclul raportează eroarea.
        """
        if self.router is None:
            routing = Routing(inspection.ROUTE_RACK, self.family)
        else:
            frame = self.recorder.sorting_frame() if self.recorder is not None else None
            try:
                routing = self.router(frame)
            except Exception as error:  # plasa de siguranță a celulei, nu o eroare ascunsă
                self._fail(f"ruta nu a putut fi decisă, piesa merge la rebut: {error}")
                routing = Routing(inspection.ROUTE_SCRAP, None, "fallback")
        if routing.destination == inspection.ROUTE_SCRAP:
            routing = Routing(inspection.ROUTE_SCRAP, None, routing.reason)
        elif routing.family is None:
            # O rută de raft fără familie nu are unde așeza piesa; eticheta sesiunii nu este o
            # intrare a deciziei (ADR 0013), deci piesa merge la rebut.
            self._fail("ruta cere un compartiment, dar verdictul nu a dat o familie; "
                       "piesa merge la rebut")
            routing = Routing(inspection.ROUTE_SCRAP, None, "family_missing")
        self.result.routing = routing
        if self.reporter is not None:
            self.reporter({"route": {"destination": routing.destination,
                                     "family": routing.family, "reason": routing.reason}})
        return routing

    def _send_to_scrap(self) -> None:
        """Banda 2 duce piesa refuzată pînă la capăt; brațul 2 nu o ridică.

        Piesa stă la linia de preluare, ținută de clemă cît scula coboară peste ea. Clema se
        scoate, banda își ține forța și după linie (`cell.carry_past_pickup`), iar piesa cade de
        la capătul de vest al benzii în ladă.
        """
        self._phase(wear.PHASE_TRANSPORT, wear.Work(belts=wear.BELT_COMPONENTS))
        grasp.unclamp(self.model, self.data, PART_BODY)
        left = cell.carry_past_pickup(self.model, self.data, PART_BODY, hook=self.hook,
                                      speed_scale=self.scales)
        if not left:
            self._fail("piesa refuzată a rămas pe bandă, fără să ajungă la capătul ei")
        self._settle(SETTLE_AFTER_FALL)

    def _aligned(self, item: moves.Move, body: str) -> moves.Move:
        """Rotește scula ca falcile să închidă pe axa îngustă a piesei.

        Se lucrează doar la apropieri verticale: acolo yaw-ul piesei și unghiul falcilor sînt
        amîndouă în planul benzii. Pentru fiecare axă îngustă a formei se calculează unghiul din
        lume cu yaw-ul citit din scenă, iar dintre candidați se alege cel mai apropiat de
        orientarea de acum — rotirea cea mai mică, ca IK-ul să rămînă aproape de poziția
        nominală.
        """
        axes = part.narrow_axes_deg(self.family)
        if not axes or abs(item.approach[0]) + abs(item.approach[1]) > 1e-9:
            return item
        yaw = part.yaw_from_quat(_body_quat(self.model, self.data, body))
        current = arms.jaw_yaw_deg(item.approach, item.spin_deg)

        def delta(angle: float) -> float:
            return (angle - current + 90.0) % 180.0 - 90.0

        desired = min(((axis + yaw) % 180.0 for axis in axes), key=lambda angle: abs(delta(angle)))
        # Un spin pozitiv rotește falcile în sens invers în plan; diferența se scade, ca să
        # ajungem la unghiul dorit fără să ghicim semnul convenției.
        spin = item.spin_deg - delta(desired)
        return replace(item, spin_deg=spin)

    def _centered(self, arm: str, item: moves.Move, body: str) -> moves.Move:
        """Mută scula peste centrul piesei, măsurat în scenă, înainte de închidere.

        Poziția cerută are eroarea servo-urilor: la universal, scula rămîne pînă la 9 mm sub
        țintă. Falcile sînt cuplate, deci la o piesă de 28 mm o abatere de 1,5 mm lasă o falcă în
        aer, iar legătura rigidă ar ascunde prinderea pe un singur vîrf. Corecția se face doar în
        plan; cota rămîne cea calculată cu marja de coborîre, ca vîrful falcii să nu intre în
        suport. Se repetă de două ori, fiindcă și a doua așezare are eroarea ei.
        """
        for _ in range(CENTER_ATTEMPTS):
            part_xy = np.asarray(_body_position(self.model, self.data, body)[:2], dtype=float)
            tool_xy = np.asarray(arms.tool_point(self.model, self.data, arm)[:2], dtype=float)
            offset = part_xy - tool_xy
            if float(np.linalg.norm(offset)) <= CENTER_TOLERANCE_M:
                break
            point = (item.point[0] + float(offset[0]),
                     item.point[1] + float(offset[1]), item.point[2])
            item = replace(item, point=point)
            self._reach(arm, item, "centrare", item.point)
        return item

    def _place_at(self, arm: str, name: str, body: str,
                  point: tuple[float, float, float] | None = None,
                  hold: bool = False, family: str | None = None) -> grasp.GraspReport:
        """Lasă piesa pe suprafața punctului de lucru, cu variația cerută.

        ``family`` este familia citită de model, cînd ruta vine din verdict: cota de așezare și
        trecerea din fața dulapului depind de ea.
        """
        item = moves.move(name, self.tool[arm], family or self.family)
        if point is not None:
            item = replace(item, point=point)
        self._enter(name)
        self._travel(arm, item)
        report = self._grip("place", arm, body, name, hold=hold)
        self._lift(arm, item)
        self._leave()
        return report

    # ------------------------------------------------------------ fluxul
    def run(self) -> CycleResult:
        """Ciclul întreg, în ordinea din plan; întoarce raportul și timpul simulat."""
        started = float(self.data.time)

        # 1. Banda 1 aduce blocul brut la linia de preluare. Piesa este ținută de clemă cît
        #    falcile se închid: altfel vîrfurile unui disc liber îl împing cîțiva centimetri.
        self._phase(wear.PHASE_TRANSPORT, wear.Work(belts=wear.BELT_COMPONENTS))
        cell.bring_parts_to_pickup(self.model, self.data, (BLANK_BODY,),
                                   hook=self.hook, speed_scale=self.scales)
        self._settle(SETTLE_AFTER_RELEASE)
        grasp.clamp(self.model, self.data, BLANK_BODY)

        # 2. Brațul 1 prinde blocul și îl duce în universal.
        self._phase(None, wear.Work(belts=wear.BELT_COMPONENTS, arms=("arm1",)))
        self._pick_from("arm1", "belt1_pick", BLANK_BODY, self.blank_half)
        blank_chuck = moves.work_point("cnc_chuck", self.family)
        self._place_at("arm1", "cnc_chuck", BLANK_BODY, blank_chuck, hold=True)
        self._settle(SETTLE_AFTER_RELEASE)
        grasp.clamp(self.model, self.data, BLANK_BODY)

        # 3. Fereastra de așchiere: mașina nu are axe în scenă, dar scula se uzează aici.
        self._machining()

        # 4. Schimbul de material: blocul prelucrat pleacă pe masa de descărcare, iar piesa
        #    mașinată apare în universal. Este o tranziție de proces, nu o mișcare de braț.
        self._swap_material()

        # 5. Brațul 1 descarcă piesa din universal și o lasă pe banda 2.
        self._phase(None, wear.Work(belts=wear.BELT_COMPONENTS, arms=("arm1",)))
        self._pick_from("arm1", "cnc_chuck", PART_BODY, self.part_half,
                        clearance_m=CHUCK_PICK_CLEARANCE_M, align=True)
        drop = self._drop_point()
        self._place_at("arm1", "belt2_drop", PART_BODY, drop)
        self._settle(SETTLE_AFTER_RELEASE, belts=True)

        # 6. Banda 2 duce piesa spre vest, pînă la linia de preluare a brațului 2.
        self._phase(wear.PHASE_TRANSPORT, wear.Work(belts=wear.BELT_COMPONENTS))
        cell.bring_parts_to_pickup(self.model, self.data, (PART_BODY,),
                                   hook=self.hook, speed_scale=self.scales)
        self._settle(SETTLE_AFTER_RELEASE)
        grasp.clamp(self.model, self.data, PART_BODY)

        # 7. Brațul 2 coboară peste piesă, iar camera de pe flanșă ia cadrul care decide ruta.
        #    Verdictul bun duce piesa în compartimentul familiei; verdictul de rebut o lasă pe
        #    bandă, care o duce peste linia de preluare pînă cade în ladă.
        self._phase(None, wear.Work(belts=wear.BELT_COMPONENTS, arms=("arm2",)))
        item = self._approach("arm2", "belt2_pick", PART_BODY, self.part_half, align=True)
        routing = self._decide()
        if routing.destination == inspection.ROUTE_SCRAP:
            self._withdraw("arm2", item)
            self._send_to_scrap()
        else:
            self._grasp_and_lift("arm2", "belt2_pick", PART_BODY, item)
            family = routing.rack_family
            rack = layout.rack_target(family)
            self._place_at("arm2", rack, PART_BODY, self._rack_point(rack, family),
                           hold=True, family=family)
            self._settle(SETTLE_AFTER_RELEASE)
            grasp.clamp(self.model, self.data, PART_BODY)

        self._phase(None, wear.Work())
        self._check_final(routing)
        self.result.duration_s = float(self.data.time) - started
        self.result.final = {
            body: _body_position(self.model, self.data, body)
            for body in (BLANK_BODY, PART_BODY)
        }
        return self.result

    # ------------------------------------------------------------ proces
    def _machining(self) -> None:
        """Fereastra `CUTTING`: ceasul și uzura merg, geometria nu se schimbă."""
        self._phase(wear.PHASE_CUTTING, wear.Work(cutting=True))
        if self.machining_s <= 0.0:
            return
        steps = max(1, int(round(self.machining_s / float(self.model.opt.timestep))))
        for _ in range(steps):
            cell.apply_belt_forces(self.model, self.data, self.scales,
                                   (BLANK_BODY, PART_BODY))
            mujoco.mj_step(self.model, self.data)
            self._step()

    def _swap_material(self) -> None:
        """Blocul brut pe masa de descărcare, piesa mașinată în universal.

        Cele două corpuri există din construcția scenei; prelucrarea este trecerea dintre ele.
        Ordinea contează: blocul pleacă întîi, ca piesa mașinată să nu apară peste el. Amîndouă
        sînt ținute apoi de clemă, ca să nu alunece cît brațul se apropie.
        """
        blank_q = _body_quat(self.model, self.data, BLANK_BODY)
        part_q = _body_quat(self.model, self.data, PART_BODY)
        grasp.unclamp(self.model, self.data, BLANK_BODY)
        grasp.set_body_pose(
            self.model, self.data, BLANK_BODY,
            (layout.MACHINE_STAGING_X, layout.MACHINE_STAGING_Y,
             layout.MACHINE_TABLE_Z + self.blank_half),
            blank_q)
        grasp.clamp(self.model, self.data, BLANK_BODY)

        target = layout.target("cnc_chuck")
        grasp.set_body_pose(
            self.model, self.data, PART_BODY,
            (float(target["x"]), float(target["y"]),
             moves.SURFACE_Z["cnc_chuck"] + self.part_half),
            part_q)
        grasp.clamp(self.model, self.data, PART_BODY)

    def _drop_point(self) -> tuple[float, float, float]:
        """Locul de așezare pe banda 2, abătut din sămînță și ținut pe bandă."""
        nominal = moves.work_point("belt2_drop", self.family)
        x, y = varied_xy(self.seed, "belt2_drop", (nominal[0], nominal[1]),
                         layout.belt_bounds(layout.BELT_OUT), self.part_radius,
                         DROP_JITTER_M)
        return (x, y, nominal[2])

    def _rack_point(self, name: str, family: str | None = None
                    ) -> tuple[float, float, float]:
        """Locul din compartiment, cu o abatere mică pe lungimea raftului.

        Abaterea pe adîncimea dulapului este zero: fața vestică este deschisă, iar piesa stă
        aproape de ea ca scula să intre fără ca antebrațul să atingă carcasa.
        """
        nominal = moves.work_point(name, family or self.family)
        rng = random.Random(f"{self.seed}:grasp:{name}")
        y = float(nominal[1]) + rng.uniform(-RACK_JITTER_M, RACK_JITTER_M)
        slot = layout.rack_slot_index(name) + 1
        entry = layout.rack_entry(slot)
        y0 = float(entry["y"])
        y1 = y0 + layout.RACK_ENTRY_M
        limit = self.part_radius + part.BLANK_EDGE_MARGIN_M
        y = min(max(y, y0 + limit), y1 - limit)
        return (float(nominal[0]), y, float(nominal[2]))

    # ------------------------------------------------------------ verificarea
    def _check_final(self, routing: Routing) -> None:
        """Piesa mașinată e în compartimentul familiei ori în ladă, iar blocul pe masă."""
        if routing.destination == inspection.ROUTE_SCRAP:
            self._check_scrap()
        else:
            family = routing.rack_family
            rack = layout.rack_target(family)
            expected = moves.work_point(rack, family)
            actual = _body_position(self.model, self.data, PART_BODY)
            distance = math.dist((actual[0], actual[1]), (expected[0], expected[1]))
            if distance > RACK_TOLERANCE_M:
                self._fail(f"piesa mașinată a rămas la {distance * 1000:.0f} mm de "
                           f"compartimentul {rack}")
            if actual[2] < expected[2] - 0.03:
                self._fail(f"piesa mașinată a căzut sub raft ({actual[2]:.3f} m)")

        blank = _body_position(self.model, self.data, BLANK_BODY)
        staging = (layout.MACHINE_STAGING_X, layout.MACHINE_STAGING_Y)
        if math.dist((blank[0], blank[1]), staging) > PLACE_TOLERANCE_M:
            self._fail(f"blocul brut nu a rămas pe masa de descărcare "
                       f"({blank[0]:.3f}, {blank[1]:.3f})")
        if blank[2] < layout.MACHINE_TABLE_Z:
            self._fail(f"blocul brut a căzut de pe masa mașinii ({blank[2]:.3f} m)")

    def _check_scrap(self) -> None:
        """Piesa refuzată a ajuns în ladă, între podeaua și marginea ei.

        Nu se cere un punct anume: piesa cade, se rostogolește și se poate sprijini de un perete.
        Se cere să fie în amprenta lăzii și la cota dintre podea și margine, adică să nu fi rămas
        pe bandă, pe jos, ori să fi trecut prin tablă. Toleranța de la fund acoperă scufundarea
        contactului la impact.
        """
        actual = _body_position(self.model, self.data, PART_BODY)
        geometry = layout.scrap_bin_geometry()
        inside = (
            geometry["scrap_bin_cx"] - geometry["scrap_bin_hx"] <= actual[0]
            <= geometry["scrap_bin_cx"] + geometry["scrap_bin_hx"]
            and geometry["scrap_bin_cy"] - geometry["scrap_bin_hy"] <= actual[1]
            <= geometry["scrap_bin_cy"] + geometry["scrap_bin_hy"])
        if not inside:
            self._fail(f"piesa refuzată a căzut în afara lăzii "
                       f"({actual[0]:.3f}, {actual[1]:.3f})")
        floor_top = geometry["scrap_bin_floor_z"] + geometry["scrap_bin_floor_hz"]
        if actual[2] < floor_top - SCRAP_FLOOR_TOLERANCE_M:
            self._fail(f"piesa refuzată a trecut prin podeaua lăzii ({actual[2]:.3f} m)")
        if actual[2] > layout.SCRAP_BIN_RIM_Z:
            self._fail(f"piesa refuzată a rămas peste marginea lăzii ({actual[2]:.3f} m)")

    def _fail(self, message: str) -> None:
        if message not in self.result.failures:
            self.result.failures.append(message)


def run_cycle(model: mujoco.MjModel, data: mujoco.MjData, family: str, *,
              seed: int = 0, settle: int = 300, machining_s: float = 0.0,
              recorder: CycleRecorder | None = None,
              hook: StepHook | None = None,
              reporter: Callable[[dict[str, Any]], None] | None = None,
              router: Router | None = None) -> CycleResult:
    """Rulează un ciclu complet, cu prindere fizică; vezi :class:`Cycle`."""
    return Cycle(model, data, family, seed=seed, settle=settle, machining_s=machining_s,
                 recorder=recorder, hook=hook, reporter=reporter, router=router).run()
