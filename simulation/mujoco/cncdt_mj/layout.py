"""Așezarea în plan și înălțimile celulei: sursa unică pentru scena MuJoCo.

Coordonatele ``(x, y)`` **nu** se transcriu aici. Se importă din ``docs/diagrams/cell_layout.py``,
fiindcă acolo stă singura sursă de adevăr a desenului, iar `docs/diagrams/README.md` promite că
planul și scena nu se pot contrazice. Înălțimile ``Z`` vin din `docs/11-geaman-mujoco.md:109-118`
și sunt trecute mai jos ca :data:`DOCUMENTED_HEIGHTS`, cu propoziția din documentație de care
aparțin; testul din `tests/unit/test_mujoco_scene.py` verifică exact potrivirea.

Modulul nu importă MuJoCo (ADR 0012), deci se poate testa în `.venv`-ul proiectului. Tot ce ține
de geometria sculei — cât de groasă e platforma benzii, cât de groasă e perechea cutiei — se
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
BIN_ACCEPTED: Mapping[str, Any] = cell_layout.BIN_OK
BIN_TRASH: Mapping[str, Any] = cell_layout.BIN_NG
ARMS: tuple[Mapping[str, Any], ...] = tuple(cell_layout.ARMS)
CAMERAS: tuple[Mapping[str, Any], ...] = tuple(cell_layout.CAMERAS)
TARGETS: tuple[Mapping[str, Any], ...] = tuple(cell_layout.TARGETS)
FLOW: tuple[tuple[float, float], ...] = tuple(cell_layout.FLOW)

#: Raza de atingere a brațului, în plan, din tabelul de coordonate.
ARM_REACH_M = float(ARMS[0]["reach"])

# ------------------------------------------------------------------ înălțimile documentate

#: Suprafața de lucru a benzilor: `docs/11-geaman-mujoco.md:109`.
BELT_SURFACE_Z = 0.85
#: Masa mașinii CNC: `docs/11-geaman-mujoco.md:110`.
MACHINE_TABLE_Z = 0.75
#: Capacul de protecție: `docs/11-geaman-mujoco.md:110`.
MACHINE_COVER_Z = 1.90
#: Marginea de sus a cutiilor: `docs/11-geaman-mujoco.md:110`.
BIN_RIM_Z = 0.35
#: Baza brațelor, pe piedestal: `docs/11-geaman-mujoco.md:110-111`.
ARM_BASE_Z = 0.55
#: Înălțimea documentată a camerelor, orientate în jos: `docs/11-geaman-mujoco.md:111`.
#: Camera de inspecție a calității este singura excepție, deliberată: vezi mai jos
#: :data:`INSPECTION_CAMERA_CLEARANCE_Z` și `README.md` pentru aritmetica din spate.
CAMERA_Z = 2.20

#: Cât stă camera de inspecție deasupra suprafeței benzii 2. La 2,20 m piesa de ~104 mm ar ocupa
#: vreo 10 pixeli, iar cel mai mic defect din catalog are 2,5 mm, deci nicio clasă nu ar fi
#: separabilă. Cifra și motivarea vin din planul aprobat, secțiunea B.
INSPECTION_CAMERA_CLEARANCE_Z = 0.25

#: Câmpul vizual vertical al camerei de inspecție, în grade. La 0,25 m acoperă 134 mm pe
#: verticală și, cu raportul de aspect 4:3, 179 mm pe orizontală — adică 3,6 px/mm la 640x480,
#: destul pentru un dinte ciobit de 4 mm. Vezi :func:`inspection_camera_intrinsics`.
INSPECTION_CAMERA_FOVY = 30.0

#: Rezoluția cadrelor de inspecție: cerută explicit de planul aprobat, în locul implicitului
#: 160x120 din `docs/11-geaman-mujoco.md:53`.
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
    DocumentedHeight("bin_rim", BIN_RIM_Z, "marginea", "cutiile pe podea, cu marginea"),
    DocumentedHeight("arm_base", ARM_BASE_Z, "piedestal", "baza brațelor pe piedestal"),
    DocumentedHeight("camera", CAMERA_Z, "camer", "camerele"),
)

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

BIN_WALL_HALF_THICKNESS = 0.01
BIN_FLOOR_HALF_Z = 0.01

#: Baza brațului are „0,25 Ø" în tabelul de coordonate (`cell_layout.py:386`).
ARM_PEDESTAL_RADIUS = 0.125

#: Camera de inspecție stă pe un braț consolă, care pornește dintr-un stâlp. Stâlpul se așază
#: exact la punctul camerei din plan, iar consola iese spre nord până deasupra axei benzii 2, cu
#: capul optic atârnat sub ea. Cotele de aici sunt secțiuni de desen, nu cifre din plan ori din
#: documentație; dacă se schimbă, se schimbă doar aici.
CAMERA_POST_HALF_XY = 0.04
#: Consola stă la 0,25 m deasupra centrului optic, ca să nu ajungă în calea mâinii unui braț care
#: lucrează pe bandă (cu cât brațul se apropie de piesă, cu atât trece mai jos); capul optic
#: atârnă de ea pe o tije subțire.
CAMERA_BOOM_CLEARANCE_Z = 0.25
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


def target_z(name: str) -> float:
    """Cota punctului de prindere, din înălțimile documentate.

    Planul dă doar desenul de sus, deci cota fiecărei ținte o fixează scena: benzile se prind de
    pe suprafața lor de lucru, cutiile la marginea de sus (`docs/11-geaman-mujoco.md:110`), iar
    dispozitivul mașinii la înălțimea universalului, pe masa mașinii.
    """
    if name in ("belt1_pick", "belt2_drop", "belt2_pick"):
        return BELT_SURFACE_Z
    if name == "cnc_chuck":
        return MACHINE_TABLE_Z + MACHINE_CHUCK_HALF_Z
    if name in ("bin_ok", "bin_ng"):
        return BIN_RIM_Z
    raise KeyError(f"țintă fără cotă cunoscută: {name}")


def target_distance(name: str) -> float:
    """Distanța în spațiu, în metri, de la baza brațului la ținta lui.

    Planul măsoară doar în plan, iar `docs/11-geaman-mujoco.md:120-122` spune explicit că o țintă
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


