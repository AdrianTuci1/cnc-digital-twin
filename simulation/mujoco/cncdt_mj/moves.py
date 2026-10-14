"""Mișcările celulei: ce puncte atinge fiecare braț, în ce ordine și din ce parte vine scula.

Punctele vin din plan (`layout.target`), cotele din înălțimile documentate, iar cota la care lucrează
scula este **centrul piesei**, nu suprafața pe care stă: de acolo o prinde griperul și tot acolo o
lasă. Modulul dă doar partea pură — puncte, ordine și direcții de apropiere; cum ajunge brațul
acolo (cinematică inversă, interpolare, prindere) este în `cncdt_mj/arms.py`, singurul care are
nevoie de motor. **Fără MuJoCo.**
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np

from . import layout
from . import part

#: Cât urcă scula deasupra punctului de lucru înainte de a coborî pe el, când are loc. Cursa scurtă
#: ține brațul departe de pereții cutiilor și de masa mașinii, iar coborârea rămâne o mișcare
#: controlată.
APPROACH_HEIGHT_M = 0.12

#: Cât lasă scula piesa deasupra suprafeței pe care o așază, înainte de a o elibera: piesa cade
#: singură ultimii centimetri, ca în celulă.
RELEASE_CLEARANCE_M = 0.01

#: Scula vine de sus în toate punctele de lucru: axul universalului este vertical, iar mașina are
#: cadrul deschis deasupra mesei, benzile au șinele dincolo de falci, iar cutiile sunt deschise.
FROM_ABOVE: tuple[float, float, float] = (0.0, 0.0, -1.0)

#: Grosimea materialului, în metri, în fiecare punct de lucru. Nu e aceeași peste tot: pe banda 1
#: stă blocul brut (18 mm), iar în universal și pe banda 2 roata mașinată (15 mm).
HALF_THICKNESS_M: Mapping[str, float] = {
    "belt1_pick": part.BLANK_THICKNESS_MM * part.MESH_SCALE / 2,
    "cnc_chuck": part.NOMINAL_GEAR.thickness * part.MESH_SCALE / 2,
    "belt2_drop": part.NOMINAL_GEAR.thickness * part.MESH_SCALE / 2,
    "belt2_pick": part.NOMINAL_GEAR.thickness * part.MESH_SCALE / 2,
    "bin_ok": part.NOMINAL_GEAR.thickness * part.MESH_SCALE / 2,
    "bin_ng": part.NOMINAL_GEAR.thickness * part.MESH_SCALE / 2,
}

#: Suprafața pe care stă piesa în fiecare punct de lucru.
SURFACE_Z: Mapping[str, float] = {
    "belt1_pick": layout.BELT_SURFACE_Z,
    "belt2_drop": layout.BELT_SURFACE_Z,
    "belt2_pick": layout.BELT_SURFACE_Z,
    "cnc_chuck": layout.MACHINE_TABLE_Z + 2 * layout.MACHINE_CHUCK_HALF_Z,
    # Piesa se lasă în cutie: se eliberează deasupra marginii, nu pe fundul cutiei.
    "bin_ok": layout.BIN_RIM_Z + RELEASE_CLEARANCE_M,
    "bin_ng": layout.BIN_RIM_Z + RELEASE_CLEARANCE_M,
}

#: Punctele pe care le deservește fiecare braț, în ordinea ciclului de lucru: brațul 1 ia blocul de
#: pe banda 1, îl duce în universal și lasă roata pe banda 2, iar brațul 2 preia roata de pe banda 2
#: și o așază în cutia verdictului. ``bin_ng`` este alternativa lui ``bin_ok``, după verdict, nu un
#: al treilea punct al rutei.
ROUTES: Mapping[str, tuple[str, ...]] = {
    "arm1": ("belt1_pick", "cnc_chuck", "belt2_drop"),
    "arm2": ("belt2_pick", "bin_ok"),
}

#: Rotirea sculei în jurul axei de apropiere, în grade, în fiecare punct de lucru. Piesa este un
#: disc, deci rotirea nu schimbă prinderea, iar falcile se deschid nord-sud după convenția din
#: `arms.tool_quat`: la cutii, acea deschidere pune o falcă deasupra benzii 2, fiindcă peretele
#: sudic al cutiei stă chiar pe marginea nordică a benzii. Cu scula rotită un sfert de tur, falcile
#: se deschid est-vest, adică de-a lungul cutiei, și coboară înăuntrul ei.
TOOL_SPIN_DEG: Mapping[str, float] = {
    "bin_ok": 90.0,
    "bin_ng": 90.0,
}

#: Punctele prin care trece scula pe drumul spre un punct de lucru, la cota de călătorie. Fără ele,
#: drumul cel mai scurt dintre două puncte taie colțul celulei: de la universal spre așezarea pe
#: banda 2, dreapta iese din mașină exact prin colțul ei sud-vestic, iar brațul — care nu are
#: planificator de traiectorie — intră în carcasă. Măsurat în scenă la înălțimea ridicată a
#: piedestalului: fără punctul de trecere, scula rămânea cu 880 mm de țintă, cu mâna pe mașină;
#: cu el, eroarea maximă a ciclului este 17,7 mm. Punctul stă la vest de mașină, la 1,10 m, adică
#: deasupra capacului meselor benzilor și în afara amprentei mașinii.
VIA: Mapping[str, tuple[tuple[float, float, float], ...]] = {
    "belt2_drop": ((3.10, 2.95, 1.10),),
}


@dataclass(frozen=True)
class Move:
    """Un punct de lucru atins de un braț, cu apropierea lui."""

    arm: str
    name: str
    label: str
    approach: tuple[float, float, float]

    #: Centrul piesei în punctul de lucru: acolo ajunge scula.
    point: tuple[float, float, float]

    #: Cât atârnă scula sub flanșă, adică distanța de la flanșă la punctul de strângere. Locul de
    #: deasupra punctului depinde de ea, iar valoarea se măsoară în scenă (`arms.pinch_length`),
    #: nu se scrie de mână.
    tool_length_m: float = 0.0

    #: Rotirea sculei în jurul direcției de apropiere, în grade (`TOOL_SPIN_DEG`).
    spin_deg: float = 0.0

    #: Punctele de trecere de dinaintea apropierii propriu-zise (`VIA`).
    via: tuple[tuple[float, float, float], ...] = ()

    @property
    def hover(self) -> tuple[float, float, float]:
        """Punctul de deasupra, de unde începe coborârea pe punctul de lucru."""
        x, y, z = self.point
        ax, ay, az = self.approach
        height = approach_height(self.name, self.tool_length_m)
        return (x - ax * height, y - ay * height, z - az * height)


def work_point(name: str) -> tuple[float, float, float]:
    """Centrul piesei într-un punct de lucru din plan, la cota lui documentată."""
    item = layout.target(name)
    return (float(item["x"]), float(item["y"]), SURFACE_Z[name] + HALF_THICKNESS_M[name])


def approach_height(name: str, tool_length_m: float = 0.0) -> float:
    """Cât se retrage scula dinspre punct, pe direcția de apropiere, rămânând în raza brațului.

    Raza de atingere se măsoară până la **flanșă**, iar scula atârnă cu ``tool_length_m`` între
    flanșă și punctul de lucru. Scriind ``s`` pentru distanța de la punct la flanșă pe direcția de
    apropiere, condiția ``|punct − s * direcție − bază| <= rază`` dă un interval pentru ``s``, iar
    capătul lui de sus este retragerea plus lungimea sculei. De aici iese cât are loc scula: la
    punctul de așezare pe banda 2, doar câțiva centimetri.

    Ridică :class:`ValueError` dacă nici punctul de lucru nu intră în rază cu scula de față: acolo
    nu e de ajustat retragerea, ci așezarea din plan, ori scula.
    """
    tool_length_m = abs(tool_length_m)
    direction = np.asarray(FROM_ABOVE, dtype=float)
    point = np.asarray(work_point(name), dtype=float)
    arm = layout.arm(str(layout.target(name)["key"]))
    base = np.array([float(arm["x"]), float(arm["y"]), layout.ARM_BASE_Z])
    relative = point - base
    along = float(relative @ direction)
    perpendicular = float(np.linalg.norm(relative - along * direction))
    reach = layout.ARM_REACH_M
    if perpendicular > reach:
        raise ValueError(
            f"punctul de lucru {name} este la {perpendicular:.3f} m de axa de apropiere, peste raza "
            f"de {reach:.3f} m")
    room = along + math.sqrt(reach * reach - perpendicular * perpendicular) - tool_length_m
    if room < 0.0:
        raise ValueError(
            f"punctul de lucru {name} este cu {abs(room) * 1000:.0f} mm în afara razei brațului, "
            f"cu scula de {tool_length_m * 1000:.0f} mm")
    return min(APPROACH_HEIGHT_M, room)


def move(name: str, tool_length_m: float = 0.0) -> Move:
    """Punctul de lucru cu numele dat, gata de urmat de un braț."""
    item = layout.target(name)
    return Move(arm=str(item["key"]), name=name, label=str(item["label"]),
                approach=FROM_ABOVE, point=work_point(name), tool_length_m=tool_length_m,
                spin_deg=TOOL_SPIN_DEG.get(name, 0.0), via=VIA.get(name, ()))


def stages(item: Move) -> tuple[tuple[str, tuple[float, float, float]], ...]:
    """Drumul unui punct de lucru: trecerile, urcarea deasupra, coborârea pe el și ridicarea.

    Ridicarea de la urmă are rolul ei: scoate scula de la cota de lucru înainte de plecarea spre
    punctul următor, altfel drumul cel mai scurt pornește de jos și mătură banda. Măsurat în scenă:
    fără ea, blocul brut era împins de pe banda 1 până pe podea.
    """
    travels = tuple((f"trecere {index}", point) for index, point in enumerate(item.via, start=1))
    return travels + (("sus", item.hover), ("jos", item.point), ("ridicare", item.hover))


def route(arm: str, tool_length_m: float = 0.0) -> tuple[Move, ...]:
    """Punctele unui braț, în ordinea ciclului; fiecare se atinge sus, apoi la cota de lucru."""
    try:
        names = ROUTES[arm]
    except KeyError:
        raise KeyError(f"braț necunoscut: {arm}; există {sorted(ROUTES)}") from None
    return tuple(move(name, tool_length_m) for name in names)
