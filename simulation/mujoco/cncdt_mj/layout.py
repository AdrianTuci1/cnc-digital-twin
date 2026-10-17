"""Așezarea în plan și înălțimile celulei: sursa unică pentru scena MuJoCo.

Coordonatele ``(x, y)`` **nu** se transcriu aici. Se importă din ``docs/diagrams/cell_layout.py``,
fiindcă acolo stă singura sursă de adevăr a desenului, iar `docs/diagrams/README.md` promite că
planul și scena nu se pot contrazice. Înălțimile ``Z`` vin din `docs/11-geaman-mujoco.md` §2, paragraful „Înălțimile propuse"
și sunt trecute mai jos ca :data:`DOCUMENTED_HEIGHTS`, cu propoziția din documentație de care
aparțin; testul din `tests/unit/test_mujoco_scene.py` verifică exact potrivirea.

Modulul nu importă MuJoCo (ADR 0012), deci se poate testa în `.venv`-ul proiectului. Tot ce ține
de geometria sculei — cât de groasă e platforma benzii, cât de subțire e blatul raftului — se
calculează tot aici, ca `scene/cell.xml` să nu conțină niciun număr care vine din plan.
"""

from __future__ import annotations

import math
import sys
from typing import Any, Mapping, NamedTuple

from . import REPO_ROOT

DIAGRAMS_DIR = REPO_ROOT / "docs" / "diagrams"
if str(DIAGRAMS_DIR) not in sys.path:
    sys.path.insert(0, str(DIAGRAMS_DIR))

import cell_layout  # noqa: E402  (importul cere calea de mai sus)



CELL_WIDTH_M = float(cell_layout.CELL_W)
CELL_DEPTH_M = float(cell_layout.CELL_H)

#: Componentele din plan, preluate ca atare. `cell_layout.py` rămâne sursa lor.
BELT_IN: Mapping[str, Any] = cell_layout.BELT_IN
BELT_OUT: Mapping[str, Any] = cell_layout.BELT_OUT
MACHINE: Mapping[str, Any] = cell_layout.CNC
RACK: Mapping[str, Any] = cell_layout.RACK
SCRAP_BIN: Mapping[str, Any] = cell_layout.SCRAP_BIN
ARMS: tuple[Mapping[str, Any], ...] = tuple(cell_layout.ARMS)
TARGETS: tuple[Mapping[str, Any], ...] = tuple(cell_layout.TARGETS)
FLOW: tuple[tuple[float, float], ...] = tuple(cell_layout.FLOW)

#: Compartimentul fiecărei familii: numele familiei de piese -> numele țintei de prindere.
RACK_TARGETS: Mapping[str, str] = dict(cell_layout.RACK_TARGETS)

#: Familiile, în ordinea compartimentelor din plan, de la vest la est.
RACK_FAMILIES: tuple[str, ...] = tuple(cell_layout.RACK_FAMILIES)

#: Raza de atingere a brațului, în plan, din tabelul de coordonate.
ARM_REACH_M = float(ARMS[0]["reach"])

#: Direcția de mers a fiecărei benzi, în plan, ca vector unitar: banda 1 alimentează spre est,
#: banda 2 evacuă spre vest (`docs/11-geaman-mujoco.md` §2). Direcția este un fapt de așezare, nu
#: de fizică, deci stă aici, iar `cncdt_mj/belt.py` o folosește ca atare.
BELT_DIRECTIONS: Mapping[str, tuple[float, float]] = {
    "belt_in": (1.0, 0.0),
    "belt_out": (-1.0, 0.0),
}

#: Ținta de preluare a fiecărei benzi: locul unde se oprește piesa și de unde o ridică brațul.
BELT_STOP_TARGETS: Mapping[str, str] = {"belt_in": "belt1_pick", "belt_out": "belt2_pick"}

#: Câte rafturi are dulapul, din plan. Rafturile sunt suprapuse, iar fiecare e despărțit în două
#: compartimente de un panou vertical.
RACK_SHELVES = int(RACK["levels"])
RACK_COLUMNS = int(RACK["columns"])

#: Lățimea nominală a unui compartiment, din plan: 0,5 m, o celulă de grid.
RACK_ENTRY_M = float(cell_layout.RACK_ENTRY_M)

