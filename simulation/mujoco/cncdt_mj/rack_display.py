"""Copiile de afișare ale pieselor sortate: raftul păstrează ce a trecut prin celulă.

Ciclul are un singur corp de piesă mașinată. La schimbul de material al ciclului următor, acel
corp pleacă din raft în universal, iar raftul rămînea gol. Aici poziția lui se copiază pe un corp
`mocap` înainte de plecare: copia nu cade, nu se ciocnește și nu intră în nicio verificare de
proces, dar se vede în raft. Corpurile există din construcția scenei, fiindcă MuJoCo nu poate
adăuga corpuri după compilare.

Copiile stau ascunse sub podea cît timp nu sînt folosite. La fiecare ciclu, piesa din raft se
copiază pe prima copie liberă; cînd toate sînt ocupate, cea mai veche este refolosită.
"""

from __future__ import annotations

import mujoco
import numpy as np

from . import layout

#: Distanța dintre centrele a două piese din același compartiment, pe lungimea raftului. Cea mai
#: lată piesă are 99 mm (roata), deci 10 cm o lasă să încapă. La 13 cm, scula ce coboară spre
#: locul dinspre margine atingea peretele compartimentului (`arm2_hand↔rack`) la placă.
SLOT_SPACING_M = 0.10

#: Cît de aproape trebuie să fie o copie de un loc ca să-l ocupe. Poziția de așezare are o
#: abatere de cîțiva milimetri, deci pragul este larg, dar rămîne sub jumătatea distanței dintre
#: locuri.
SLOT_TOLERANCE_M = 0.04


def _body_id(model: mujoco.MjModel, name: str) -> int:
    """Identificatorul unui corp de copie, ori ``-1`` dacă scena nu are copii."""
    return int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name))


def _geom_id(model: mujoco.MjModel, name: str) -> int:
    """Identificatorul unui geom de piesă, ori ``-1``."""
    return int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name))


def _mocap_index(model: mujoco.MjModel, name: str) -> int:
    """Indexul din `data.mocap_pos` al copiei date, ori ``-1``."""
    body = _body_id(model, name)
    if body < 0:
        return -1
    return int(model.body_mocapid[body])


def park(model: mujoco.MjModel, data: mujoco.MjData, name: str) -> None:
    """Ascunde o copie sub podea, fără să o scoată din scenă."""
    index = _mocap_index(model, name)
    if index < 0:
        return
    data.mocap_pos[index] = (0.0, 0.0, layout.RACK_COPY_PARK_Z)
    data.mocap_quat[index] = (1.0, 0.0, 0.0, 0.0)


def park_all(model: mujoco.MjModel, data: mujoco.MjData) -> None:
    """Ascunde toate copiile: starea de pornire a scenei și cea de după resetare."""
    for name in layout.RACK_COPY_BODIES:
        park(model, data, name)


def _copy_position(model: mujoco.MjModel, data: mujoco.MjData,
                   name: str) -> np.ndarray:
    """Poziția unei copii, citită din starea mocap, fără să fie nevoie de un pas."""
    index = _mocap_index(model, name)
    return np.array(data.mocap_pos[index], dtype=float)


def _copy_quat(model: mujoco.MjModel, data: mujoco.MjData,
               name: str) -> np.ndarray:
    """Cuaternarul unei copii, citit din starea mocap."""
    index = _mocap_index(model, name)
    return np.array(data.mocap_quat[index], dtype=float)


def visible(model: mujoco.MjModel, data: mujoco.MjData,
            rack: str) -> list[tuple[str, np.ndarray]]:
    """Copiile vizibile din compartimentul `rack`, ca perechi (nume, poziție).

    O copie este vizibilă dacă stă deasupra podelei; cele ascunse au cota
    :data:`layout.RACK_COPY_PARK_Z`. Se cer doar copiile din amprenta compartimentului, fiindcă
    un dulap are șase compartimente, iar fiecare are propriile locuri.
    """
    entry = layout.rack_entry(layout.rack_slot_index(rack) + 1)
    x = float(layout.RACK["x"])
    y0 = float(entry["y"])
    y1 = y0 + layout.RACK_ENTRY_M
    rows: list[tuple[str, np.ndarray]] = []
    for name in layout.RACK_COPY_BODIES:
        if _mocap_index(model, name) < 0:
            continue
        position = _copy_position(model, data, name)
        if position[2] <= 0.0:
            continue
        if not (x <= position[0] <= x + layout.RACK_DEPTH_M + 0.05):
            continue
        if not (y0 <= position[1] <= y1):
            continue
        rows.append((name, position))
    return rows


def used_offsets(model: mujoco.MjModel, data: mujoco.MjData,
                 rack: str, center_y: float) -> list[float]:
    """Abaterile pe lungimea raftului ale copiilor vizibile, față de centrul compartimentului."""
    return [float(position[1] - center_y) for _, position in visible(model, data, rack)]


def archive(model: mujoco.MjModel, data: mujoco.MjData,
            source_body: str) -> str | None:
    """Copiază poziția piesei `source_body` pe o copie de afișare și o face vizibilă.

    Se ia prima copie ascunsă, ca raftul să se umple de la sud la nord; cînd toate sînt ocupate,
    se refolosește cea cu numărul cel mai mic. Întoarce numele copiei folosite, ori ``None`` dacă
    scena nu are copii.
    """
    body = _body_id(model, source_body)
    if body < 0:
        return None
    names = [name for name in layout.RACK_COPY_BODIES if _mocap_index(model, name) >= 0]
    if not names:
        return None
    free = next((name for name in names
                 if _copy_position(model, data, name)[2] <= 0.0), names[0])
    index = _mocap_index(model, free)
    data.mocap_pos[index] = data.xpos[body]
    data.mocap_quat[index] = data.xquat[body]
    # Copia ia forma pe care o are piesa acum, nu forma inițială a scenei: în demonstrația pe
    # familii, raftul trebuie să arate ce variantă a fost sortată în fiecare compartiment.
    source_geom = _geom_id(model, f"{source_body}_geom")
    copy_geom = _geom_id(model, f"{free}_geom")
    if source_geom >= 0 and copy_geom >= 0:
        model.geom_dataid[copy_geom] = model.geom_dataid[source_geom]
    return free


def free_slot(model: mujoco.MjModel, data: mujoco.MjData, rack: str,
              center_y: float) -> float:
    """Abaterea unui loc liber din compartiment, față de centrul lui.

    Primul loc este centrul; următoarele se așază la sud și la nord. Cînd toate locurile sînt
    ocupate, copia cea mai veche este ascunsă și locul ei se eliberează: raftul ține trei piese
    pe compartiment, iar a patra o înlocuiește pe prima. Altfel, un compartiment s-ar umple la
    nesfîrșit cu piese suprapuse.
    """
    offsets = (0.0, -SLOT_SPACING_M, SLOT_SPACING_M)
    rows = visible(model, data, rack)
    used = [float(position[1] - center_y) for _, position in rows]
    free = next((offset for offset in offsets
                 if all(abs(offset - value) > SLOT_TOLERANCE_M for value in used)), None)
    if free is not None:
        return free
    # Toate locurile sînt ocupate: cea mai veche copie este cea cu numărul cel mai mic.
    oldest = min(rows, key=lambda row: row[0])
    offset = float(oldest[1][1] - center_y)
    park(model, data, oldest[0])
    return offset
