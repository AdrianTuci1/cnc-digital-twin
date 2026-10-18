"""Brațele în mișcare: cinematică inversă, urmărirea punctelor de lucru, prinderea piesei.

MuJoCo nu are rezolvitor de cinematică inversă pentru un lanț oarecare, deci unul se scrie aici:
Jacobinul corpului sculei (`mj_jacBody`) și metoda damped least squares, care se poartă rezonabil și
aproape de singularități. Rezolvitorul lucrează pe o copie a stării — mută `qpos`, măsoară, apoi pune
`qpos` la loc — și întoarce **ținte de articulație**, nu o poziție scrisă direct: brațul ajunge acolo
prin servo-urile lui, iar fizica rămâne întreagă.

Scula se măsoară prin **punctul de strângere**: mijlocul fețelor falcilor, adică locul unde ajunge
piesa prinsă. Eliberat, el stă la ~0,1 m în fața flanșei, deci ținta cinematicii inverse este flanșa
mutată înapoi cu atât.

Prinderea nu se face prin frecarea falcilor, ci printr-o legătură rigidă activată la prindere, ca în
celula reală: acolo griperul nu are falci comandabile din script, iar piesa se leagă de el printr-o
articulație fixă (`simulation/extensions/cncdt.cnc_cell/cncdt/cnc_cell/grasping.py`). Modulul importă
`mujoco`, ca `scene.py` și `cell.py`; restul pachetului rămâne pur (ADR 0012).
"""

from __future__ import annotations

import math
from typing import Callable

import mujoco
import numpy as np

#: Ce se cheamă după fiecare pas de simulare, când cineva vrea să se uite la braț în timpul
#: mișcării. Viewer-ul din `view.py` își desenează cadrul de aici, fără ca mișcarea să știe de el.
StepHook = Callable[[mujoco.MjModel, mujoco.MjData], None]

#: Brațele scenei, în ordinea în care apar la compunere.
ARMS = ("arm1", "arm2")

#: Câte articulații are un braț, fără falci.
ARM_JOINTS = 7

#: Actuatoarele unui braț: șapte pe articulații, al optulea pe tendonul falcilor.
GRIPPER_ACTUATOR_INDEX = 7

#: Comanda falcilor: 0 închide, 255 deschide la maximum (`panda.xml` remapează 0–0,04 m pe 0–255).
GRIPPER_OPEN = 255.0
GRIPPER_CLOSED = 0.0

#: Cât de repede își schimbă articulațiile ținta, în radiani pe secundă. Mișcarea se face prin
#: interpolarea țintelor, nu printr-un profil de traiectorie: servo-urile brațului fac oricum
#: tranziția lină, iar viteza de aici este cea care ține scula pe un drum previzibil.
JOINT_SPEED_RAD_S = 1.0

#: Câți pași de simulare se așteaptă după ce ținta a ajuns, ca brațul să se așeze pe ea.
SETTLE_STEPS = 400

#: Cinematica inversă: cât de mult se retrage rezolvitorul din singularități, ce fracțiune din
#: eroare intră într-un pas, câți pași face și cu cât se ține de marginile cursei articulațiilor.
IK_DAMPING = 0.02
IK_STEP = 0.6
IK_ITERATIONS = 120
IK_JOINT_MARGIN = 0.02

#: Rotirile sculei în jurul axei de apropiere, în grade, încercate de rezolvitor. Piesa prinsă este
#: rotundă, deci rotirea din jurul axei nu schimbă nimic la prindere, iar brațul o alege pe cea pe
#: care ajunge.
SPIN_CANDIDATES_DEG = (0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0)

#: Drumul sculei se împarte în pași de cel mult atâția metri, iar punctul de strângere se mișcă cu
#: viteza de mai jos. Servo-urile au nevoie de câțiva timpi de stabilizare pe fiecare pas, deci
#: amândouă țin scula pe linia dreaptă: la pași mai rari, brațul taie colțurile, iar la viteză mai
#: mare rămâne în urmă și drumul nu mai este cel cerut.
PATH_STEP_M = 0.01
TOOL_SPEED_MPS = 0.15

