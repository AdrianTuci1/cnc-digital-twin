"""Din starea motorului în mărimi de proces: ce citesc senzorii celulei.

Scena nu are senzori declarați — `model.nsensor == 0`, verificat —, deci fiecare mărime se
calculează din `MjData`: efortul articulațiilor din forțele actuatoarelor, poziția și viteza
pieselor din articulațiile lor libere, sarcina de contact din lista de contacte a pasului de acum.
Sunt mărimi ale **celulei**, nu ale așchierii: sarcina axului, vibrația, temperatura, debitul de
răcire și uzura descriu prelucrarea, iar scena de azi nu are axele mașinii. Ele stau în
`telemetry.MachineReading` și vin din scenariul sesiunii, cu același nume, până le scrie motorul de
uzură (`docs/11-geaman-mujoco.md` §3, faza 1).

Modulul importă `mujoco`, ca `scene.py`, `cell.py` și `arms.py`; contractele de date rămân pure, în
`telemetry.py` (ADR 0012).
"""

from __future__ import annotations

from typing import Any, Mapping

import mujoco
import numpy as np

from . import arms, belt, cell


def joint_effort_nm(model: mujoco.MjModel, data: mujoco.MjData) -> dict[str, float]:
    """Efortul mediu al articulațiilor fiecărui braț, în newton-metri.

    `actuator_force` este forța pe care o pune actuatorul la pasul de acum: pentru cele șapte
    articulații ale unui braț Panda este un cuplu, iar media modulelor spune cât de încordat este
    brațul. Este mărimea care crește cu frecarea articulațiilor, deci coloana `joint_effort` din
    contract are sursă în simulare, nu în scenariu.
    """
    return {
        arm: float(np.mean(np.abs(data.actuator_force[arms.actuator_ids(model, arm)
                                                          [:arms.ARM_JOINTS]])))
        for arm in arms.ARMS
    }


def grip_force_n(model: mujoco.MjModel, data: mujoco.MjData) -> dict[str, float]:
    """Forța de strângere a fiecărui griper, în newtoni, citită din actuatorul falcilor."""
    return {
        arm: float(abs(data.actuator_force[arms.actuator_ids(model, arm)
                                          [arms.GRIPPER_ACTUATOR_INDEX]]))
        for arm in arms.ARMS
    }


def part_readings(model: mujoco.MjModel, data: mujoco.MjData, parts: tuple[str, ...]
                  ) -> dict[str, dict[str, Any]]:
    """Poziția, viteza și banda fiecărei piese, citite din scenă.

    Viteza în plan vine din articulația liberă a piesei; componenta verticală se ia din aceeași
    articulație, ca să se vadă și o piesă scăpată din falci. Banda pe care stă este cea din
    contactul cu platforma ei (`cell.carried_parts`), nu cea dedusă din coordonate.
    """
    positions = cell.part_positions(model, data, parts)
    carrying = cell.carried_parts(model, data, parts)
    readings: dict[str, dict[str, Any]] = {}
    for body in parts:
        planar = cell.part_velocity(model, data, body)
        identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body)
        joint = int(model.body_jntadr[identifier])
        address = int(model.jnt_dofadr[joint])
        speed = float(np.linalg.norm([planar[0], planar[1], data.qvel[address + 2]]))
        readings[body] = {
            "position": positions[body],
            "speed_mps": speed,
            "belt": carrying.get(body),
        }
    return readings


def contact_load_n(model: mujoco.MjModel, data: mujoco.MjData, parts: tuple[str, ...]) -> float:
    """Suma modulelor forțelor de contact în care intră vreo piesă, în newtoni.

    Forța unui contact se cere motorului (`mj_contactForce`), în sistemul contactului: este
    mărimea care sare când piesa este strânsă ori lovită, deci semnalul cel mai apropiat de un
    senzor de forță de pe falci.
    """
    bodies = {mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body) for body in parts}
    wrench = np.zeros(6)
    total = 0.0
    for index, contact in enumerate(data.contact[:data.ncon]):
        if int(model.geom_bodyid[contact.geom1]) in bodies \
                or int(model.geom_bodyid[contact.geom2]) in bodies:
            mujoco.mj_contactForce(model, data, index, wrench)
            total += float(np.linalg.norm(wrench[:3]))
    return total


