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

#: Câte niveluri are raftul și câte locuri are un nivel, din plan.
RACK_LEVELS = int(RACK["levels"])
RACK_SLOTS_PER_LEVEL = int(RACK["slots_per_level"])

#: Locurile raftului, în ordinea de umplere: nivelurile de sus în jos, iar în nivel de la nord la
#: sud. Toate au câte o țintă în plan, în afară de cele care rămân goale — familiile sunt cinci,
#: iar locurile șase.
RACK_SLOT_TARGETS: tuple[str, ...] = tuple(RACK_TARGETS[family] for family in RACK_FAMILIES)

# ------------------------------------------------------------------ înălțimile documentate

#: Suprafața de lucru a benzilor: `docs/11-geaman-mujoco.md` §2.
BELT_SURFACE_Z = 0.85
#: Masa mașinii CNC: `docs/11-geaman-mujoco.md` §2.
MACHINE_TABLE_Z = 0.75
#: Capacul de protecție: `docs/11-geaman-mujoco.md` §2.
MACHINE_COVER_Z = 1.90
#: Podeaua treptei de jos a raftului, adică suprafața pe care stau piesele de acolo:
#: `docs/11-geaman-mujoco.md` §2. Stă la nivelul suprafeței benzii, ca piesa să treacă din bandă
#: în raft fără să urce ori să coboare. De aici vine și câștigul față de cutii: raftul e deschis,
#: cu buze scurte, nu are pereți de 0,85 m în care să intre antebrațul.
RACK_SHELF_Z = BELT_SURFACE_Z
#: Cât urcă o treaptă față de cea de dedesubt: `docs/11-geaman-mujoco.md` §2. Pasul are două
#: margini, amândouă măsurate în scenă cu `view.py --demo`. De sus, antebrațul brațului 2 mătură
#: amprenta raftului la ~1,20 m în timpul apropierii, deci vârful treptei de sus rămâne sub atât. De
#: jos, încheietura brațului trece la ~0,153 m peste podeaua locului în care coboară scula, iar
#: marginea de est a treptei de dedesubt, cu buza ei cu tot, stă la ``pas + 0,02`` peste aceeași
#: podea: la 0,14 m veriga rămâne sprijinită pe buza treptei de la est, la 0,12 m trece cu 13 mm. La
#: 0,12 m podelele cad la 0,85 / 0,97 / 1,09 m. Treptele urcă spre vest, departe de braț, fiindcă
#: scula nu nimerește nimic sub ~0,65 m la sud de baza brațului 2; vezi `docs/diagrams/cell_layout.py`.
RACK_LEVEL_PITCH = 0.12
#: Marginea lăzii de rebut, sub capătul de vest al benzii 2: `docs/11-geaman-mujoco.md` §2. Cota
#: stă sub nivelul benzii, ca piesa care cade de la capătul benzii să treacă peste margine.
SCRAP_BIN_RIM_Z = 0.62
#: Baza brațelor, pe piedestal: `docs/11-geaman-mujoco.md` §2. `view.py --base-z` o schimbă
#: pentru măsurători, dar cota din scenă este cea documentată. Cota a fost 0,55 m până s-a măsurat
#: în scenă: acolo brațul 2 nu putea ridica piesa de pe banda 2, fiindcă antebrațul lui coboară sub
#: platformă, iar platforma rămâne deasupra sculei (`arm2_link3↔belt_out`, 106 mm eroare rămasă).
#: La 0,95 m ciclul se încheie; în schimb anvelopa brațelor urcă până la ~1,63 m, peste capul
#: camerei fixe, deci camera se ridică și ea (vezi mai jos) și nu mai are cum să rămână singura
#: sursă a verdictului: verdictul îl dă camera de pe braț, iar camera fixă privește banda de sus.
ARM_BASE_Z = 0.95
#: Înălțimea documentată a camerelor, orientate în jos: `docs/11-geaman-mujoco.md` §2.
#: Camera de inspecție a calității este singura excepție, deliberată: vezi mai jos
#: :data:`INSPECTION_CAMERA_CLEARANCE_Z` și `README.md` pentru aritmetica din spate.
CAMERA_Z = 2.20