#: Cât de bine trebuie să nimerească rezolvitorul: poziția punctului de strângere, în metri, și
#: orientarea sculei, în grade. Sunt pragurile verificării mișcării din `view.py --demo`.
IK_POSITION_TOLERANCE_M = 0.002
IK_ANGLE_TOLERANCE_DEG = 1.0


def _body_id(model: mujoco.MjModel, name: str) -> int:
    """Identificatorul unui corp, cu eroare care spune ce nume există."""
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    if identifier < 0:
        raise ValueError(f"corp inexistent în scenă: {name}")
    return int(identifier)


def hand_body(arm: str) -> str:
    """Numele corpului sculei: flanșa cu falcile, în scenă."""
    return f"{arm}_hand"


def finger_bodies(arm: str) -> tuple[str, str]:
    """Cele două falci, în ordinea stânga–dreapta din model."""
    return (f"{arm}_left_finger", f"{arm}_right_finger")


def _pad_center(model: mujoco.MjModel, data: mujoco.MjData, finger: str) -> np.ndarray:
    """Centrul feței de prindere a unei falci, în sistemul lumii.

    Fața de prindere este cea mai mare geomă de tip cutie a falcii: modelul are mai multe cutii mici
    pe vârf, iar cea care atinge piesa este cea mare.
    """
    identifier = _body_id(model, finger)
    best: tuple[float, np.ndarray] | None = None
    for index in range(model.body_geomadr[identifier],
                       model.body_geomadr[identifier] + model.body_geomnum[identifier]):
        if model.geom_type[index] != mujoco.mjtGeom.mjGEOM_BOX:
            continue
        size = np.array(model.geom_size[index], dtype=float)
        volume = float(np.prod(size))
        if best is None or volume > best[0]:
            best = (volume, np.array(model.geom_pos[index], dtype=float))
    if best is None:
        raise ValueError(f"falca {finger} nu are nicio geomă de contact")
    rotation = np.array(data.xmat[identifier], dtype=float).reshape(3, 3)
    return np.array(data.xpos[identifier], dtype=float) + rotation @ best[1]


def pinch_local(model: mujoco.MjModel, data: mujoco.MjData, arm: str) -> np.ndarray:
    """Punctul de strângere în sistemul sculei: mijlocul fețelor celor două falci.

    Falcile se închid simetric, deci mijlocul cade pe axa sculei, la distanța până la care ajunge
    piesa prinsă. Se măsoară din starea de acum, nu se presupune din geometria scrisă în model.
    """
    centres = [_pad_center(model, data, finger) for finger in finger_bodies(arm)]
    middle = (centres[0] + centres[1]) / 2
    identifier = _body_id(model, hand_body(arm))
    rotation = np.array(data.xmat[identifier], dtype=float).reshape(3, 3)
    return rotation.T @ (middle - np.array(data.xpos[identifier], dtype=float))


def tool_length(model: mujoco.MjModel, data: mujoco.MjData, arm: str) -> float:
    """Cât atârnă scula de flanșă până la punctul de strângere, în metri."""
    return float(np.linalg.norm(pinch_local(model, data, arm)))


def tool_quat(approach: tuple[float, float, float]) -> np.ndarray:
    """Orientarea sculei care apropie falcile din direcția dată.

    Direcția este axa ``Z`` a sculei (de-a lungul falcilor), iar falcile se închid pe axa ``Y``. A
    treia axă se ia din versorii lumii, cu preferință pentru cea mai apropiată de verticală și
    perpendiculară pe direcția de apropiere. Rotația rămasă în jurul direcției de apropiere nu
    schimbă nimic la o piesă rotundă, deci convenția e suficientă și se vede în scenă.
    """
    z_axis = np.asarray(approach, dtype=float)
    norm = float(np.linalg.norm(z_axis))
    if norm == 0.0:
        raise ValueError("direcția de apropiere nu poate fi nulă")
    z_axis = z_axis / norm
    for candidate in ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        x_axis = np.asarray(candidate, dtype=float)
        x_axis = x_axis - z_axis * float(x_axis @ z_axis)
        if float(np.linalg.norm(x_axis)) > 1e-6:
            x_axis = x_axis / float(np.linalg.norm(x_axis))
            break
    else:
        raise ValueError("nu s-a găsit o axă perpendiculară pe direcția de apropiere")
    rotation = np.column_stack((x_axis, np.cross(z_axis, x_axis), z_axis))
    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, rotation.ravel())
    return quat