#: Adâncimea totală a dulapului, din plan: 0,30 m, cu fața deschisă la x = 3,18 m. Cei 0,30 m
#: se întind spre est, în afara anvelopei brațului; dacă fața se mută spre braț, verigile ating
#: marginea raftului de deasupra.
RACK_DEPTH_M = float(cell_layout.RACK_DEPTH_M)

#: Cota fiecărei podele de raft, de jos în sus, din plan.
RACK_FLOORS: tuple[float, ...] = tuple(float(value) for value in cell_layout.RACK_FLOORS)

#: Cota feței de sus a dulapului, din plan.
RACK_TOP_Z = float(cell_layout.RACK_TOP_Z)

#: Compartimentele dulapului, așa cum le dă planul: raftul, coloana, familia și podeaua.
RACK_ENTRIES: tuple[Mapping[str, Any], ...] = tuple(cell_layout.RACK_ENTRIES)

#: Compartimentele dulapului, în ordinea de umplere din plan. Cinci au câte o familie; al șaselea
#: rămâne gol, ca în dulapul real.
RACK_SLOT_TARGETS: tuple[str, ...] = tuple(RACK_TARGETS[family] for family in RACK_FAMILIES)

# ------------------------------------------------------------------ înălțimile documentate

#: Suprafața de lucru a benzilor: `docs/11-geaman-mujoco.md` §2.
BELT_SURFACE_Z = 0.85
#: Masa mașinii CNC: `docs/11-geaman-mujoco.md` §2.
MACHINE_TABLE_Z = 0.75
#: Capacul de protecție: `docs/11-geaman-mujoco.md` §2.
MACHINE_COVER_Z = 1.90
#: Podeaua raftului de jos, suprafața pe care stau piesele din compartimentele de jos:
#: `docs/11-geaman-mujoco.md` §2. Stă la nivelul suprafeței benzii, ca piesa să treacă din bandă în
#: dulap fără să urce ori să coboare. Rafturile de deasupra urcă la 0,31 m și la 0,25 m peste ea
#: (:data:`RACK_FLOORS`); un dulap adânc cu rafturi aliniate nu se poate încărca de sus, fiindcă
#: antebrațul brațului 2 mătură prin volumul lui.
RACK_SHELF_Z = RACK_FLOORS[0]
#: Marginea lăzii de rebut, sub capătul de vest al benzii 2: `docs/11-geaman-mujoco.md` §2. Cota
#: stă sub nivelul benzii, ca piesa care cade de la capătul benzii să treacă peste margine.
SCRAP_BIN_RIM_Z = 0.62
#: Baza brațelor, pe piedestal: `docs/11-geaman-mujoco.md` §2. `view.py --base-z` o schimbă
#: pentru măsurători, dar cota din scenă este cea documentată. Cota a fost 0,55 m până s-a măsurat
#: în scenă: acolo brațul 2 nu putea ridica piesa de pe banda 2, fiindcă antebrațul lui coboară sub
#: platformă, iar platforma rămâne deasupra sculei (`arm2_link3↔belt_out`, 106 mm eroare rămasă).
#: La 0,95 m ciclul se încheie, iar anvelopa brațelor urcă până la ~1,63 m, adică peste capacul
#: mașinii; celula nu are deci nicio structură care să privească piesa de sus, iar verdictul îl dă
#: camera de pe flanșa brațului 2, care fotografiază piesa ținută în falci.
ARM_BASE_Z = 0.95


class DocumentedHeight(NamedTuple):
    """O înălțime din scenă, plus fragmentul din documentație care o fixează.

    ``keyword`` este rădăcina de cuvânt căutată înaintea lui „la <valoare> m"; testul o
    transformă în expresie regulată, ca să nu depindă de forma exactă a propoziției.
    """

    name: str
    value: float
    keyword: str
    quote: str


DOCUMENTED_HEIGHTS: tuple[DocumentedHeight, ...] = (
    DocumentedHeight("belt_surface", BELT_SURFACE_Z, "benz", "suprafețele de lucru ale benzilor"),
    DocumentedHeight("machine_table", MACHINE_TABLE_Z, "mașin", "masa mașinii"),
    DocumentedHeight("machine_cover", MACHINE_COVER_Z, "capac", "capacul de protecție"),
    DocumentedHeight("rack_shelf", RACK_SHELF_Z, "raft", "podeaua raftului de jos"),
    DocumentedHeight("rack_entry", RACK_ENTRY_M, "compartiment", "lățimea compartimentului"),
    DocumentedHeight("scrap_bin_rim", SCRAP_BIN_RIM_Z, "lăz", "marginea lăzii de rebut"),
    DocumentedHeight("arm_base", ARM_BASE_Z, "piedestal", "baza brațelor pe piedestal"),
)


