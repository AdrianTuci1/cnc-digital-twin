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
#: ține brațul departe de despărțitoarele raftului și de masa mașinii, iar coborârea rămâne o
#: mișcare controlată.
APPROACH_HEIGHT_M = 0.12

#: Scula vine de sus în toate punctele de lucru: axul universalului este vertical, iar mașina are
#: cadrul deschis deasupra mesei, benzile au șinele dincolo de falci, iar raftul este deschis.
FROM_ABOVE: tuple[float, float, float] = (0.0, 0.0, -1.0)


def half_thickness_m(name: str, family: str | None = None) -> float:
    """Jumătatea grosimii materialului, în metri, în punctul de lucru.

    Nu e aceeași peste tot: pe banda 1 stă blocul brut al familiei (cu adaosul lui de prelucrare),
    iar în universal, pe banda 2 și pe raft, piesa mașinată. Grosimea depinde deci de familie.
    """
    if name == "belt1_pick":
        return part.blank_half_thickness_m(family)
    return part.gear_half_thickness_m(family)


#: Suprafața pe care stă piesa în fiecare punct de lucru: podeaua treptei din raft, platforma
#: benzilor, masa mașinii. Piesa se așază direct pe ea, deci nu se eliberează deasupra unei buze.
SURFACE_Z: Mapping[str, float] = {
    "belt1_pick": layout.BELT_SURFACE_Z,
    "belt2_drop": layout.BELT_SURFACE_Z,
    "belt2_pick": layout.BELT_SURFACE_Z,
    "cnc_chuck": layout.MACHINE_TABLE_Z + 2 * layout.MACHINE_CHUCK_HALF_Z,
    **{name: layout.target_z(name) for name in layout.RACK_TARGETS.values()},
}


def route_names(arm: str, family: str | None = None) -> tuple[str, ...]:
    """Punctele unui braț, în ordinea ciclului, pentru familia dată.

    Brațul 1 ia blocul de pe banda 1, îl duce în universal și lasă roata pe banda 2. Brațul 2
    preia roata de pe banda 2 și o așază în compartimentul familiei ei: acolo intră verdictul, iar
    piesa neconformă nu are punct de lucru, fiindcă rămâne pe bandă până la capătul ei.
    """
    if arm == "arm1":
        return ("belt1_pick", "cnc_chuck", "belt2_drop")
    if arm == "arm2":
        # Fără o familie cerută, ruta este cea a familiei de referință: desenul și testele au
        # nevoie de o rută nominală, iar ciclul cere familia fiecărei piese.
        return ("belt2_pick", layout.rack_target(family or part.family().name))
    raise KeyError(f"braț necunoscut: {arm}")


#: Punctele nominale ale fiecărui braț: ruta familiei de referință, ca desenul și testele să aibă
#: o rută fără să ceară o familie.
ROUTES: Mapping[str, tuple[str, ...]] = {arm: route_names(arm) for arm in ("arm1", "arm2")}

#: Rotirea sculei în jurul axei de apropiere, în grade, în fiecare punct de lucru. Piesa este un
#: disc, deci rotirea nu schimbă prinderea, iar falcile se deschid nord-sud după convenția din
#: `arms.tool_quat`. Pe raft, deschiderea nord-sud este cea bună: intrarea de 0,50 m este un cub
#: deschis, iar marginile ei stau dincolo de falci.
TOOL_SPIN_DEG: Mapping[str, float] = {}

#: Cota la care scula călătorește pe deasupra raftului, înainte de a coborî în intrare. Fără ea,
#: drumul cel mai scurt de la preluarea de pe banda 2 până la o intrare taie rândul din spate la
#: cota de lucru a benzii, iar falcile agață buzele intrării vecine: măsurat în scenă, mâna se
#: înțepena în raft și scula rămânea cu 280 mm de țintă, cu 34° de răsucire. Cota stă cu 0,23 m
#: deasupra buzelor intrărilor (0,87 m) și ține brațul în raza lui comodă: la intrarea cea mai
#: depărtată, flanșa ajunge la 0,785 m din 0,855 m, pe când de la 1,25 m ar sta la 0,846 m, adică
#: 99 % din rază, iar de acolo servo-urile lasă scula cu 20-30 mm în urmă, peste pragul de 20 mm.
#: Coborârea în intrare este verticală, deci marginile intrării rămân dincolo de falci.
RACK_VIA_Z = 1.10