def bin_items() -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Cutiile, cu cheile lor de scenă."""
    return (("bin_ok", BIN_ACCEPTED), ("bin_ng", BIN_TRASH))


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

    Blocul brut stă pe banda 1, în ținta de preluare a brațului 1: acolo ajunge marfa, cu poziție
    și rotație aleatoare în jurul punctului nominal, deci punctul de aici este centrul împrăștierii,
    nu o constantă a scenei. Piesa mașinată stă pe banda 2, exact sub camera de inspecție, fiindcă
    acolo verdictul de calitate are un subiect în cadru, iar poziția rămâne în raza brațului 2.
    """
    pick = target("belt1_pick")
    camera_plan = CAMERAS[0]
    return (
        PartPlacement("blank_01", STATE_BLANK, float(pick["x"]), float(pick["y"]),
                      BELT_SURFACE_Z, belt_bounds(BELT_IN),
                      'ținta „' + str(pick["label"]) + '" din plan'),
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


def bin_geometry(key: str, item: Mapping[str, Any]) -> dict[str, float]:
    """O cutie deschisă, cu marginea sus la :data:`BIN_RIM_Z`."""
    cx, cy = center(item)
    hx, hy = float(item["w"]) / 2, float(item["h"]) / 2
    wall_hz = (BIN_RIM_Z - 2 * BIN_FLOOR_HALF_Z) / 2
    return {
        f"{key}_cx": cx,
        f"{key}_cy": cy,
        f"{key}_hx": hx,
        f"{key}_hy": hy,
        f"{key}_wall_dx": hx - BIN_WALL_HALF_THICKNESS,
        f"{key}_wall_dy": hy - BIN_WALL_HALF_THICKNESS,
        f"{key}_wall_hy_long": hy - 2 * BIN_WALL_HALF_THICKNESS,
        f"{key}_wall_hx_long": hx - 2 * BIN_WALL_HALF_THICKNESS,
        "bin_floor_hz": BIN_FLOOR_HALF_Z,
        "bin_floor_z": BIN_FLOOR_HALF_Z,
        "bin_wall_hxy": BIN_WALL_HALF_THICKNESS,
        "bin_wall_hz": wall_hz,
        "bin_wall_z": 2 * BIN_FLOOR_HALF_Z + wall_hz,
    }


def inspection_camera_z(belt_surface_z: float = BELT_SURFACE_Z) -> float:
    """Cota centrului optic al camerei de inspecție: 0,25 m deasupra suprafeței benzii 2."""
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
    """Camera fixă de inspecție: stâlp la locul ei din plan, cap optic deasupra benzii 2.

    Stâlpul stă exact la punctul din plan (`cell_layout.CAMERAS[0]`), iar consola duce capul optic
    până deasupra axei benzii 2. Mutarea e necesară: o cameră care privește în jos trebuie să fie
    deasupra subiectului ei, iar la 1,10 m cu 30° pe verticală un punct aflat la 0,45 m spre nord
    ar ieși complet din cadru. Cota capului optic este parametru, nu constantă.
    """
    camera_plan = CAMERAS[0]
    post_x, post_y = float(camera_plan["x"]), float(camera_plan["y"])
    aim_x, aim_y = post_x, center(BELT_OUT)[1]
    z = inspection_camera_z() if camera_z is None else camera_z
    boom_dy = aim_y - post_y
    boom_bottom = z + CAMERA_BOOM_CLEARANCE_Z
    strut_hz = (boom_bottom - z - CAMERA_BODY_HALF) / 2
    post_hz = (boom_bottom + 2 * CAMERA_BOOM_HALF_Z) / 2
    return {
        "cam_x": aim_x,
        "cam_y": aim_y,
        "cam_z": z,
        "cam_dx": aim_x - post_x,
        "cam_dy": aim_y - post_y,
        "cam_body_half": CAMERA_BODY_HALF,
        "cam_lens_r": CAMERA_LENS_RADIUS,
        "cam_lens_hz": CAMERA_LENS_HALF_Z,
        "cam_lens_dz": -CAMERA_LENS_HALF_Z,
        "cam_fovy": INSPECTION_CAMERA_FOVY,
        "cam_post_x": post_x,
        "cam_post_y": post_y,
        "cam_post_hxy": CAMERA_POST_HALF_XY,
        "cam_post_hz": post_hz,
        "cam_post_z": post_hz,
        "cam_boom_hx": CAMERA_POST_HALF_XY,
        "cam_boom_dy": boom_dy / 2,
        "cam_boom_hy": boom_dy / 2,
        "cam_boom_hz": CAMERA_BOOM_HALF_Z,
        "cam_boom_z": boom_bottom + CAMERA_BOOM_HALF_Z,
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

    ``camera_z`` este parametrul camerei de inspecție: implicit 0,25 m deasupra benzii, dar se
    poate muta fără să atingă niciun alt element al scenei.
    """
    values: dict[str, float] = {}
    values.update(cell_geometry())
    values.update(belt_geometry("belt_in", BELT_IN))
    values.update(belt_geometry("belt_out", BELT_OUT))
    values.update(machine_geometry())
    for key, item in bin_items():
        values.update(bin_geometry(key, item))
    values.update(camera_geometry(camera_z))
    for item in ARMS:
        values.update(arm_geometry(item["key"]))
    return values
