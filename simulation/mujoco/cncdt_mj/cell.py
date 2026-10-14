"""Celula în mișcare: primul pas este transportul pe benzi.

MuJoCo nu are „conveyor" de motor, deci banda își pune forța pe piesă la fiecare pas, iar forța o
calculează `cncdt_mj/belt.py` (pur, testabil fără motor). Aici stă partea care atinge motorul:
cine e în contact cu banda, ce viteză are piesa, ce forță se scrie în `data.xfrc_applied`.

Forța se aplică **numai** piesei care atinge platforma benzii (`*_slab`): o piesă aruncată de braț
peste bandă nu este transportată până nu o atinge, iar una ridicată de braț nu mai este împinsă.
Se oprește la linia benzii (``belt.stop_x()``): de acolo piesa alunecă singură până o oprește
frecarea, fiindcă banda nu are opritor fizic.

Modulul importă `mujoco`, ca `scene.py`; restul pachetului rămâne pur (ADR 0012).
"""

from __future__ import annotations

import mujoco
import numpy as np

from . import belt, layout


def _body_id(model: mujoco.MjModel, name: str) -> int:
    """Identificatorul unui corp, cu eroare care spune ce nume există."""
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    if identifier < 0:
        raise ValueError(f"corp inexistent în scenă: {name}")
    return int(identifier)


def part_bodies(model: mujoco.MjModel) -> tuple[str, ...]:
    """Numele corpurilor care pot fi transportate de benzi: cele două stări de material."""
    names = []
    for state in (layout.STATE_BLANK, layout.STATE_MACHINED):
        for item in layout.part_placements():
            if item.state == state and mujoco.mj_name2id(
                    model, mujoco.mjtObj.mjOBJ_BODY, item.body) >= 0:
                names.append(item.body)
    return tuple(names)


def carried_parts(model: mujoco.MjModel, data: mujoco.MjData,
                  parts: tuple[str, ...] | None = None) -> dict[str, str]:
    """Ce piesă stă pe ce bandă, citit din contactele de acum: ``{corp: cheia benzii}``.

    Contactul care contează este cel cu platforma benzii (``belt_in_slab``, ``belt_out_slab``):
    șinele și picioarele sunt geome ale aceluiași corp, dar o atingere a lor nu înseamnă că piesa
    este pe bandă.
    """
    names = part_bodies(model) if parts is None else parts
    carrying: dict[str, str] = {}
    for contact in data.contact[:data.ncon]:
        for geom, other in ((contact.geom1, contact.geom2), (contact.geom2, contact.geom1)):
            geom_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(geom)) or ""
            if not geom_name.endswith("_slab"):
                continue
            body_name = mujoco.mj_id2name(
                model, mujoco.mjtObj.mjOBJ_BODY, int(model.geom_bodyid[int(other)])) or ""
            if body_name in names:
                carrying[body_name] = geom_name[: -len("_slab")]
    return carrying


def part_velocity(model: mujoco.MjModel, data: mujoco.MjData, body: str) -> tuple[float, float]:
    """Viteza în plan a unei piese, în metri pe secundă, în sistemul lumii.

    Articulația liberă a piesei ține viteza liniară în primele trei poziții ale lui ``qvel``, iar
    pentru o articulație liberă MuJoCo o scrie în sistemul lumii.
    """
    identifier = _body_id(model, body)
    joint = int(model.body_jntadr[identifier])
    address = int(model.jnt_dofadr[joint])
    return (float(data.qvel[address]), float(data.qvel[address + 1]))


def apply_belt_forces(model: mujoco.MjModel, data: mujoco.MjData, speed_scale: float = 1.0,
                      parts: tuple[str, ...] | None = None) -> dict[str, tuple[float, float]]:
    """Scrie în `data.xfrc_applied` forța pe care o pune fiecare bandă pe piesa de pe ea.

    Forța se scrie pentru fiecare piesă, cu zero acolo unde nu e cazul: `mj_step` **nu** golește
    `xfrc_applied`, deci o piesă ridicată de braț ori ajunsă la linia de oprire a benzii ar rămâne
    cu forța de la pasul dinainte. Componenta verticală rămâne a contactului: banda trage
    tangențial, nu ridică.

    Întoarce forțele scrise, ca ``{corp: (Fx, Fy)}``, pentru raportare și pentru teste.
    """
    names = part_bodies(model) if parts is None else parts
    carrying = carried_parts(model, data, names)
    applied: dict[str, tuple[float, float]] = {}
    for body in names:
        identifier = _body_id(model, body)
        key = carrying.get(body)
        force = (0.0, 0.0)
        if key is not None:
            item = belt.belt(key)
            if not item.reached_stop(float(data.xpos[identifier][0])):
                mass = float(model.body_mass[identifier])
                force = item.drag_force(mass, part_velocity(model, data, body),
                                        speed_scale=speed_scale)
        np.copyto(data.xfrc_applied[identifier, :2], np.asarray(force))
        applied[body] = force
    return applied


def transport(model: mujoco.MjModel, data: mujoco.MjData, steps: int, speed_scale: float = 1.0,
              parts: tuple[str, ...] | None = None) -> None:
    """Rulează pașii cu benzile pornite: forța se rescrie la fiecare pas, apoi se face pasul.

    `mj_step` nu golește `data.xfrc_applied`, deci forța se scrie la fiecare pas: o dată pentru
    toată cursa ar rămâne aplicată și după ce piesa ajunge la linia de oprire ori e ridicată de braț.
    """
    for _ in range(steps):
        apply_belt_forces(model, data, speed_scale, parts)
        mujoco.mj_step(model, data)


def part_positions(model: mujoco.MjModel, data: mujoco.MjData,
                   parts: tuple[str, ...] | None = None) -> dict[str, tuple[float, float, float]]:
    """Poziția în lume a fiecărei piese, pentru raportare și pentru criteriul „piese pierdute"."""
    names = part_bodies(model) if parts is None else parts
    return {name: tuple(float(value) for value in data.xpos[_body_id(model, name)])
            for name in names}
