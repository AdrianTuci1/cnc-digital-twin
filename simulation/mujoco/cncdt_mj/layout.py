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
CAMERAS: tuple[Mapping[str, Any], ...] = tuple(cell_layout.CAMERAS)
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

# ------------------------------------------------------------------ înălțimile documentate

#: Suprafața de lucru a benzilor: `docs/11-geaman-mujoco.md` §2.
BELT_SURFACE_Z = 0.85
#: Masa mașinii CNC: `docs/11-geaman-mujoco.md` §2.
MACHINE_TABLE_Z = 0.75
#: Capacul de protecție: `docs/11-geaman-mujoco.md` §2.
MACHINE_COVER_Z = 1.90
#: Podeaua raftului, adică suprafața pe care stau piesele din compartimente:
#: `docs/11-geaman-mujoco.md` §2. Stă la nivelul suprafeței benzii, ca piesa să treacă din bandă
#: pe raft fără să urce ori să coboare, iar brațul să coboare pe ea la fel ca pe bandă. De aici
#: vine și câștigul față de cutii: un raft deschis, cu despărțitoare scurte, nu are pereți de
#: 0,85 m în care să intre antebrațul.
RACK_SHELF_Z = BELT_SURFACE_Z
#: Marginea lăzii de rebut, sub capătul de vest al benzii 2: `docs/11-geaman-mujoco.md` §2. Cota
#: stă sub nivelul benzii, ca piesa care cade de la capătul benzii să treacă peste margine.
SCRAP_BIN_RIM_Z = 0.62
#: Baza brațelor, pe piedestal: `docs/11-geaman-mujoco.md` §2. Cota este cea din document, iar
#: `view.py --base-z` o ridică pentru măsurători. La 0,55 m brațele nu ajungeau în cutiile adânci
#: de dinainte, fiindcă pereții lor urcau până la 0,85 m (nivelul benzii), iar la 0,95 m ciclul se
#: încheia, dar anvelopa brațelor cuprindea capul camerei de inspecție. Raftul deschis mută
#: problema: nu mai are pereți înalți, deci cota se remăsoară cu `make mj-demo`, iar cifrele
#: rămase din măsurătoarea veche sunt în `README.md`.
ARM_BASE_Z = 0.55
#: Înălțimea documentată a camerelor, orientate în jos: `docs/11-geaman-mujoco.md` §2.
#: Camera de inspecție a calității este singura excepție, deliberată: vezi mai jos
#: :data:`INSPECTION_CAMERA_CLEARANCE_Z` și `README.md` pentru aritmetica din spate.
CAMERA_Z = 2.20

#: Cât stă camera de inspecție deasupra suprafeței benzii 2. La 2,20 m piesa de ~104 mm ar ocupa
#: vreo 10 pixeli, iar cel mai mic defect din catalog are 2,5 mm, deci nicio clasă nu ar fi
#: separabilă. Cota nu coboară însă sub anvelopa brațelor: măsurat în scenă pe plaja
#: articulațiilor, geomele unui braț urcă acolo până la ~1,63 m în lume, adică ~0,78 m peste banda
#: 2. Cu capul optic la 1,60 m, golul minim măsurat pe 2000 de configurații este −0,022 m (adică
#: suprapunere); la cota de față, 1,70 m, cel mai mic gol este +0,034 m. Vezi `README.md`,
#: „Camera de inspecție", și `view.py --check`.
INSPECTION_CAMERA_CLEARANCE_Z = 0.85

#: Câmpul vizual vertical al camerei de inspecție, în grade. De la 0,85 m, 9° acoperă tot
#: 134 mm pe verticală și, cu raportul de aspect 4:3, 179 mm pe orizontală — adică 3,6 px/mm la
#: 640x480, destul pentru un dinte ciobit de 4 mm. Când camera se ridică peste anvelopa
#: brațelor, obiectivul se îngustează: acoperirea, deci rezoluția pe piesă, rămâne aceeași.
INSPECTION_CAMERA_FOVY = 9.0