def documented_height(name: str) -> float:
    """Cota din documentație pentru o înălțime numită (``"arm_base"``, ``"belt_surface"``...).

    Snapshot-ul din :data:`DOCUMENTED_HEIGHTS` se ia la import, deci rămâne cota documentată și
    când `view.py --base-z` ridică temporar constanta, pentru măsurători.
    """
    return next(height.value for height in DOCUMENTED_HEIGHTS if height.name == name)

# ------------------------------------------------------------------ secțiuni de desen

# Cifrele de mai jos nu vin din plan și nu vin din documentație: sunt grosimi și secțiuni alese
# aici, ca piesa să stea pe ceva. Sunt singurele locuri unde apar.

BELT_SLAB_HALF_Z = 0.025
BELT_RAIL_HALF_Y = 0.02
BELT_RAIL_HALF_Z = 0.03
BELT_RAIL_GAP = 0.005
BELT_LEG_HALF_XY = 0.025
BELT_LEG_INSET = 0.15

MACHINE_TABLE_HALF_Z = 0.025
MACHINE_TABLE_OVERHANG = 0.02
MACHINE_POST_HALF_XY = 0.03
MACHINE_POST_INSET = 0.06
MACHINE_ROOF_HALF_Z = 0.025
MACHINE_CHUCK_RADIUS = 0.055
MACHINE_CHUCK_HALF_Z = 0.04

#: Dulapul pe familii: carcasa are laterale și spate, un fund și un capac, iar înăuntru stau trei
#: rafturi despărțite în două de un panou vertical. Fața nordică rămâne deschisă, fiindcă pe acolo
#: intră scula; dulapul are 0,30 m adâncime, dar cei 0,12 m din spate stau în afara anvelopei
#: brațului. Sunt secțiuni de desen, nu cifre din plan ori din documentație.
RACK_SIDE_HALF_THICKNESS = 0.015
RACK_SHELF_HALF_Z = 0.006
RACK_DIVIDER_HALF_THICKNESS = 0.015
#: Rafturile se opresc la fața din spate a carcasei, nu în ea.
RACK_SHELF_BACK_MARGIN = RACK_SIDE_HALF_THICKNESS

#: Lada de rebut: podea și trei pereți, cu latura de est deschisă, sub capătul de vest al benzii 2.
SCRAP_BIN_WALL_HALF_THICKNESS = 0.01
SCRAP_BIN_FLOOR_HALF_Z = 0.025

#: Baza brațului are „0,25 Ø" în tabelul de coordonate (`cell_layout.py`).
ARM_PEDESTAL_RADIUS = 0.125

CELL_MARKER_HALF = 0.015
CELL_MARKER_HALF_Z = 0.005


def center(item: Mapping[str, Any]) -> tuple[float, float]:
    """Centrul amprentei unui element din plan, în metri."""
    return (float(item["x"]) + float(item["w"]) / 2, float(item["y"]) + float(item["h"]) / 2)


def belt_bounds(item: Mapping[str, Any], margin: float = 0.0) -> tuple[float, float, float, float]:
    """Amprenta în care încape o piesă pe o bandă: ``(x0, x1, y0, y1)``, strânsă cu ``margin``.

    ``margin`` este raza piesei plus o urmă de siguranță, ca discul să stea întreg pe bandă chiar
    și la capătul intervalului. Intervalul rămâne oricum în interiorul amprentei din plan; nimic
    nu se lărgește.
    """
    return (
        float(item["x"]) + margin,
        float(item["x"]) + float(item["w"]) - margin,
        float(item["y"]) + margin,
        float(item["y"]) + float(item["h"]) - margin,
    )


def arm(key: str) -> Mapping[str, Any]:
    """Brațul cu cheia dată (``arm1`` ori ``arm2``)."""
    for item in ARMS:
        if item["key"] == key:
            return item
    raise KeyError(f"braț necunoscut: {key}")


def target(name: str) -> Mapping[str, Any]:
    """Ținta de prindere cu numele dat."""
    for item in TARGETS:
        if item["name"] == name:
            return item
    raise KeyError(f"țintă necunoscută: {name}")