def reading(model: mujoco.MjModel, data: mujoco.MjData, parts: tuple[str, ...],
            effort_scale: Mapping[str, float] | float = 1.0,
            belt_speed_mps: float | None = None) -> dict[str, Any]:
    """Toate mărimile unui pas: ce intră în contract și ce rămâne diagnostic local.

    `joint_effort_nm` este media brațelor, adică exact câmpul `joint_effort` din
    `cnc.telemetry.v1.json`. Restul — efortul pe braț, forța falcilor, viteza benzii, piesa și
    contactele — nu are coloană în contract: rămâne în sesiune, ca diagnostic și ca sursă pentru
    testul de paritate cu scena Isaac Sim.

    ``effort_scale`` este efectul uzurii articulațiilor: scena are `frictionloss` fix în modelul
    Panda, deci frecarea crescută se aplică peste efortul măsurat, ca eșantionul și diagnosticul să
    spună același lucru. ``belt_speed_mps`` este viteza reală de suprafață a benzilor, care scade cu
    uzura; fără el se raportează cea nominală.
    """
    efforts = joint_effort_nm(model, data)
    scales = effort_scale if isinstance(effort_scale, Mapping) else None
    scaled = {arm: value * (scales.get(arm, 1.0) if scales else float(effort_scale))
              for arm, value in efforts.items()}
    return {
        "sim_time_s": float(data.time),
        "joint_effort_nm": float(np.mean(list(scaled.values()))),
        "arm_effort_nm": scaled,
        "grip_force_n": grip_force_n(model, data),
        "belt_speed_mps": (belt.BELT_SURFACE_SPEED_MPS if belt_speed_mps is None
                           else float(belt_speed_mps)),
        "parts": part_readings(model, data, parts),
        "contact_load_n": contact_load_n(model, data, parts),
        "contacts": int(data.ncon),
    }


def reading_row(reading_values: Mapping[str, Any]) -> dict[str, Any]:
    """Rândul de diagnostic al unei citiri, așa cum intră în `readings.jsonl`.

    Contractul de telemetrie este închis (`additionalProperties: false`), deci mărimile care nu au
    coloană acolo nu se strecoară în eșantion: se scriu alături, în fișierul local al sesiunii.
    """
    parts = reading_values["parts"]
    return {
        "sim_time_s": round(float(reading_values["sim_time_s"]), 6),
        "joint_effort_nm": round(float(reading_values["joint_effort_nm"]), 6),
        "arm1_effort_nm": round(float(reading_values["arm_effort_nm"]["arm1"]), 6),
        "arm2_effort_nm": round(float(reading_values["arm_effort_nm"]["arm2"]), 6),
        "arm1_grip_force_n": round(float(reading_values["grip_force_n"]["arm1"]), 6),
        "arm2_grip_force_n": round(float(reading_values["grip_force_n"]["arm2"]), 6),
        "belt_speed_mps": round(float(reading_values["belt_speed_mps"]), 6),
        "contact_load_n": round(float(reading_values["contact_load_n"]), 6),
        "contacts": int(reading_values["contacts"]),
        **{f"{body}_x": round(float(item["position"][0]), 6) for body, item in parts.items()},
        **{f"{body}_y": round(float(item["position"][1]), 6) for body, item in parts.items()},
        **{f"{body}_speed_mps": round(float(item["speed_mps"]), 6) for body, item in parts.items()},
        **{f"{body}_belt": item["belt"] for body, item in parts.items()},
    }