#: Rezoluția cadrelor de inspecție: cerută explicit de planul aprobat, în locul implicitului
#: 160x120 din `docs/11-geaman-mujoco.md` §2.
INSPECTION_CAMERA_FRAME = (640, 480)


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
    DocumentedHeight("rack_shelf", RACK_SHELF_Z, "raft", "podeaua raftului pe familii"),
    DocumentedHeight("scrap_bin_rim", SCRAP_BIN_RIM_Z, "lăz", "marginea lăzii de rebut"),
    DocumentedHeight("arm_base", ARM_BASE_Z, "piedestal", "baza brațelor pe piedestal"),
    DocumentedHeight("camera", CAMERA_Z, "camer", "camerele"),
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

#: Raftul pe familii: un blat subțire, despărțitoare scurte și patru picioare. Numărul
#: despărțitoarelor iese din plan (unul mai mult decât compartimentele), iar blatul stă la
#: :data:`RACK_SHELF_Z`. Buzunarele se deschid spre nord, adică spre brațul 2, deci raftul nu are
#: pereți decât scurți, cât să prindă piesa la așezare.
RACK_SLAB_HALF_Z = 0.015
RACK_DIVIDER_HALF_XY = 0.005
RACK_DIVIDER_HALF_Z = 0.025
RACK_LIP_HALF_Y = 0.005
RACK_LEG_HALF_XY = 0.02
RACK_LEG_INSET = 0.05

#: Lada de rebut: podea și trei pereți, cu latura de est deschisă, sub capătul de vest al benzii 2.
SCRAP_BIN_WALL_HALF_THICKNESS = 0.01
SCRAP_BIN_FLOOR_HALF_Z = 0.025

#: Baza brațului are „0,25 Ø" în tabelul de coordonate (`cell_layout.py:386`).
ARM_PEDESTAL_RADIUS = 0.125

#: Consola camerei de inspecție se prinde de colțul sud-vest al mașinii CNC și se așază pe
#: capacul ei, la :data:`MACHINE_COVER_Z`; capul optic atârnă de ea pe o tijă subțire, până la
#: :func:`inspection_camera_z`. Cotele de aici sunt secțiuni de desen, nu cifre din plan ori din
#: documentație; dacă se schimbă, se schimbă doar aici.
CAMERA_BOOM_HALF_XY = 0.04
CAMERA_BOOM_HALF_Z = 0.02
CAMERA_BODY_HALF = 0.035
CAMERA_STRUT_HALF_XY = 0.012
CAMERA_LENS_RADIUS = 0.018
CAMERA_LENS_HALF_Z = 0.02

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
    """Numele țintei de prindere a compartimentului familiei date.

    Compartimentul fiecărei familii este o țintă ca oricare alta, fiindcă acolo ajunge piesa:
    planul le desenează, iar brațul 2 le deservește pe toate cinci.
    """
    try:
        return RACK_TARGETS[family]
    except KeyError:
        raise KeyError(f"familie fără compartiment în raft: {family}") from None