def targets_of(key: str) -> tuple[Mapping[str, Any], ...]:
    """Țintele pe care le deservește un braț."""
    return tuple(item for item in TARGETS if item["key"] == key)


def rack_target(family: str) -> str:
    """Numele țintei de prindere a locului familiei date.

    Locul fiecărei familii este o țintă ca oricare alta, fiindcă acolo ajunge piesa: planul le
    desenează, iar brațul 2 le deservește pe toate cinci.
    """
    try:
        return RACK_TARGETS[family]
    except KeyError:
        raise KeyError(f"familie fără loc în raft: {family}") from None


def rack_entry(index: int) -> Mapping[str, Any]:
    """Intrarea cu numărul dat din plan: 1 este prima din ordinea de umplere."""
    return cell_layout.rack_entry(index)


def rack_slot_index(name: str) -> int:
    """Compartimentul unei ținte din dulap, în ordinea de umplere (0 = primul)."""
    return int(target(name)["slot"])


def rack_shelf_of(name: str) -> int:
    """Raftul unei ținte din dulap, numerotat de jos în sus, de la 1."""
    return int(rack_entry(rack_slot_index(name) + 1)["shelf"])


def rack_column_of(name: str) -> int:
    """Compartimentul unei ținte pe lungimea dulapului: 1 la sud, 2 la nord."""
    return int(rack_entry(rack_slot_index(name) + 1)["column"])


def rack_floor_z() -> float:
    """Cota podelei raftului de jos, nivelul de la care urcă celelalte."""
    return RACK_SHELF_Z


def rack_entry_floor(index: int) -> float:
    """Cota podelei compartimentului dat, din plan."""
    return float(cell_layout.rack_entry_floor(index))


def target_floor_z(name: str) -> float:
    """Cota podelei raftului pe care stă ținta dată, dacă e o țintă din dulap."""
    return rack_entry_floor(rack_slot_index(name) + 1)


def rack_shelf_span(shelf: int) -> tuple[float, float]:
    """Felia din lungimea dulapului pe care stă raftul dat, în lume (``x0``, ``x1``)."""
    if not 1 <= shelf <= RACK_SHELVES:
        raise KeyError(f"raft inexistent în dulap: {shelf}")
    return tuple(cell_layout.rack_shelf_span(shelf))  # type: ignore[return-value]


def rack_entry_center(index: int) -> tuple[float, float]:
    """Punctul de așezare al piesei în compartimentul dat, în lume."""
    return tuple(cell_layout.rack_entry_center(index))  # type: ignore[return-value]


def target_z(name: str) -> float:
    """Cota punctului de prindere, din înălțimile documentate.

    Planul dă doar desenul de sus, deci cota fiecărei ținte o fixează scena: benzile se prind de
    pe suprafața lor de lucru, locurile raftului de pe podeaua nivelului lor
    (`docs/11-geaman-mujoco.md` §2), iar dispozitivul mașinii la înălțimea universalului, pe masa
    mașinii.
    """
    if name in ("belt1_pick", "belt2_drop", "belt2_pick"):
        return BELT_SURFACE_Z
    if name == "cnc_chuck":
        return MACHINE_TABLE_Z + MACHINE_CHUCK_HALF_Z
    if name in RACK_TARGETS.values():
        return target_floor_z(name)
    raise KeyError(f"țintă fără cotă cunoscută: {name}")


def target_distance(name: str) -> float:
    """Distanța în spațiu, în metri, de la baza brațului la ținta lui.

    Planul măsoară doar în plan, iar `docs/11-geaman-mujoco.md` §2 spune explicit că o țintă
    poate părea la îndemână în desen și să fie prea sus. Verificarea de aici este cea în spațiu,
    făcută din cifrele planului; cea pe corpurile reale ale scenei compilate o face
    `view.py --check`.
    """
    item = target(name)
    base = arm(str(item["key"]))
    dx = float(item["x"]) - float(base["x"])
    dy = float(item["y"]) - float(base["y"])
    dz = target_z(name) - ARM_BASE_Z
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def reach_ok(name: str) -> bool:
    """Dacă ținta cade în sfera de atingere a brațului, măsurată în spațiu."""
    return target_distance(name) <= ARM_REACH_M


class PartPlacement(NamedTuple):
    """Locul unei piese în scenă, așezată pe suprafața unei benzi.

    ``state`` spune ce stare a materialului se vede acolo: blocul brut care intră în mașină sau
    piesa mașinată care iese din ea. Sunt două momente ale aceluiași flux, nu două piese
    diferite.
    """

    body: str
    state: str
    x: float
    y: float
    surface_z: float
    bounds: tuple[float, float, float, float]
    note: str