def rotation_matrix(quat: np.ndarray) -> np.ndarray:
    """Matricea de rotație a unui cuaternar ``(w, x, y, z)``."""
    flat = np.zeros(9)
    mujoco.mju_quat2Mat(flat, np.asarray(quat, dtype=float))
    return flat.reshape(3, 3)


def rotation_vector(rotation: np.ndarray) -> np.ndarray:
    """Vectorul de rotație al unei matrice de rotație: axa, înmulțită cu unghiul.

    Unghiul de 180° nu are axă unică în formula obișnuită, deci se tratează separat: acolo axa se
    citește din diagonala matricei, semnul luându-se din elementele din afara diagonalei.
    """
    cosine = min(max((float(np.trace(rotation)) - 1) / 2, -1.0), 1.0)
    angle = math.acos(cosine)
    if angle < 1e-9:
        return np.zeros(3)
    if angle > math.pi - 1e-3:
        axis = np.sqrt(np.maximum((np.diag(rotation) + 1) / 2, 0.0))
        largest = int(np.argmax(axis))
        if axis[largest] > 0.0:
            other = (largest + 1) % 3
            axis[other] = rotation[largest, other] / (2 * axis[largest])
        return axis * angle
    axis = np.array([rotation[2, 1] - rotation[1, 2],
                     rotation[0, 2] - rotation[2, 0],
                     rotation[1, 0] - rotation[0, 1]]) / (2 * math.sin(angle))
    return axis * angle