def target_z(name: str) -> float:
    """Cota punctului de prindere, din înălțimile documentate.

    Planul dă doar desenul de sus, deci cota fiecărei ținte o fixează scena: benzile se prind de
    pe suprafața lor de lucru, compartimentele raftului de pe podeaua lui
    (`docs/11-geaman-mujoco.md` §2), iar dispozitivul mașinii la înălțimea universalului, pe masa
    mașinii.
    """
    if name in ("belt1_pick", "belt2_drop", "belt2_pick"):
        return BELT_SURFACE_Z
    if name == "cnc_chuck":
        return MACHINE_TABLE_Z + MACHINE_CHUCK_HALF_Z
    if name in RACK_TARGETS.values():
        return RACK_SHELF_Z
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
    banda 2, exact sub camera de inspecție, fiindcă acolo verdictul de calitate are un subiect în
    cadru, iar poziția rămâne în raza brațului 2.
    """
    pick = target("belt1_pick")
    camera_plan = CAMERAS[0]
    return (
        PartPlacement("blank_01", STATE_BLANK, belt_stop_x("belt_in"), float(pick["y"]),
                      BELT_SURFACE_Z, belt_bounds(BELT_IN),
                      'adusă de bandă până la ținta „' + str(pick["label"]) + '" din plan'),
        PartPlacement("part_01", STATE_MACHINED, float(camera_plan["x"]), center(BELT_OUT)[1],
                      BELT_SURFACE_Z, belt_bounds(BELT_OUT),
                      "banda 2, sub camera de inspecție"),
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
    """Raftul pe familii: blatul, despărțitoarele, buzele de la capete și picioarele.

    Despărțitoarele ies din plan: ``compartments + 1`` muchii, una la fiecare margine de
    compartiment, fiindcă numărul compartimentelor stă într-un singur loc, în plan. Blatul are
    fața de sus la :data:`RACK_SHELF_Z`, iar buzele de la capete sunt scurte, cât să țină piesa
    pe blat fără să stea în calea falcilor.
    """
    cx, cy = center(RACK)
    hx, hy = float(RACK["w"]) / 2, float(RACK["h"]) / 2
    slab_hz = RACK_SLAB_HALF_Z
    slab_z = RACK_SHELF_Z - slab_hz
    leg_top = slab_z - slab_hz
    leg_hz = leg_top / 2
    values: dict[str, float] = {
        "rack_cx": cx,
        "rack_cy": cy,
        "rack_hx": hx,
        "rack_hy": hy,
        "rack_slab_hz": slab_hz,
        "rack_slab_z": slab_z,
        "rack_div_hxy": RACK_DIVIDER_HALF_XY,
        "rack_div_hz": RACK_DIVIDER_HALF_Z,
        "rack_div_z": RACK_SHELF_Z + RACK_DIVIDER_HALF_Z,
        "rack_lip_hy": RACK_LIP_HALF_Y,
        "rack_lip_dy": hy - RACK_LIP_HALF_Y,
        "rack_leg_hxy": RACK_LEG_HALF_XY,
        "rack_leg_hz": leg_hz,
        "rack_leg_dz": leg_hz,
        "rack_leg_dx": hx - RACK_LEG_INSET,
        "rack_leg_dy": hy - RACK_LEG_INSET,
    }
    compartments = int(RACK["compartments"])
    for index in range(compartments + 1):
        values[f"rack_div_dx_{index}"] = -hx + 2 * hx * index / compartments
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


def inspection_camera_z(belt_surface_z: float = BELT_SURFACE_Z) -> float:
    """Cota centrului optic al camerei de inspecție: 0,85 m deasupra suprafeței benzii 2."""
    return belt_surface_z + INSPECTION_CAMERA_CLEARANCE_Z


def inspection_camera_intrinsics(camera_z: float | None = None) -> dict[str, float]:
    """Intrinsics-urile camerei de inspecție, calculate din geometrie, nu afirmate.

    Documentul nu spune nimic despre câmpul vizual ori despre rezoluția camerei de inspecție, iar
    cifrele de aici sunt exact cele care decid dacă defectele din catalog se pot distinge: un
    dinte ciobit are 4 mm adâncime, o gaură descentrată 2,5 mm.
    """
    z = inspection_camera_z() if camera_z is None else camera_z
    width, height = INSPECTION_CAMERA_FRAME
    half_angle = math.radians(INSPECTION_CAMERA_FOVY) / 2
    coverage_y = 2 * (z - BELT_SURFACE_Z) * math.tan(half_angle)
    return {
        "distance_m": z - BELT_SURFACE_Z,
        "coverage_x_m": coverage_y * width / height,
        "coverage_y_m": coverage_y,
        "px_per_mm": height / (coverage_y * 1000),
        "fovy_deg": INSPECTION_CAMERA_FOVY,
        "fovx_deg": math.degrees(2 * math.atan(math.tan(half_angle) * width / height)),
    }


def camera_geometry(camera_z: float | None = None) -> dict[str, float]:
    """Camera fixă de inspecție: consolă pe mașină, cap optic deasupra axei benzii 2.

    Axul optic stă la punctul camerei din plan (`cell_layout.CAMERAS[0]`), iar consola se prinde
    de colțul sud-vest al mașinii și se așază pe capacul ei, care este și cota de montaj. Capul
    optic nu poate sta chiar la punctul din plan: o cameră care privește în jos trebuie să fie
    deasupra subiectului ei, iar acolo, peste axa benzii 2, brațele urcă până la ~1,63 m în lume.
    Consola ocolește anvelopa pe la 1,90 m, iar tija coboară numai cât să iasă în câmp piesa.
    Cota capului optic este parametru, nu constantă.
    """
    machine = machine_geometry()
    mount_x = machine["cnc_cx"] - machine["cnc_post_dx"]
    mount_y = machine["cnc_cy"] - machine["cnc_post_dy"]
    aim_x = float(CAMERAS[0]["x"])
    aim_y = center(BELT_OUT)[1]
    z = inspection_camera_z() if camera_z is None else camera_z
    boom_bottom = MACHINE_COVER_Z
    boom_dx, boom_dy = aim_x - mount_x, aim_y - mount_y
    boom_length = math.hypot(boom_dx, boom_dy)
    boom_yaw = math.atan2(boom_dy, boom_dx)
    strut_hz = (boom_bottom - z - CAMERA_BODY_HALF) / 2
    if strut_hz <= 0:
        raise ValueError(
            f"capul optic la {z:.3f} m nu încape sub consola de la {boom_bottom:.3f} m; "
            f"cota maximă este {boom_bottom - CAMERA_BODY_HALF:.3f} m")
    return {
        "cam_x": aim_x,
        "cam_y": aim_y,
        "cam_z": z,
        "cam_dx": boom_dx,
        "cam_dy": boom_dy,
        "cam_body_half": CAMERA_BODY_HALF,
        "cam_lens_r": CAMERA_LENS_RADIUS,
        "cam_lens_hz": CAMERA_LENS_HALF_Z,
        "cam_lens_dz": -CAMERA_LENS_HALF_Z,
        "cam_fovy": INSPECTION_CAMERA_FOVY,
        "cam_mount_x": mount_x,
        "cam_mount_y": mount_y,
        "cam_boom_hx": boom_length / 2,
        "cam_boom_hy": CAMERA_BOOM_HALF_XY,
        "cam_boom_hz": CAMERA_BOOM_HALF_Z,
        # Consola se întinde de la montaj până deasupra axei benzii; orientarea se scrie ca
        # cuaternar, fiindcă `scene/cell.xml` nu are unde ține un unghi.
        "cam_boom_dx": boom_dx / 2,
        "cam_boom_dy": boom_dy / 2,
        "cam_boom_z": boom_bottom + CAMERA_BOOM_HALF_Z,
        "cam_boom_qw": math.cos(boom_yaw / 2),
        "cam_boom_qz": math.sin(boom_yaw / 2),
        "cam_strut_hxy": CAMERA_STRUT_HALF_XY,
        "cam_strut_hz": strut_hz,
        "cam_strut_dz": boom_bottom - strut_hz - z,
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


def scene_geometry(camera_z: float | None = None) -> dict[str, float]:
    """Toate cifrele pe care `scene/cell.xml` le primește ca valori de substituție.

    ``camera_z`` este parametrul camerei de inspecție: implicit 0,85 m deasupra benzii, dar se
    poate muta fără să atingă niciun alt element al scenei.
    """
    values: dict[str, float] = {}
    values.update(cell_geometry())
    values.update(belt_geometry("belt_in", BELT_IN))
    values.update(belt_geometry("belt_out", BELT_OUT))
    values.update(machine_geometry())
    values.update(rack_geometry())
    values.update(scrap_bin_geometry())
    values.update(camera_geometry(camera_z))
    for item in ARMS:
        values.update(arm_geometry(item["key"]))
    return values