def _rack_vias() -> dict[str, tuple[tuple[float, float, float], ...]]:
    """Punctul de trecere al fiecărei intrări din raft: chiar deasupra intrării, la cota de călătorie.

    Intrarea are 0,50 m latură și este deschisă deasupra, deci locul de deasupra ei este chiar gura
    pe unde intră scula: ultima bucată a drumului spre intrare coboară pe verticală.
    """
    vias = {}
    for name in layout.RACK_TARGETS.values():
        item = layout.target(name)
        vias[name] = ((float(item["x"]), float(item["y"]), RACK_VIA_Z),)
    return vias


#: Punctele prin care trece scula pe drumul spre un punct de lucru, la cota de călătorie. Fără ele,
#: drumul cel mai scurt dintre două puncte taie colțul celulei: de la universal spre așezarea pe
#: banda 2, dreapta iese din mașină exact prin colțul ei sud-vestic, iar brațul — care nu are
#: planificator de traiectorie — intră în carcasă. Măsurat în scenă la înălțimea ridicată a
#: piedestalului: fără punctul de trecere, scula rămânea cu 880 mm de țintă, cu mâna pe mașină;
#: cu el, eroarea maximă a ciclului este 17,7 mm. Punctul stă la vest de mașină, la 1,10 m, adică
#: deasupra capacului meselor benzilor și în afara amprentei mașinii.
VIA: Mapping[str, tuple[tuple[float, float, float], ...]] = {
    "belt2_drop": ((3.10, 2.95, 1.10),),
    **_rack_vias(),
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

    #: Familia de piese cu care se lucrează în punctul de lucru. Grosimea materialului, deci și
    #: cota centrului piesei, depinde de ea.
    family: str | None = None

    #: Punctele de trecere de dinaintea apropierii propriu-zise (`VIA`).
    via: tuple[tuple[float, float, float], ...] = ()

    @property
    def hover(self) -> tuple[float, float, float]:
        """Punctul de deasupra, de unde începe coborârea pe punctul de lucru."""
        x, y, z = self.point
        ax, ay, az = self.approach
        height = approach_height(self.name, self.tool_length_m, self.family)
        return (x - ax * height, y - ay * height, z - az * height)


def work_point(name: str, family: str | None = None) -> tuple[float, float, float]:
    """Centrul piesei într-un punct de lucru din plan, la cota lui documentată."""
    item = layout.target(name)
    return (float(item["x"]), float(item["y"]),
            SURFACE_Z[name] + half_thickness_m(name, family))


def approach_height(name: str, tool_length_m: float = 0.0,
                    family: str | None = None) -> float:
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
    point = np.asarray(work_point(name, family), dtype=float)
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


def move(name: str, tool_length_m: float = 0.0, family: str | None = None) -> Move:
    """Punctul de lucru cu numele dat, gata de urmat de un braț."""
    item = layout.target(name)
    return Move(arm=str(item["key"]), name=name, label=str(item["label"]),
                approach=FROM_ABOVE, point=work_point(name, family),
                tool_length_m=tool_length_m, spin_deg=TOOL_SPIN_DEG.get(name, 0.0),
                family=family, via=VIA.get(name, ()))


def stages(item: Move) -> tuple[tuple[str, tuple[float, float, float]], ...]:
    """Drumul unui punct de lucru: trecerile, urcarea deasupra, coborârea pe el și ridicarea.

    Ridicarea de la urmă are rolul ei: scoate scula de la cota de lucru înainte de plecarea spre
    punctul următor, altfel drumul cel mai scurt pornește de jos și mătură banda. Măsurat în scenă:
    fără ea, blocul brut era împins de pe banda 1 până pe podea.
    """
    travels = tuple((f"trecere {index}", point) for index, point in enumerate(item.via, start=1))
    return travels + (("sus", item.hover), ("jos", item.point), ("ridicare", item.hover))


def route(arm: str, tool_length_m: float = 0.0, family: str | None = None) -> tuple[Move, ...]:
    """Punctele unui braț, în ordinea ciclului; fiecare se atinge sus, apoi la cota de lucru."""
    return tuple(move(name, tool_length_m, family) for name in route_names(arm, family))