#: Cât stă camera de inspecție deasupra suprafeței benzii 2: capul optic stă la cota documentată a
#: camerelor, :data:`CAMERA_Z`, adică 1,35 m peste bandă. La 2,20 m piesa de ~104 mm ar ocupa
#: vreo 10 pixeli, iar cel mai mic defect din catalog are 2,5 mm, deci nicio clasă nu ar fi
#: separabilă; obiectivul se îngustează atât cât acoperirea pe piesă să rămână aceeași. Sub cota
#: asta, camera nu are însă loc: măsurat în scenă pe plaja articulațiilor, cu baza la
#: :data:`ARM_BASE_Z`, cotul unui braț ajunge la ~1,15 m de bază, adică ~2,10 m în lume, peste axa
#: benzii 2. Vezi `README.md`, „Camera de inspecție", și `view.py --check`.
INSPECTION_CAMERA_CLEARANCE_Z = CAMERA_Z - BELT_SURFACE_Z

#: Câmpul vizual vertical al camerei de inspecție, în grade. De la 1,35 m, 5,6° acoperă 132 mm pe
#: verticală și, cu raportul de aspect 4:3, 176 mm pe orizontală — adică 3,6 px/mm la 640x480,
#: destul pentru un dinte ciobit de 4 mm. Cota și obiectivul merg împreună: singură contează
#: acoperirea la nivelul benzii, adică rezoluția pe piesă.
INSPECTION_CAMERA_FOVY = 5.6

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
    DocumentedHeight("rack_shelf", RACK_SHELF_Z, "raft", "podelele raftului pe familii"),
    DocumentedHeight("rack_level_pitch", RACK_LEVEL_PITCH, "nivel",
                     "pasul dintre nivelurile raftului"),
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

#: Raftul pe familii: trei trepte pline, fiecare cu două locuri. Treptele urcă spre est, spre
#: braț, iar fiecare treaptă e un bloc plin până la podeaua ei, deci fața de est a treptei de
#: dedesubt slujește drept perete de vest locului de deasupra. Peste blocuri vin buzele, scunde cât
#: să țină piesa culcată fără să stea în calea mâinii, și despărțitorul dintre cele două locuri ale
#: treptei. Sunt secțiuni de desen, nu cifre din plan ori din documentație.
RACK_LIP_H = 0.02
RACK_LIP_HALF_THICKNESS = 0.005
#: Despărțitorul dintre locurile unei trepte: peste înălțimea piesei, dar cât mai jos, fiindcă
#: încheietura brațului trece pe deasupra lui când scula coboară în locul de lângă braț.
RACK_DIVIDER_H = 0.03
RACK_DIVIDER_HALF_THICKNESS = 0.005
#: Cât se retrage despărțitorul de la marginea de est a treptei. Scula se lasă în locul treptei de
#: la est, iar încheietura brațului rămâne deasupra muchiei dintre trepte, chiar la colțul de est al
#: despărțitorului: măsurat în scenă, cu despărțitorul până în margine veriga încheieturii îl atinge
#: și rămâne sprijinită pe el, iar cu 3,5 cm de retragere trece.
RACK_DIVIDER_INSET = 0.035

#: Lada de rebut: podea și trei pereți, cu latura de est deschisă, sub capătul de vest al benzii 2.
SCRAP_BIN_WALL_HALF_THICKNESS = 0.01
SCRAP_BIN_FLOOR_HALF_Z = 0.025

#: Baza brațului are „0,25 Ø" în tabelul de coordonate (`cell_layout.py:386`).
ARM_PEDESTAL_RADIUS = 0.125

#: Consola camerei de inspecție: un picior care se așază pe capacul mașinii CNC, la
#: :data:`MACHINE_COVER_Z`, o braț care merge de acolo până deasupra axei benzii 2 și capul optic
#: atârnat sub braț pe o tijă subțire, până la :func:`inspection_camera_z`. Cotele de aici sunt
#: secțiuni de desen, nu cifre din plan ori din documentație; dacă se schimbă, se schimbă doar aici.
CAMERA_BOOM_HALF_XY = 0.04
CAMERA_BOOM_HALF_Z = 0.02
CAMERA_RISER_HALF_XY = 0.03
CAMERA_BODY_HALF = 0.035
CAMERA_STRUT_HALF_XY = 0.012
#: Cât atârnă tija între braț și corpul camerei. Sub braț nu are loc decât atât: capul optic stă la
#: cota documentată a camerelor, iar brațul trece imediat deasupra lui.
CAMERA_STRUT_HEIGHT = 0.08
CAMERA_LENS_RADIUS = 0.018
CAMERA_LENS_HALF_Z = 0.02
#: Cât de departe de axul brațului 1 stă piciorul consolei, pe capac. Sub cota asta, anvelopa
#: cotului cuprinde colțul sud-vestic al capacului: măsurat, cotul lui arm1 ajunge la ~1,15 m de
#: bază, iar colțul e la 1,05 m. Deplasarea spre est mută piciorul la 1,33 m.
CAMERA_RISER_INSET_X = 0.25

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