#: Stările de material pe care le arată scena.
STATE_BLANK = "blank"
STATE_MACHINED = "machined"


def part_placements() -> tuple[PartPlacement, ...]:
    """Cele două stări ale materialului, în locurile lor nominale din plan.

    Blocul brut stă pe banda 1, la linia ei de oprire (`belt_stop_x`): acolo ajunge piesa
    transportată, iar ce rămâne aleator este cota laterală și rotația. Piesa mașinată stă pe
    banda 2, la jumătatea drumului dintre locul unde o lasă brațul 1 și linia de unde o ridică
    brațul 2, deci în raza amândurora.
    """
    pick = target("belt1_pick")
    drop = target("belt2_drop")
    stop = target("belt2_pick")
    halfway = (float(drop["x"]) + float(stop["x"])) / 2
    return (
        PartPlacement("blank_01", STATE_BLANK, belt_stop_x("belt_in"), float(pick["y"]),
                      BELT_SURFACE_Z, belt_bounds(BELT_IN),
                      'adusă de bandă până la ținta „' + str(pick["label"]) + '" din plan'),
        PartPlacement("part_01", STATE_MACHINED, halfway, center(BELT_OUT)[1],
                      BELT_SURFACE_Z, belt_bounds(BELT_OUT),
                      "banda 2, la jumătatea drumului dintre așezare și preluare"),
    )


# ------------------------------------------------------------------ geometria sculei


def belt_geometry(key: str, item: Mapping[str, Any]) -> dict[str, float]:
    """Pozițiile și dimensiunile unei benzi, cu platforma sus la :data:`BELT_SURFACE_Z`."""
    cx, cy = center(item)
    hx, hy = float(item["w"]) / 2, float(item["h"]) / 2
    slab_z = BELT_SURFACE_Z - BELT_SLAB_HALF_Z
    leg_top = BELT_SURFACE_Z - 2 * BELT_SLAB_HALF_Z
    leg_hz = leg_top / 2
    return {
        f"{key}_cx": cx,
        f"{key}_cy": cy,
        f"{key}_hx": hx,
        f"{key}_hy": hy,
        f"{key}_slab_z": BELT_SURFACE_Z - BELT_SLAB_HALF_Z,
        f"{key}_rail_dy": hy - BELT_RAIL_GAP - BELT_RAIL_HALF_Y,
        f"{key}_leg_dx": hx - BELT_LEG_INSET,
        f"{key}_leg_dy": hy - BELT_LEG_INSET,
        "belt_slab_hz": BELT_SLAB_HALF_Z,
        "belt_rail_hy": BELT_RAIL_HALF_Y,
        "belt_rail_hz": BELT_RAIL_HALF_Z,
        "belt_rail_dz": BELT_SLAB_HALF_Z + BELT_RAIL_HALF_Z - 0.01,
        "belt_leg_hxy": BELT_LEG_HALF_XY,
        "belt_leg_hz": leg_hz,
        # Picioarele merg de la pardoseală până sub platformă: nu intră în podea, nu plutesc.
        "belt_leg_dz": leg_hz - slab_z,
    }


def belt_stop_x(key: str) -> float:
    """Linia de oprire a unei benzi: cota din lungul benzii de unde nu mai împinge piesa.

    Banda nu are opritor fizic, fiindcă nici celula reală nu are nevoie de unul: brațul prinde
    un disc, iar cota din lungul benzii nu trebuie să fie exactă. Banda transportă până la ținta
    de preluare, iar de acolo piesa alunecă singură până se oprește. Fără perete, piesa nu mai
    poate fi oprită de geometrie, deci regula stă într-un singur loc: aici, în plan, plus
    :meth:`cncdt_mj.belt.Belt.reached_stop`, care o folosește la fiecare pas.
    """
    return float(target(BELT_STOP_TARGETS[key])["x"])


