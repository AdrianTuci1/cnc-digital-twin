"""Benzile: cum se mișcă piesele pe ele.

MuJoCo nu are „conveyor" de motor, deci forța care târăște piesa se calculează aici și se aplică
per pas, prin `data.xfrc_applied` (vezi `cell.py`). Modulul dă partea pură — direcția fiecărei
benzi, viteza de suprafață, forța de antrenare cu limita de frecare — iar testele din `.venv`-ul
proiectului o pot verifica fără motor (ADR 0012).

Direcțiile vin din `docs/11-geaman-mujoco.md` §2: banda 1 alimentează spre est, banda 2 evacuază
spre vest, cu un singur sens de mers. Cine oprește piesa la capătul cursei este opritorul fizic
din `scene/cell.xml`, nu o condiție din cod: o condiție s-ar putea dezlipi de geometrie, un perete
nu.

Forța de frecare are limita lui Coulomb, ``|F| <= mu * N``; peste ea contactul alunecă, iar piesa
rămâne în urmă — exact ce trebuie să se vadă în date când banda se uzează.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from . import layout

#: Direcția de mers a fiecărei benzi și ținta de preluare a ei. Amândouă sunt fapte de așezare,
#: luate ca atare din `layout`, ca desenul și transportul să nu se poată contrazice.
BELT_DIRECTIONS = layout.BELT_DIRECTIONS
STOP_TARGETS = layout.BELT_STOP_TARGETS

#: Viteza de suprafață nominală, în metri pe secundă. Un transport realist pentru piese de un
#: kilogram într-o celulă de laborator: cursa benzii 1 (2,4 m) durează 20 s.
BELT_SURFACE_SPEED_MPS = 0.12

#: Coeficientul de frecare dintre piesă și bandă. Banda are `friction` ridicat în scenă, iar
#: limita de frecare a forței de antrenare se calculează din el.
BELT_FRICTION = 1.0

#: Cât de tare corectează banda viteza piesei: forță pe kilogram și pe metru pe secundă de
#: diferență de viteză. Destul de mare ca piesa să ajungă la viteza benzii în câteva zecimi de
#: secundă, dar limitat oricum de frecare.
BELT_DRAG_GAIN_N_PER_KG_MPS = 60.0

#: Accelerația gravitațională, folosită la forța normală implicită dintre piesă și bandă.
GRAVITY_MPS2 = 9.81

@dataclass(frozen=True)
class Belt:
    """O bandă: direcția ei în plan și viteza de suprafață."""

    key: str
    direction: tuple[float, float]
    surface_speed_mps: float = BELT_SURFACE_SPEED_MPS
    friction: float = BELT_FRICTION
    footprint: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def surface_velocity(self, speed_scale: float = 1.0) -> tuple[float, float]:
        """Viteza de suprafață, în m/s. ``speed_scale`` este locul unde intră driftul de uzură.

        `docs/11-geaman-mujoco.md` §3b: când banda se uzează, viteza ei reală scade cu până la
        8%. Contractul de aici este un factor, nu o valoare: uzura nu se pune încă în cod.
        """
        speed = self.surface_speed_mps * speed_scale
        return (self.direction[0] * speed, self.direction[1] * speed)

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        """Dacă un punct din plan este deasupra benzii, opțional cu o marjă de toleranță."""
        x0, x1, y0, y1 = self.footprint
        return (x0 - margin <= x <= x1 + margin) and (y0 - margin <= y <= y1 + margin)

    def drag_force(self, mass_kg: float, velocity_xy: tuple[float, float],
                   normal_force_n: float | None = None, speed_scale: float = 1.0
                   ) -> tuple[float, float]:
        """Forța tangențială pe care banda o pune pe o piesă aflată pe ea, în newtoni.

        Forța este proporțională cu diferența dintre viteza de suprafață și viteza piesei, dar
        limitată de frecare: ``mu * N``. ``normal_force_n`` este apăsarea dintre piesă și bandă;
        implicit greutatea piesei, iar `cell.py` poate pasa apăsarea măsurată în contact.
        """
        vx, vy = self.surface_velocity(speed_scale)
        force = (BELT_DRAG_GAIN_N_PER_KG_MPS * mass_kg * (vx - velocity_xy[0]),
                 BELT_DRAG_GAIN_N_PER_KG_MPS * mass_kg * (vy - velocity_xy[1]))
        normal = mass_kg * GRAVITY_MPS2 if normal_force_n is None else normal_force_n
        limit = self.friction * max(normal, 0.0)
        magnitude = math.hypot(*force)
        if magnitude > limit > 0.0:
            scale = limit / magnitude
            return (force[0] * scale, force[1] * scale)
        if limit == 0.0:
            return (0.0, 0.0)
        return force

    def stop_x(self) -> float:
        """Unde se oprește centrul unei piese pe banda asta, când o atinge opritorul.

        Cota vine din `layout`, care o calculează din ținta de preluare și din așezarea
        opritorului: o singură sursă pentru desen și pentru transport.
        """
        return layout.belt_stop_x(self.key)


def belts() -> tuple[Belt, ...]:
    """Benzile celulei, cu amprenta lor din plan."""
    return tuple(
        Belt(key, BELT_DIRECTIONS[key], footprint=layout.belt_bounds(item))
        for key, item in (("belt_in", layout.BELT_IN), ("belt_out", layout.BELT_OUT))
    )


def belt(key: str) -> Belt:
    """Banda cu cheia dată (``belt_in`` ori ``belt_out``)."""
    for item in belts():
        if item.key == key:
            return item
    raise KeyError(f"bandă necunoscută: {key}")


def belt_at(x: float, y: float, margin: float = 0.0) -> str | None:
    """Cheia benzii de sub un punct din plan, ori ``None`` dacă punctul e în afară.

    Testul este numai în plan: dacă piesa chiar stă pe bandă îl spune contactul, pe care îl
    verifică `cell.py`, singurul care are starea motorului.
    """
    for item in belts():
        if item.contains(x, y, margin):
            return item.key
    return None
