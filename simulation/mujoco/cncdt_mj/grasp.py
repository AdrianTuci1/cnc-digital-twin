"""Prinderea piesei: falcile se închid fizic, iar legătura rigidă ține piesa în mână.

Griperul Panda are două falci comandate prin tendon și o deschidere de 80 mm. Falcile se închid cu
fizica motorului: se comandă actuatorul spre zero, iar contactul dintre vârfuri și piesă oprește
închiderea. Pentru discurile mai mari decît deschiderea — roata de 100 mm — falcile ajung pe fața
de sus a piesei, nu pe diametru; legătura rigidă de după contact este cea care ține piesa, exact
cum celula din Isaac Sim leagă piesa de griper printr-o articulație fixă.

Legăturile sunt egalități de tip `weld` **predefinite în scenă** și activate la rulare. MuJoCo nu
poate adăuga constrângeri după compilare, dar poate schimba `eq_data` și `eq_active`; legătura se
scrie cu poziția relativă de acum, deci piesa nu sare cînd este prinsă. O a doua egalitate, tot
`weld`, ține piesa pe bandă pînă la prindere (o clemă de oprire invizibilă): altfel vîrfurile
falcile împing un disc liber cîțiva centimetri, iar prinderea ratată arată ca un defect de fizică.

Modulul atinge motorul, ca `arms.py`; restul pachetului rămîne pur (ADR 0012).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable

import mujoco
import numpy as np

from . import arms

#: Cît de tare ține legătura rigidă și clema. `solref` scurt înseamnă arc rigid; `solimp` aproape
#: de 1 înseamnă că legătura nu se lasă. Valorile sunt cele ale unei prinderi metalice, nu ale unui
#: arc moale.
WELD_SOLREF = (0.004, 1.0)
WELD_SOLIMP = (0.95, 0.99, 0.001, 0.5, 2.0)

#: Cîți pași se lasă falcile să se închidă, respectiv să se deschidă și piesa să cadă.
CLOSE_STEPS = 420
OPEN_STEPS = 160
RELEASE_STEPS = 180

#: Numele egalității de prindere dintre un braț și o piesă.
def grasp_equality(arm: str, body: str) -> str:
    """Numele legăturii rigide dintre mîna brațului și piesă."""
    return f"grasp_{arm}_{body}"


def fixture_equality(body: str) -> str:
    """Numele clemei care ține piesa pe loc pînă la prindere."""
    return f"fixture_{body}"


@dataclass(frozen=True)
class GraspReport:
    """Ce s-a întîmplat la o prindere ori la o eliberare, ca rînd de diagnostic."""

    arm: str
    body: str
    action: str
    contact: bool
    success: bool
    finger_gap_m: float
    steps: int
    point: str = ""
    left_contacts: int = 0
    right_contacts: int = 0

    def as_dict(self) -> dict[str, object]:
        """Rîndul care intră în `session.json`, sub ciclul lui."""
        return {
            "arm": self.arm,
            "body": self.body,
            "action": self.action,
            "point": self.point,
            "contact": bool(self.contact),
            "success": bool(self.success),
            "finger_gap_m": round(float(self.finger_gap_m), 6),
            "left_contacts": int(self.left_contacts),
            "right_contacts": int(self.right_contacts),
            "steps": int(self.steps),
        }


def body_id(model: mujoco.MjModel, name: str) -> int:
    """Identificatorul unui corp, cu eroare care spune ce nume lipsește."""
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    if identifier < 0:
        raise ValueError(f"corp inexistent în scenă: {name}")
    return int(identifier)


def equality_id(model: mujoco.MjModel, name: str) -> int:
    """Identificatorul unei egalități, cu eroare care spune ce nume lipsește."""
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_EQUALITY, name)
    if identifier < 0:
        raise ValueError(f"egalitate inexistentă în scenă: {name}")
    return int(identifier)


def add_constraints(spec: mujoco.MjSpec, arm_names: Iterable[str],
                    part_bodies: Iterable[str]) -> None:
    """Adaugă în scenă legăturile de prindere și clemele, inactive.

    O legătură pentru fiecare pereche braț–piesă, fiindcă aceeași piesă poate fi prinsă de oricare
    braț; și o clemă pentru fiecare piesă, ca piesa să poată fi ținută pe bandă pînă la prindere.
    """
    for arm in arm_names:
        for body in part_bodies:
            _add_weld(spec, grasp_equality(arm, body), f"{arm}_hand", body)
    for body in part_bodies:
        _add_weld(spec, fixture_equality(body), body, "")


def _add_weld(spec: mujoco.MjSpec, name: str, body1: str, body2: str) -> None:
    """O egalitate `weld` inactivă, cu `eq_data` care se rescrie la activare."""
    equality = spec.add_equality()
    equality.name = name
    equality.type = mujoco.mjtEq.mjEQ_WELD
    equality.objtype = mujoco.mjtObj.mjOBJ_BODY
    equality.name1 = body1
    equality.name2 = body2
    equality.active = False
    equality.solref = list(WELD_SOLREF)
    equality.solimp = list(WELD_SOLIMP)
    # `eq_data` are 11 valori: trei nefolosite la weld, poziția relativă (3), cuaternarul (4) și
    # scara cuplului (1). Se scriu toate la activare; aici doar forma este validă.
    equality.data = [0.0] * 10 + [1.0]


def _pose(model: mujoco.MjModel, data: mujoco.MjData,
          body: str) -> tuple[np.ndarray, np.ndarray]:
    """Poziția și orientarea unui corp, în sistemul lumii."""
    identifier = body_id(model, body)
    return (np.array(data.xpos[identifier], dtype=float),
            np.array(data.xquat[identifier], dtype=float))


def _set_relative(model: mujoco.MjModel, data: mujoco.MjData, name: str,
                  first: tuple[np.ndarray, np.ndarray],
                  second: tuple[np.ndarray, np.ndarray]) -> None:
    """Scrie poziția relativă dintre două corpuri și activează legătura.

    Legătura `weld` impune ca al doilea corp să stea, în sistemul primului, exact la poziția
    scrisă. Se ia chiar poziția de acum, deci activarea nu mișcă nimic.
    """
    (position1, quat1), (position2, quat2) = first, second
    rotation = np.zeros(9)
    mujoco.mju_quat2Mat(rotation, np.asarray(quat1, dtype=float))
    relative_position = rotation.reshape(3, 3).T @ (position2 - position1)
    inverse = np.zeros(4)
    mujoco.mju_negQuat(inverse, np.asarray(quat1, dtype=float))
    relative_quat = np.zeros(4)
    mujoco.mju_mulQuat(relative_quat, inverse, np.asarray(quat2, dtype=float))
    identifier = equality_id(model, name)
    model.eq_data[identifier, :3] = 0.0
    model.eq_data[identifier, 3:6] = relative_position
    model.eq_data[identifier, 6:10] = relative_quat
    model.eq_data[identifier, 10] = 1.0
    data.eq_active[identifier] = 1


def clamp(model: mujoco.MjModel, data: mujoco.MjData, body: str) -> None:
    """Ține piesa pe loc, legată de lume, pînă cînd o prinde brațul."""
    _set_relative(model, data, fixture_equality(body), _pose(model, data, body),
                  (np.zeros(3), np.array([1.0, 0.0, 0.0, 0.0])))


def unclamp(model: mujoco.MjModel, data: mujoco.MjData, body: str) -> None:
    """Scoate clema piesei."""
    data.eq_active[equality_id(model, fixture_equality(body))] = 0


def is_clamped(model: mujoco.MjModel, data: mujoco.MjData, body: str) -> bool:
    """Dacă piesa este ținută de clemă."""
    return bool(data.eq_active[equality_id(model, fixture_equality(body))])


def attach(model: mujoco.MjModel, data: mujoco.MjData, arm: str, body: str) -> None:
    """Leagă piesa de mîna brațului, păstrînd poziția relativă de acum."""
    unclamp(model, data, body)
    _set_relative(model, data, grasp_equality(arm, body),
                  _pose(model, data, f"{arm}_hand"), _pose(model, data, body))
    _stop(model, data, body)


def release(model: mujoco.MjModel, data: mujoco.MjData, arm: str, body: str) -> None:
    """Desface legătura rigidă, fără să miște piesa."""
    data.eq_active[equality_id(model, grasp_equality(arm, body))] = 0


def is_attached(model: mujoco.MjModel, data: mujoco.MjData, arm: str, body: str) -> bool:
    """Dacă piesa este legată de mîna brațului."""
    return bool(data.eq_active[equality_id(model, grasp_equality(arm, body))])


def _stop(model: mujoco.MjModel, data: mujoco.MjData, body: str) -> None:
    """Oprește piesa din mișcare, ca legătura să nu primească un impuls la activare."""
    identifier = body_id(model, body)
    joint = int(model.body_jntadr[identifier])
    address = int(model.jnt_dofadr[joint])
    data.qvel[address:address + 6] = 0.0


def finger_contact_counts(model: mujoco.MjModel, data: mujoco.MjData, arm: str,
                          body: str) -> tuple[int, int]:
    """Cîte contacte are fiecare falcă cu piesa, în ordinea stînga–dreapta.

    O prindere sigură are contact pe ambele falci. Un contact doar pe o falcă înseamnă că piesa
    stă pe vîrful celeilalte ori a fost împinsă într-o parte, iar legătura rigidă ar ascunde
    problema pînă la așezare.
    """
    left, right = (body_id(model, name) for name in arms.finger_bodies(arm))
    part = body_id(model, body)
    counts = [0, 0]
    for contact in data.contact[:data.ncon]:
        first = int(model.geom_bodyid[contact.geom1])
        second = int(model.geom_bodyid[contact.geom2])
        if second == part:
            first, second = second, first
        if first != part:
            continue
        if second == left:
            counts[0] += 1
        elif second == right:
            counts[1] += 1
    return counts[0], counts[1]


def finger_contacts(model: mujoco.MjModel, data: mujoco.MjData, arm: str,
                    body: str) -> int:
    """Cîte contacte sînt între falcile brațului și piesă, în starea de acum."""
    return sum(finger_contact_counts(model, data, arm, body))


def finger_gap(model: mujoco.MjModel, data: mujoco.MjData, arm: str) -> float:
    """Deschiderea dintre falci, în metri: zero închise, ~80 mm complet deschise.

    Se citește din poziția articulațiilor, nu din comenză: după ce falcile întîlnesc piesa,
    comanda rămîne „închis", dar ele se opresc la diametrul piesei. Un report cu deschidere zero
    înseamnă că falcile s-au închis complet, adică n-au găsit nimic între ele.
    """
    gap = 0.0
    for name in (f"{arm}_finger_joint1", f"{arm}_finger_joint2"):
        joint = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if joint < 0:
            raise ValueError(f"articulație inexistentă în scenă: {name}")
        gap += float(data.qpos[int(model.jnt_qposadr[joint])])
    return max(0.0, float(gap))


def close(model: mujoco.MjModel, data: mujoco.MjData, arm: str, body: str | None = None,
          steps: int = CLOSE_STEPS, hook: Callable[[mujoco.MjModel, mujoco.MjData], None]
          | None = None) -> GraspReport:
    """Comandă falcile închis și lasă fizica să oprească închiderea pe piesă."""
    arms.set_gripper(model, data, arm, arms.GRIPPER_CLOSED)
    contact = False
    counts = (0, 0)
    grasp_counts: tuple[int, int] | None = None
    for _ in range(steps):
        mujoco.mj_step(model, data)
        if hook is not None:
            hook(model, data)
        if body is not None:
            counts = finger_contact_counts(model, data, arm, body)
            contact = contact or bool(counts[0] or counts[1])
            # Verdictul cere ambele falci în același pas, nu contacte răzlețe: o piesă ținută
            # de un singur vîrf alunecă la prima accelerație, iar legătura rigidă ar ascunde
            # problema pînă la așezare.
            if counts[0] and counts[1]:
                grasp_counts = counts
    if grasp_counts is None and body is not None:
        grasp_counts = counts
    left, right = grasp_counts or (0, 0)
    return GraspReport(arm=arm, body=body or "", action="close", contact=contact,
                       success=bool(body is None or (left and right)),
                       finger_gap_m=finger_gap(model, data, arm), steps=steps,
                       left_contacts=left, right_contacts=right)


def pick(model: mujoco.MjModel, data: mujoco.MjData, arm: str, body: str,
         steps: int = CLOSE_STEPS,
         hook: Callable[[mujoco.MjModel, mujoco.MjData], None] | None = None,
         point: str = "") -> GraspReport:
    """Închide falcile pe piesă, scoate clema și leagă piesa de mînă.

    Ordinea contează: piesa stă în clemă cît se închid falcile, altfel vîrfurile o împing; abia
    după contact clema se scoate și legătura rigidă preia piesa.
    """
    report = close(model, data, arm, body, steps=steps, hook=hook)
    if report.success:
        attach(model, data, arm, body)
    return replace(report, action="pick", point=point)


def place(model: mujoco.MjModel, data: mujoco.MjData, arm: str, body: str,
          steps: int = OPEN_STEPS,
          hook: Callable[[mujoco.MjModel, mujoco.MjData], None] | None = None,
          point: str = "", hold: bool = False) -> GraspReport:
    """Deschide falcile, apoi desface legătura, ca piesa să cadă pe suprafață.

    Falcile se deschid prima dată: altfel piesa ar fi eliberată între vîrfuri încă închise, iar
    deschiderea ar împinge-o în lateral.

    ``hold`` ține piesa pe loc cu clema de îndată ce legătura se desface, înainte ca gravitația
    să o miște. Se folosește pe raft și în universal: un disc de Ø104 mm stă cu o parte peste
    marginea raftului, iar primele 180 de trepte de așezare îl răsturnau. Clemă este invizibilă
    și reprezintă sprijinul raftului, nu o prindere a brațului.
    """
    attached = is_attached(model, data, arm, body)
    arms.set_gripper(model, data, arm, arms.GRIPPER_OPEN)
    for _ in range(steps):
        mujoco.mj_step(model, data)
        if hook is not None:
            hook(model, data)
    # Deschiderea se citește înainte de desfacerea legăturii: după aceea piesa rămasă pe raft
    # poate împinge o falcă peste limita articulației, iar cifra ar arăta o prindere mai largă
    # decît a fost.
    gap = finger_gap(model, data, arm)
    release(model, data, arm, body)
    if hold:
        clamp(model, data, body)
    for _ in range(RELEASE_STEPS):
        mujoco.mj_step(model, data)
        if hook is not None:
            hook(model, data)
    return GraspReport(arm=arm, body=body, action="place", contact=True,
                       success=attached, finger_gap_m=gap,
                       steps=steps + RELEASE_STEPS, point=point)


def set_body_pose(model: mujoco.MjModel, data: mujoco.MjData, body: str,
                  position: tuple[float, float, float],
                  quat: tuple[float, float, float, float]) -> None:
    """Mută o piesă liberă în poziția dată, cu viteza zero.

    Se folosește la schimbul de material din mașină: blocul brut pleacă pe masa de descărcare,
    iar piesa mașinată apare în universal. Este o tranziție de proces, nu o mișcare a brațului.
    """
    identifier = body_id(model, body)
    joint = int(model.body_jntadr[identifier])
    address = int(model.jnt_qposadr[joint])
    data.qpos[address:address + 3] = np.asarray(position, dtype=float)
    data.qpos[address + 3:address + 7] = np.asarray(quat, dtype=float)
    data.qvel[int(model.jnt_dofadr[joint]):int(model.jnt_dofadr[joint]) + 6] = 0.0
    mujoco.mj_forward(model, data)