def machine_geometry() -> dict[str, float]:
    """Corpul mașinii CNC, fără axe mobile: cabinet, masă, universal, capac."""
    cx, cy = center(MACHINE)
    hx, hy = float(MACHINE["w"]) / 2, float(MACHINE["h"]) / 2
    cabinet_hz = (MACHINE_TABLE_Z - MACHINE_TABLE_HALF_Z) / 2
    table_hz = MACHINE_TABLE_HALF_Z
    table_z = MACHINE_TABLE_Z - table_hz
    roof_hz = MACHINE_ROOF_HALF_Z
    roof_z = MACHINE_COVER_Z - roof_hz
    post_hz = (MACHINE_COVER_Z - MACHINE_TABLE_Z - 2 * MACHINE_ROOF_HALF_Z) / 2
    post_z = MACHINE_TABLE_Z + post_hz
    chuck = target("cnc_chuck")
    return {
        "cnc_cx": cx,
        "cnc_cy": cy,
        "cnc_hx": hx,
        "cnc_hy": hy,
        "cnc_cabinet_hz": cabinet_hz,
        "cnc_cabinet_z": cabinet_hz,
        "cnc_table_hx": hx + MACHINE_TABLE_OVERHANG,
        "cnc_table_hy": hy + MACHINE_TABLE_OVERHANG,
        "cnc_table_hz": table_hz,
        "cnc_table_z": table_z,
        "cnc_chuck_dx": float(chuck["x"]) - cx,
        "cnc_chuck_dy": float(chuck["y"]) - cy,
        "cnc_chuck_z": MACHINE_TABLE_Z + MACHINE_CHUCK_HALF_Z,
        "cnc_chuck_r": MACHINE_CHUCK_RADIUS,
        "cnc_chuck_hz": MACHINE_CHUCK_HALF_Z,
        "cnc_post_hxy": MACHINE_POST_HALF_XY,
        "cnc_post_dx": hx + MACHINE_TABLE_OVERHANG - MACHINE_POST_INSET,
        "cnc_post_dy": hy + MACHINE_TABLE_OVERHANG - MACHINE_POST_INSET,
        "cnc_post_hz": post_hz,
        "cnc_post_z": post_z,
        "cnc_roof_hx": hx + MACHINE_TABLE_OVERHANG,
        "cnc_roof_hy": hy + MACHINE_TABLE_OVERHANG,
        "cnc_roof_hz": roof_hz,
        "cnc_roof_z": roof_z,
    }


def rack_geometry() -> dict[str, float]:
    """Dulapul pe familii: carcasa, cele trei rafturi și despărțitorul din mijloc.

    Așezarea și cotele rafturilor ies din `docs/diagrams/cell_layout.py`, iar podeaua de jos din
    înălțimile documentate. Fața vestică rămâne deschisă; lateralele de nord și sud, spatele de
    est, fundul și capacul închid dulapul, iar rafturile se opresc la fața vestică. Dulapul are
    0,30 m adâncime pe est și 1,00 m lungime pe nord-sud, ca brațul să se rotească 90° de la
    banda 2 până la raft.
    """
    cx, cy = center(RACK)
    hx = float(RACK["w"]) / 2
    hy = float(RACK["h"]) / 2
    side = RACK_SIDE_HALF_THICKNESS
    board = RACK_SHELF_HALF_Z
    # Rafturile pornesc de la fața vestică deschisă și merg până la fața din spate a carcasei.
    shelf_hx = hx - RACK_SHELF_BACK_MARGIN
    shelf_dx = -RACK_SHELF_BACK_MARGIN
    divider_z0 = RACK_FLOORS[0]
    divider_z1 = RACK_TOP_Z - 2 * board
    values: dict[str, float] = {
        "rack_cx": cx,
        "rack_cy": cy,
        "rack_hx": hx,
        "rack_hy": hy,
        # Lateralele de nord și sud și spatele de est urcă până la fața de sus.
        "rack_side_hx": hx,
        "rack_side_hy": side,
        "rack_side_hz": RACK_TOP_Z / 2,
        "rack_side_z": RACK_TOP_Z / 2,
        "rack_side_south_dy": -(hy - side),
        "rack_side_north_dy": hy - side,
        "rack_back_hx": side,
        "rack_back_hy": hy,
        "rack_back_hz": RACK_TOP_Z / 2,
        "rack_back_z": RACK_TOP_Z / 2,
        "rack_back_dx": hx - side,
        # Fundul și capacul, între laterale.
        "rack_board_hx": hx,
        "rack_board_hy": hy,
        "rack_board_hz": board,
        "rack_bottom_z": board,
        "rack_top_z": RACK_TOP_Z - board,
        # Rafturile, cu fața de sus chiar la cota podelei.
        "rack_shelf_hx": shelf_hx,
        "rack_shelf_hy": hy,
        "rack_shelf_hz": board,
        "rack_shelf_dx": shelf_dx,
        # Despărțitorul dintre cele două compartimente, pe toată înălțimea.
        "rack_divider_hx": shelf_hx,
        "rack_divider_hy": RACK_DIVIDER_HALF_THICKNESS,
        "rack_divider_hz": (divider_z1 - divider_z0) / 2,
        "rack_divider_z": (divider_z1 + divider_z0) / 2,
        "rack_divider_dx": shelf_dx,
    }
    for number, floor in enumerate(RACK_FLOORS, start=1):
        values[f"rack_shelf_{number}_z"] = floor - board

    for item in cell_layout.RACK_ENTRIES:
        index = int(item["index"])
        ex, ey = cell_layout.rack_entry_center(index)
        values[f"rack_e{index}_dx"] = ex - cx
        values[f"rack_e{index}_dy"] = ey - cy
        values[f"rack_e{index}_floor"] = rack_entry_floor(index)
    return values