def rack_slot_index(name: str) -> int:
    """Locul unei ținte din raft, în ordinea de umplere (0 = treapta de jos, latura nordică)."""
    return int(target(name)["slot"])


def rack_level_of(name: str) -> int:
    """Nivelul unei ținte din raft, numerotat de jos în sus, de la 1."""
    return rack_slot_index(name) // RACK_SLOTS_PER_LEVEL + 1


def rack_level_floor_z(level: int) -> float:
    """Podeaua unui nivel din raft: nivelul 1 e cel de jos, la cota documentată.

    Treptele urcă spre vest, departe de braț: lângă bază brațul are cel mai puțin loc, iar scula nu
    nimerește oricum nimic sub ~0,65 m la sud de baza brațului 2 (vezi `docs/diagrams/cell_layout.py`).
    """
    if not 1 <= level <= RACK_LEVELS:
        raise KeyError(f"nivel inexistent în raft: {level}")
    return RACK_SHELF_Z + (level - 1) * RACK_LEVEL_PITCH


def rack_level_span(level: int) -> tuple[float, float]:
    """Felia din lungimea raftului pe care stă nivelul dat, în lume (``x0``, ``x1``).

    Nivelul 1 este treapta de la est, cea de lângă braț: scara urcă spre vest, departe de braț.
    """
    if not 1 <= level <= RACK_LEVELS:
        raise KeyError(f"nivel inexistent în raft: {level}")
    return tuple(cell_layout.rack_level_span(level))  # type: ignore[return-value]