def hand_target(model: mujoco.MjModel, data: mujoco.MjData, arm: str,
                point: tuple[float, float, float],
                approach: tuple[float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Poziția și orientarea flanșei care aduc punctul de strângere în ``point``.

    Flanșa stă înapoi cu punctul de strângere, măsurat în sistemul sculei: cine urmărește un punct
    din plan urmărește de fapt flanșa asta.
    """
    quat = tool_quat(approach)
    world_offset = rotation_matrix(quat) @ pinch_local(model, data, arm)
    return np.asarray(point, dtype=float) - world_offset, quat


def joint_names(arm: str) -> tuple[str, ...]:
    """Numele celor șapte articulații ale unui braț."""
    return tuple(f"{arm}_joint{index + 1}" for index in range(ARM_JOINTS))


def actuator_ids(model: mujoco.MjModel, arm: str) -> np.ndarray:
    """Identificatorii actuatoarelor unui braț: cele șapte de articulație, plus griperul."""
    identifiers = []
    for index in range(ARM_JOINTS + 1):
        name = f"{arm}_actuator{index + 1}"
        identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
        if identifier < 0:
            raise ValueError(f"actuator inexistent în scenă: {name}")
        identifiers.append(int(identifier))
    return np.array(identifiers)


def joint_qpos_addresses(model: mujoco.MjModel, arm: str) -> np.ndarray:
    """Adresele din `qpos` ale celor șapte articulații, în ordinea lor."""
    addresses = []
    for name in joint_names(arm):
        joint = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint < 0:
            raise ValueError(f"articulație inexistentă în scenă: {name}")
        addresses.append(int(model.jnt_qposadr[joint]))
    return np.array(addresses)


def joint_controls(model: mujoco.MjModel, data: mujoco.MjData, arm: str) -> np.ndarray:
    """Țintele actuatoarelor de articulație ale unui braț, citite din starea de acum."""
    return np.array([float(data.ctrl[index]) for index in actuator_ids(model, arm)[:ARM_JOINTS]])


def set_joint_controls(model: mujoco.MjModel, data: mujoco.MjData, arm: str,
                       target: np.ndarray) -> None:
    """Scrie țintele actuatoarelor de articulație ale unui braț."""
    data.ctrl[actuator_ids(model, arm)[:ARM_JOINTS]] = target


def hand_pose(model: mujoco.MjModel, data: mujoco.MjData,
              arm: str) -> tuple[np.ndarray, np.ndarray]:
    """Poziția și orientarea flanșei, în sistemul lumii, citite din scenă."""
    identifier = _body_id(model, hand_body(arm))
    position = np.array(data.xpos[identifier], dtype=float)
    # `data.xquat` este deja în ordinea cerută de `mju_quat2Mat`, (w, x, y, z): reordonarea lui
    # dădea o altă rotație, iar scula urmărea un punct care nu era cel de strângere.
    quat = np.array(data.xquat[identifier], dtype=float)
    return position, quat


def tool_point(model: mujoco.MjModel, data: mujoco.MjData, arm: str) -> np.ndarray:
    """Unde se află punctul de strângere acum, în sistemul lumii."""
    position, quat = hand_pose(model, data, arm)
    return position + rotation_matrix(quat) @ pinch_local(model, data, arm)


def error_report(model: mujoco.MjModel, data: mujoco.MjData, arm: str,
                 point: tuple[float, float, float], approach: tuple[float, float, float],
                 quat: np.ndarray | None = None) -> dict[str, float]:
    """Cât de departe este scula de ținta ei: poziția în metri, orientarea în grade.

    ``quat`` este orientarea față de care se măsoară unghiul; lipsind, se ia cea canonică a
    direcției de apropiere. Mișcarea care a ales o rotire a sculei trebuie să dea rotirea aceea,
    altfel raportul ar arăta o eroare de unghi care în scenă nu există.
    """
    target_position, target_quat = hand_target(model, data, arm, point, approach)
    if quat is not None:
        target_quat = np.asarray(quat, dtype=float)
    position, _ = hand_pose(model, data, arm)
    current = np.array(data.xmat[_body_id(model, hand_body(arm))], dtype=float).reshape(3, 3)
    error = rotation_matrix(target_quat) @ current.T
    return {
        "position_m": float(np.linalg.norm(target_position - position)),
        "angle_deg": math.degrees(float(np.linalg.norm(rotation_vector(error)))),
    }


def spin_quat(quat: np.ndarray, approach: tuple[float, float, float], degrees: float) -> np.ndarray:
    """Aceeași orientare, rotită cu unghiul dat în jurul axei de apropiere."""
    axis = np.asarray(approach, dtype=float)
    axis = axis / float(np.linalg.norm(axis))
    spin = np.zeros(4)
    mujoco.mju_axisAngle2Quat(spin, axis, math.radians(degrees))
    result = np.zeros(4)
    mujoco.mju_mulQuat(result, spin, np.asarray(quat, dtype=float))
    return result


def jaw_yaw_deg(approach: tuple[float, float, float], spin_deg: float = 0.0) -> float:
    """Direcția în plan a axei pe care se închid falcile, în grade, modulo 180.

    Falcile se închid pe axa ``Y`` a sculei (`tool_quat`), iar ``spin_deg`` o rotește în jurul
    direcției de apropiere. Pentru o apropiere verticală, unghiul este cel din planul benzii și
    se compară direct cu yaw-ul piesei; pentru o apropiere înclinată, este proiecția lui.
    """
    quat = tool_quat(approach)
    if spin_deg:
        quat = spin_quat(quat, approach, spin_deg)
    jaw = rotation_matrix(quat)[:, 1]
    return math.degrees(math.atan2(float(jaw[1]), float(jaw[0]))) % 180.0


def solve(model: mujoco.MjModel, data: mujoco.MjData, arm: str, target_position: np.ndarray,
          target_quat: np.ndarray, seed: np.ndarray | None = None) -> np.ndarray:
    """Țintele de articulație care aduc flanșa în poziția și orientarea date.

    Se pleacă de la ``seed`` (ori de la starea de acum a brațului) și se corectează iterativ, cu
    Jacobinul flanșei: eroarea de poziție și cea de orientare intră în același pas, iar damping-ul
    ține pașii mărginiți lângă singularități. `qpos` se pune la loc la ieșire: rezultatul este o
    țintă pentru actuatoare, nu o teleportare a brațului.
    """
    saved = np.array(data.qpos, dtype=float)
    addresses = joint_qpos_addresses(model, arm)
    actuator = actuator_ids(model, arm)[:ARM_JOINTS]
    limits = np.array([model.actuator_ctrlrange[index] for index in actuator])
    lower = limits[:, 0] + IK_JOINT_MARGIN
    upper = limits[:, 1] - IK_JOINT_MARGIN
    identifier = _body_id(model, hand_body(arm))
    dof = np.array([
        int(model.jnt_dofadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)])
        for name in joint_names(arm)
    ])
    jacp = np.zeros((3, model.nv))
    jacr = np.zeros((3, model.nv))
    error = np.zeros(6)
    for address, value in zip(addresses, saved[addresses] if seed is None else seed):
        data.qpos[address] = value
    for _ in range(IK_ITERATIONS):
        mujoco.mj_forward(model, data)
        current = np.array(data.xmat[identifier], dtype=float).reshape(3, 3)
        error[:3] = target_position - np.array(data.xpos[identifier], dtype=float)
        error[3:] = rotation_vector(rotation_matrix(target_quat) @ current.T)
        if (float(np.linalg.norm(error[:3])) < IK_POSITION_TOLERANCE_M / 10
                and float(np.linalg.norm(error[3:])) < math.radians(IK_ANGLE_TOLERANCE_DEG) / 10):
            break
        mujoco.mj_jacBody(model, data, jacp, jacr, identifier)
        jacobian = np.vstack((jacp[:, dof], jacr[:, dof]))
        normal = jacobian @ jacobian.T + IK_DAMPING * np.eye(6)
        step = jacobian.T @ np.linalg.solve(normal, error) * IK_STEP
        current_qpos = np.array([float(data.qpos[address]) for address in addresses])
        for address, value in zip(addresses, np.clip(current_qpos + step, lower, upper)):
            data.qpos[address] = value
    result = np.array([float(data.qpos[address]) for address in addresses])
    np.copyto(data.qpos, saved)
    mujoco.mj_forward(model, data)
    return result


def solve_point(model: mujoco.MjModel, data: mujoco.MjData, arm: str,
                point: tuple[float, float, float], approach: tuple[float, float, float],
                spin_deg: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Țintele de articulație pentru un punct de lucru, cu rotirea sculei din plan.

    ``spin_deg`` este rotirea sculei în jurul direcției de apropiere cerută de plan
    (:data:`cncdt_mj.moves.TOOL_SPIN_DEG`), și este cea care se folosește dacă rezolvitorul o
    nimerește: piesa este un disc, însă falcile au o singură deschidere bună — de-a lungul cutiei,
    nu peste bandă —, deci rotirea este o cerință a planului, nu o alegere a brațului. Doar când
    rotirea cerută nu se poate atinge se caută în :data:`SPIN_CANDIDATES_DEG` în jurul ei, ca
    mișcarea să nu cadă din pricina unei singure orientări.

    Întoarce țintele și orientarea față de care au fost găsite, ca măsurătoarea de după mișcare să
    folosească aceeași țintă. Rezolvitorul este local: din unele configurații de pornire nu iese din
    văile lui, deși ținta e la îndemână, deci fiecare orientare se încearcă din două porniri —
    configurația de acum și cea nominală.
    """
    target_position, target_quat = hand_target(model, data, arm, point, approach)
    nominal = np.array([float(value) for value in nominal_arm_qpos(arm)])
    seeds = (None, nominal)

    def attempt(degrees: float) -> tuple[float, float, np.ndarray, np.ndarray]:
        """Cea mai bună încercare pentru o rotire: eroarea, drumul până acolo și soluția."""
        spun = spin_quat(target_quat, approach, degrees)
        best: tuple[float, float, np.ndarray, np.ndarray] | None = None
        for seed in seeds:
            candidate = solve(model, data, arm, target_position, spun, seed)
            error = residual(model, data, arm, target_position, spun, candidate)
            travel = float(np.linalg.norm(candidate - joint_controls(model, data, arm)))
            if best is None or (error, travel) < (best[0], best[1]):
                best = (error, travel, candidate, spun)
        assert best is not None
        return best

    chosen = attempt(spin_deg)
    if chosen[0] > IK_POSITION_TOLERANCE_M:
        for degrees in SPIN_CANDIDATES_DEG:
            candidate = attempt(spin_deg + degrees)
            if candidate[:2] < chosen[:2]:
                chosen = candidate
    return chosen[2], chosen[3]


def nominal_arm_qpos(arm: str) -> tuple[float, ...]:
    """Poziția nominală a brațului, cea scrisă de celulă în scenă."""
    from . import scene

    return scene.NOMINAL_ARM_QPOS[arm]


def residual(model: mujoco.MjModel, data: mujoco.MjData, arm: str, target_position: np.ndarray,
             target_quat: np.ndarray, qpos: np.ndarray) -> float:
    """Cât de departe ar rămâne flanșa de țintă dacă brațul ar sta în configurația dată.

    Măsoară poziția și orientarea împreună, în metri: o eroare de un grad se socotește pe raza
    sculei, ca cele două să se poată aduna.
    """
    saved = np.array(data.qpos, dtype=float)
    for address, value in zip(joint_qpos_addresses(model, arm), qpos):
        data.qpos[address] = value
    mujoco.mj_kinematics(model, data)
    position, _ = hand_pose(model, data, arm)
    current = np.array(data.xmat[_body_id(model, hand_body(arm))], dtype=float).reshape(3, 3)
    angle = float(np.linalg.norm(rotation_vector(rotation_matrix(target_quat) @ current.T)))
    distance = float(np.linalg.norm(target_position - position))
    np.copyto(data.qpos, saved)
    mujoco.mj_forward(model, data)
    return distance + angle * float(np.linalg.norm(pinch_local(model, data, arm)))


def follow(model: mujoco.MjModel, data: mujoco.MjData, arm: str, target: np.ndarray,
           hook: StepHook | None = None) -> int:
    """Duce brațul la țintele de articulație date, cu viteza din `JOINT_SPEED_RAD_S`.

    Ținta actuatoarelor se mișcă treptat de la starea de acum până la cea cerută, iar între pași se
    face un pas de simulare: brațul nu sare, iar drumul rămâne al servo-urilor. Întoarce câți pași a
    durat.
    """
    actuator = actuator_ids(model, arm)[:ARM_JOINTS]
    start = joint_controls(model, data, arm)
    distance = float(np.max(np.abs(target - start)))
    speed = JOINT_SPEED_RAD_S * float(model.opt.timestep)
    count = max(1, int(math.ceil(distance / speed)))
    for index in range(1, count + 1):
        data.ctrl[actuator] = start + (target - start) * (index / count)
        mujoco.mj_step(model, data)
        _report(hook, model, data)
    return count


def hold(model: mujoco.MjModel, data: mujoco.MjData, steps: int = SETTLE_STEPS,
         hook: StepHook | None = None) -> None:
    """Lasă servo-urile să ajungă pe țintă, cu comanda neschimbată."""
    for _ in range(steps):
        mujoco.mj_step(model, data)
        _report(hook, model, data)


def _report(hook: StepHook | None, model: mujoco.MjModel, data: mujoco.MjData) -> None:
    """Cheamă hook-ul, dacă există."""
    if hook is not None:
        hook(model, data)


def set_gripper(model: mujoco.MjModel, data: mujoco.MjData, arm: str, command: float) -> None:
    """Comandă falcile: `GRIPPER_OPEN` ori `GRIPPER_CLOSED` (ori o poziție între ele)."""
    identifier = int(actuator_ids(model, arm)[GRIPPER_ACTUATOR_INDEX])
    low, high = model.actuator_ctrlrange[identifier]
    data.ctrl[identifier] = min(max(command, float(low)), float(high))


def tool_path(start: np.ndarray, end: np.ndarray,
              step_m: float = PATH_STEP_M) -> list[np.ndarray]:
    """Punctele intermediare ale liniei drepte din ``start`` în ``end``, fără cel de plecare.

    Drumul sculei se descrie în coordonate, nu în articulații: interpolarea articulațiilor dintre
    două poziții bune trece prin configurații care nu sunt cerute de nimeni, iar într-o celulă
    strâmtă acelea sunt exact cele care lovesc mașina. Aici scula merge pe linie dreaptă, iar
    articulațiile rezultă din cinematică inversă la fiecare pas.
    """
    start = np.asarray(start, dtype=float)
    end = np.asarray(end, dtype=float)
    distance = float(np.linalg.norm(end - start))
    count = max(1, int(math.ceil(distance / step_m)))
    return [start + (end - start) * (index / count) for index in range(1, count + 1)]


def waypoint_target(model: mujoco.MjModel, data: mujoco.MjData, arm: str,
                    target_position: np.ndarray, quat: np.ndarray,
                    seed: np.ndarray) -> tuple[np.ndarray, float]:
    """Țintele de articulație pentru un punct de pe drum, plus cât a rămas din eroare.

    Se pleacă de la pasul dinainte, ca soluția să curgă. Dacă prima încercare nu ajunge la punct —
    se întâmplă când drumul intră într-o zonă din care rezolvitorul local nu iese —, se mai încearcă
    o dată din poziția nominală a brațului, care este mereu o configurație bună de pornire.
    """
    candidate = solve(model, data, arm, target_position, quat, seed)
    error = residual(model, data, arm, target_position, quat, candidate)
    if error > IK_POSITION_TOLERANCE_M:
        nominal = np.array([float(value) for value in nominal_arm_qpos(arm)])
        second = solve(model, data, arm, target_position, quat, nominal)
        second_error = residual(model, data, arm, target_position, quat, second)
        if second_error < error:
            candidate, error = second, second_error
    return candidate, error


def follow_line(model: mujoco.MjModel, data: mujoco.MjData, arm: str,
                point: tuple[float, float, float], approach: tuple[float, float, float],
                quat: np.ndarray, settle: int = SETTLE_STEPS,
                hook: StepHook | None = None) -> dict[str, float]:
    """Mută punctul de strângere în linie dreaptă din locul de acum până în ``point``.

    Fiecare pas al drumului se rezolvă prin cinematică inversă pornind de la pasul dinainte, deci
    soluția curge și nu sare în altă ramură la jumătatea drumului, iar servo-urile fac atâția pași
    de simulare cât ține mersul cu viteza din :data:`TOOL_SPEED_MPS`. Se reține cea mai mare eroare
    pe care a lăsat-o rezolvitorul pe drum: dacă acolo e o valoare mare, drumul a trecut printr-o
    configurație pe care brațul nu o poate urma, oricât de bine ar nimeri capătul.
    """
    end = np.asarray(point, dtype=float)
    quat = np.asarray(quat, dtype=float)
    steps = 0
    worst = 0.0
    seed = joint_controls(model, data, arm)
    per_point = max(1, int(round(PATH_STEP_M / TOOL_SPEED_MPS / float(model.opt.timestep))))
    for waypoint in tool_path(tool_point(model, data, arm), end):
        target_position = waypoint - rotation_matrix(quat) @ pinch_local(model, data, arm)
        seed, error = waypoint_target(model, data, arm, target_position, quat, seed)
        worst = max(worst, error)
        set_joint_controls(model, data, arm, seed)
        for _ in range(per_point):
            mujoco.mj_step(model, data)
            _report(hook, model, data)
            steps += 1
    hold(model, data, settle, hook)
    return {"steps": float(steps), "path_residual_m": worst}


def reach_point(model: mujoco.MjModel, data: mujoco.MjData, arm: str,
                point: tuple[float, float, float],
                approach: tuple[float, float, float],
                spin_deg: float = 0.0,
                settle: int = SETTLE_STEPS,
                hook: StepHook | None = None) -> dict[str, float]:
    """Duce scula în punctul dat și întoarce cât de departe a ajuns de el.

    Este mișcarea întreagă, ca o singură chemare: întâi se alege rotirea sculei
    (`solve_point`, căutare în jurul rotirii cerute de plan), apoi scula merge pe linie dreaptă
    până în punct (`follow_line`), iar la sfârșit se măsoară eroarea rămasă — poziție, orientare,
    cât lăsase rezolvitorul la țintă și câți pași a durat drumul.
    """
    target, quat = solve_point(model, data, arm, point, approach, spin_deg)
    target_position = hand_target(model, data, arm, point, approach)[0]
    report = follow_line(model, data, arm, point, approach, quat, settle, hook)
    report.update(error_report(model, data, arm, point, approach, quat))
    report["ik_residual_m"] = residual(model, data, arm, target_position, quat, target)
    return report