def scrap_bin_geometry() -> dict[str, float]:
    """Lada de rebut: podea și trei pereți, cu marginea sus la :data:`SCRAP_BIN_RIM_Z`.

    Latura de est rămâne deschisă: piesa cade de la capătul de vest al benzii 2, iar un perete
    chiar sub capătul benzii ar prinde-o și ar ține-o afară din ladă.
    """
    cx, cy = center(SCRAP_BIN)
    hx, hy = float(SCRAP_BIN["w"]) / 2, float(SCRAP_BIN["h"]) / 2
    half_thickness = SCRAP_BIN_WALL_HALF_THICKNESS
    floor_hz = SCRAP_BIN_FLOOR_HALF_Z
    wall_hz = (SCRAP_BIN_RIM_Z - 2 * floor_hz) / 2
    return {
        "scrap_bin_cx": cx,
        "scrap_bin_cy": cy,
        "scrap_bin_hx": hx,
        "scrap_bin_hy": hy,
        "scrap_bin_wall_dx": hx - half_thickness,
        "scrap_bin_wall_dy": hy - half_thickness,
        "scrap_bin_wall_hy_long": hy - 2 * half_thickness,
        "scrap_bin_wall_hx_long": hx - 2 * half_thickness,
        "scrap_bin_floor_hz": floor_hz,
        "scrap_bin_floor_z": floor_hz,
        "scrap_bin_wall_hxy": half_thickness,
        "scrap_bin_wall_hz": wall_hz,
        "scrap_bin_wall_z": 2 * floor_hz + wall_hz,
    }


def arm_geometry(key: str) -> dict[str, float]:
    """Piedestalul unui braț, cu vârful la :data:`ARM_BASE_Z`."""
    item = arm(key)
    return {
        f"{key}_x": float(item["x"]),
        f"{key}_y": float(item["y"]),
        f"{key}_z": ARM_BASE_Z,
        "arm_pedestal_r": ARM_PEDESTAL_RADIUS,
        "arm_pedestal_hz": ARM_BASE_Z / 2,
        "arm_pedestal_dz": -ARM_BASE_Z / 2,
    }


def cell_geometry() -> dict[str, float]:
    """Cadrul celulei, desenat ca patru șipci subțiri pe pardoseală."""
    return {
        "cell_w": CELL_WIDTH_M,
        "cell_h": CELL_DEPTH_M,
        "cell_cx": CELL_WIDTH_M / 2,
        "cell_cy": CELL_DEPTH_M / 2,
        "cell_half_w": CELL_WIDTH_M / 2,
        "cell_half_h": CELL_DEPTH_M / 2,
        "cell_marker_half": CELL_MARKER_HALF,
        "cell_marker_half_z": CELL_MARKER_HALF_Z,
        "cell_marker_z": CELL_MARKER_HALF_Z,
    }


def scene_geometry() -> dict[str, float]:
    """Toate cifrele pe care `scene/cell.xml` le primește ca valori de substituție."""
    values: dict[str, float] = {}
    values.update(cell_geometry())
    values.update(belt_geometry("belt_in", BELT_IN))
    values.update(belt_geometry("belt_out", BELT_OUT))
    values.update(machine_geometry())
    values.update(rack_geometry())
    values.update(scrap_bin_geometry())
    for item in ARMS:
        values.update(arm_geometry(item["key"]))
    return values