def rack_empty_names() -> tuple[str, ...]:
    """Locurile raftului care rămân goale: fără familie, deci fără țintă în plan."""
    count = RACK_LEVELS * RACK_SLOTS_PER_LEVEL - len(RACK_SLOT_TARGETS)
    return tuple(f"rack_loc_{index + 1 + len(RACK_SLOT_TARGETS)}" for index in range(count))


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
        return rack_level_floor_z(rack_level_of(name))
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
    """Raftul pe familii: trei trepte pline, fiecare cu podeaua, buzele și despărțitorul ei.

    Numărul nivelurilor și al locurilor iese din plan, iar cotele din înălțimile documentate:
    treapta de jos are podeaua la :data:`RACK_SHELF_Z` — cota suprafeței benzii, ca piesa să treacă
    din bandă în raft fără să urce — iar celelalte urcă cu câte :data:`RACK_LEVEL_PITCH`, spre vest,
    departe de braț. Fiecare treaptă e un bloc plin până la podeaua ei, deci fața de est a treptei
    de dedesubt este peretele de vest al locului; peste asta, fiecare nivel are buze scunde la
    marginile lui și un despărțitor între cele două locuri, iar treapta de sus, care nu mai are
    nimic în față spre vest, are și buză la vest.
    """
    cx, cy = center(RACK)
    hx, hy = float(RACK["w"]) / 2, float(RACK["h"]) / 2
    values: dict[str, float] = {
        "rack_cx": cx,
        "rack_cy": cy,
        "rack_hy": hy,
        "rack_lip_h": RACK_LIP_H,
        "rack_lip_hy": RACK_LIP_HALF_THICKNESS,
        "rack_lip_dy": hy - RACK_LIP_HALF_THICKNESS,
        "rack_divider_hy": RACK_DIVIDER_HALF_THICKNESS,
        "rack_divider_h": RACK_DIVIDER_H,
    }
    for level in range(1, RACK_LEVELS + 1):
        floor = rack_level_floor_z(level)
        x0, x1 = rack_level_span(level)
        step_hx = (x1 - x0) / 2
        values[f"rack_block_cx_{level}"] = (x0 + x1) / 2 - cx
        values[f"rack_block_hx_{level}"] = step_hx
        values[f"rack_block_hz_{level}"] = floor / 2
        values[f"rack_lip_west_dx_{level}"] = x0 + RACK_LIP_HALF_THICKNESS - cx
        values[f"rack_lip_east_dx_{level}"] = x1 - RACK_LIP_HALF_THICKNESS - cx
        values[f"rack_lip_hx_{level}"] = step_hx
        values[f"rack_lip_z_{level}"] = floor + RACK_LIP_H / 2
        values[f"rack_divider_hx_{level}"] = (x1 - x0 - RACK_DIVIDER_INSET) / 2
        values[f"rack_divider_dx_{level}"] = (x0 + x1 - RACK_DIVIDER_INSET) / 2 - cx
        values[f"rack_divider_z_{level}"] = floor + RACK_DIVIDER_H / 2
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
    """Camera fixă de inspecție: picior pe capacul mașinii, braț, cap optic deasupra benzii 2.

    Axul optic stă la punctul camerei din plan (`cell_layout.CAMERAS[0]`), iar consola se așază pe
    capacul mașinii, la :data:`MACHINE_COVER_Z`: capacul este singura structură înaltă de lângă
    bandă, iar piciorul lui stă spre est, ca să nu intre în anvelopa cotului brațului 1.
    Capul optic nu poate sta chiar la punctul din plan: o cameră care privește în jos trebuie să
    fie deasupra subiectului ei, iar acolo, peste axa benzii 2, anvelopa brațelor urcă până la
    ~2,10 m în lume — cotul ajunge la ~1,15 m de bază. De aceea capul optic stă la cota
    documentată a camerelor, :data:`CAMERA_Z`, iar brațul consolei trece imediat deasupra lui.
    Cota capului optic este parametru, dar geometria refuză cotele de sub :data:`CAMERA_Z`: acolo
    obiectivul ar vedea brațele, nu piesa.
    """
    machine = machine_geometry()
    mount_x = machine["cnc_cx"] - CAMERA_RISER_INSET_X
    mount_y = machine["cnc_cy"] - machine["cnc_roof_hy"]
    aim_x = float(CAMERAS[0]["x"])
    aim_y = center(BELT_OUT)[1]
    z = inspection_camera_z() if camera_z is None else camera_z
    if z < CAMERA_Z:
        raise ValueError(
            f"capul optic la {z:.3f} m coboară sub cota documentată a camerelor, "
            f"{CAMERA_Z:.2f} m; sub ea anvelopa brațelor trece peste axa benzii 2")
    boom_bottom = z + CAMERA_BODY_HALF + CAMERA_STRUT_HEIGHT
    boom_dx, boom_dy = aim_x - mount_x, aim_y - mount_y
    boom_length = math.hypot(boom_dx, boom_dy)
    boom_yaw = math.atan2(boom_dy, boom_dx)
    riser_hz = (boom_bottom - MACHINE_COVER_Z) / 2
    strut_hz = CAMERA_STRUT_HEIGHT / 2
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
        "cam_riser_hxy": CAMERA_RISER_HALF_XY,
        "cam_riser_hz": riser_hz,
        "cam_riser_z": MACHINE_COVER_Z + riser_hz,
        "cam_boom_hx": boom_length / 2,
        "cam_boom_hy": CAMERA_BOOM_HALF_XY,
        "cam_boom_hz": CAMERA_BOOM_HALF_Z,
        # Brațul consolei se întinde de la picior până deasupra axei benzii; orientarea se scrie
        # ca cuaternar, fiindcă `scene/cell.xml` nu are unde ține un unghi.
        "cam_boom_dx": boom_dx / 2,
        "cam_boom_dy": boom_dy / 2,
        "cam_boom_z": boom_bottom + CAMERA_BOOM_HALF_Z,
        "cam_boom_qw": math.cos(boom_yaw / 2),
        "cam_boom_qz": math.sin(boom_yaw / 2),
        "cam_strut_hxy": CAMERA_STRUT_HALF_XY,
        "cam_strut_hz": strut_hz,
        "cam_strut_dz": CAMERA_BODY_HALF + strut_hz,
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
