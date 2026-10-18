"""Teste pentru geamănul MuJoCo: partea pură, fără motorul de fizică.

Scena nu se poate compila în `.venv`-ul proiectului, unde `mujoco` nu există (ADR 0012, decizia
7). Regulile care decid însă dacă scena e corectă — așezarea în plan, înălțimile documentate,
aritmetica obiectivului camerei de inspecție, compararea yaw-urilor, contactele normale ale
brațelor — sunt cod pur, iar testele de aici le acoperă pe acestea. Verifică în plus încadrarea
modulelor: `layout`, `part` și `checks` nu au voie să importe `mujoco`, iar `scene` este singurul
care o face.

Verificarea care are nevoie de motor — compilare, distanțe măsurate în scenă, cadru randat — stă
în `simulation/mujoco/view.py --check`, rulat cu `make mj-check` din venv-ul geamănului. Testul
marcat cu `pytest.importorskip` de la final o reia pe scurt, pentru cine rulează `pytest` chiar
în acel venv; în `.venv`-ul proiectului se sare singur.
"""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path

import numpy as np
import pytest

from cncdt.cnc_cell.parts import gear
from cncdt_mj import REPO_ROOT, belt, checks, layout, moves, part, textures

MUJOCO_DIR = REPO_ROOT / "simulation" / "mujoco"
PACKAGE_DIR = MUJOCO_DIR / "cncdt_mj"
DOC = REPO_ROOT / "docs" / "11-geaman-mujoco.md"

#: Paragraful din document care fixează înălțimile scenei. Testul îl caută după începutul lui, ca
#: o cifră să nu fie potrivită întâmplător într-o altă propoziție care o conține.
HEIGHTS_PARAGRAPH_START = "Înălțimile propuse"


def _document() -> str:
    return DOC.read_text(encoding="utf-8")


def _heights_paragraph() -> str:
    """Paragraful înălțimilor, fără restul secțiunii."""
    text = _document()
    start = text.index(HEIGHTS_PARAGRAPH_START)
    return text[start:text.index("\n\n", start)]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- înălțimile, față de document


def test_inaltimile_din_scena_apar_in_document() -> None:
    """Fiecare cotă din `layout.DOCUMENTED_HEIGHTS` are propoziția ei în documentul geamănului."""
    paragraph = _heights_paragraph()
    for height in layout.DOCUMENTED_HEIGHTS:
        value = f"{height.value:.2f}".replace(".", ",")
        pattern = rf"{height.keyword}\w*[^.\n]{{0,40}}?(?:la|de) {value} m"
        assert re.search(pattern, paragraph), (
            f"{height.name} = {height.value:.2f} m nu are propoziția așteptată în paragraful "
            f"înălțimilor (căutat: „{height.keyword}… la/de {value} m”): {height.quote}"
        )


# ---------------------------------------------------------------- camerele de pe flanșe


def test_geamănul_are_doar_camerele_de_pe_flanșe() -> None:
    """Camera fixă de proces a ieșit din scenă: verdictul îl dă brațul 2, din cadrul lui."""
    text = _document()
    assert "trei puncte de vedere" in text, (
        "documentul trebuie să spună câte puncte de vedere are scena")
    assert "camera de inspecție" not in text and "Camera de inspecție" not in text, (
        "documentul încă descrie camera fixă de deasupra benzii 2")
    for module, attribute in ((layout, "inspection_camera_intrinsics"),
                              (layout, "inspection_camera_z"),
                              (layout, "camera_geometry"),
                              (layout, "CAMERA_Z"),
                              (layout, "CAMERAS"),
                              (checks, "MIN_CAMERA_CLEARANCE_M"),
                              (checks, "MIN_INSPECTION_PX_PER_MM")):
        assert not hasattr(module, attribute), f"{module.__name__} încă are {attribute}"


# ---------------------------------------------------------------- piesa și așezarea ei


def test_piesa_masinata_are_geometria_generatorului() -> None:
    """Piesa din scenă este chiar cea dată de generatorul procedural, doar scalată în metri."""
    variant = part.load_variant()
    vertices, faces = part.scaled_geometry(variant)
    radius = float(np.hypot(vertices[:, 0], vertices[:, 1]).max())
    thickness = float(vertices[:, 2].max() - vertices[:, 2].min())
    assert radius == pytest.approx(part.NOMINAL_GEAR.outer_radius * part.MESH_SCALE, rel=0.01)
    assert thickness == pytest.approx(part.NOMINAL_GEAR.thickness * part.MESH_SCALE, rel=0.02)
    assert faces.shape[1] == 3
    assert int(faces.max()) < len(vertices)
    assert part.rest_z_m(variant, layout.BELT_SURFACE_Z) == pytest.approx(
        layout.BELT_SURFACE_Z + vertices[:, 2].max())


def test_axele_inguste_ale_formelor_sunt_cele_asteptate() -> None:
    """Falcile închid pe fețele cele mai apropiate, nu pe o coardă oarecare.

    Hexagonul are trei perechi de laturi paralele (normalele la 0°, 60°, 120°), triunghiul
    rotunjit trei laturi (30°, 90°, 150°), iar legătura și placa o singură axă scurtă. Roțile sînt
    rotunde: variația lățimii e sub prag, deci nu se rotește scula după un dinte.
    """
    expected = {
        "wheel_100": (),
        "pinion_56": (),
        "gusset_72": (30.0, 90.0, 150.0),
        "hex_68": (0.0, 60.0, 120.0),
        "link_68": (90.0,),
        "plate_84": (90.0,),
    }
    for family, axes in expected.items():
        assert part.narrow_axes_deg(family) == axes, family


def test_piesa_este_reproductibila_din_samanta() -> None:
    """Aceeași sămânță dă aceeași piesă; alta dă altă piesă."""
    variant = part.load_variant(part.DEFAULT_SEED)
    assert part.describe(variant) == part.describe(part.load_variant(part.DEFAULT_SEED))
    assert variant.part_id != part.load_variant(part.DEFAULT_SEED + 1).part_id


def test_blocul_brut_are_adaos_de_prelucrare() -> None:
    """Semifabricatul este mai mare decât roata care iese din el, pe rază și pe grosime."""
    blank = part.blank_stock()
    assert blank.diameter_mm == pytest.approx(2 * part.BLANK_RADIUS_MM)
    assert blank.height_mm == pytest.approx(part.BLANK_THICKNESS_MM)
    assert blank.radius_m > part.NOMINAL_GEAR.outer_radius * part.MESH_SCALE
    assert blank.half_height_m > part.NOMINAL_GEAR.thickness * part.MESH_SCALE / 2
    assert blank.rest_z_m(layout.BELT_SURFACE_Z) == pytest.approx(
        layout.BELT_SURFACE_Z + blank.half_height_m)
    assert part.blank_stock("box").shape == "box"
    with pytest.raises(ValueError):
        part.blank_stock("hexagon")


def test_celor_doua_stari_de_material_li_se_stie_locul() -> None:
    """Blocul brut stă în ținta de preluare a benzii 1, piesa mașinată pe masa de descărcare."""
    placements = {item.state: item for item in layout.part_placements()}
    blank = placements[layout.STATE_BLANK]
    machined = placements[layout.STATE_MACHINED]
    pick = layout.target("belt1_pick")
    assert blank.x == pytest.approx(belt.belt("belt_in").stop_x()), (
        "blocul brut stă la linia de oprire a benzii 1, adică în dreptul țintei de preluare")
    assert blank.y == pytest.approx(float(pick["y"]))
    assert blank.surface_z == layout.BELT_SURFACE_Z
    # Piesa mașinată apare în universal și este scoasă pe masa de descărcare a mașinii, lîngă
    # universal: acolo o lasă schimbul de material, iar de acolo o ridică brațul 1.
    assert machined.x == pytest.approx(layout.MACHINE_STAGING_X)
    assert machined.y == pytest.approx(layout.MACHINE_STAGING_Y)
    assert machined.surface_z == layout.MACHINE_TABLE_Z


def test_piesa_oprita_sta_fix_pe_sensul_benzii() -> None:
    """La linia de oprire, cota din lungul benzii nu mai e aleatoare: acolo o aduce banda."""
    place = next(item for item in layout.part_placements() if item.state == layout.STATE_BLANK)
    item = belt.belt("belt_in")
    radius = part.blank_stock().radius_m
    stop = item.stop_x()
    x0, x1, y0, y1 = place.bounds
    xy = part.stopped_xy(part.DEFAULT_SEED, stop, place.y, place.bounds, radius)
    assert xy[0] == stop
    assert y0 + radius <= xy[1] <= y1 - radius
    assert abs(xy[1] - place.y) <= part.BLANK_JITTER_M
    assert part.stopped_xy(part.DEFAULT_SEED, stop, place.y, place.bounds, radius) == xy
    # Lateral, două semințe diferite pot da cote diferite; în lungul benzii, niciodată.
    other = part.stopped_xy(part.DEFAULT_SEED + 1, stop, place.y, place.bounds, radius)
    assert other[0] == stop
    # O linie de oprire care lasă piesa peste capătul benzii este refuzată, nu strecurată.
    with pytest.raises(ValueError):
        part.stopped_xy(part.DEFAULT_SEED, x1, place.y, place.bounds, radius)


def test_imprastierea_piesei_ramane_pe_banda() -> None:
    """Poziția aleatoare este deterministă și ține piesa întreagă pe amprenta benzii."""
    place = next(item for item in layout.part_placements() if item.state == layout.STATE_BLANK)
    radius = part.blank_stock().radius_m
    nominal = (place.x, place.y)
    xy = part.random_xy(part.DEFAULT_SEED, nominal, place.bounds, radius)
    x0, x1, y0, y1 = place.bounds
    assert x0 + radius <= xy[0] <= x1 - radius
    assert y0 + radius <= xy[1] <= y1 - radius
    assert math.dist(xy, nominal) <= math.hypot(part.BLANK_JITTER_M, part.BLANK_JITTER_M) + 1e-9
    assert part.random_xy(part.DEFAULT_SEED, nominal, place.bounds, radius) == xy
    assert part.random_yaw_deg(part.DEFAULT_SEED, "part") != part.random_yaw_deg(
        part.DEFAULT_SEED, "blank")


def test_fiecare_tinta_cade_in_raza_bratului() -> None:
    """Distanța în spațiu, calculată din plan, nu doar cea din desen."""
    for item in layout.TARGETS:
        name = str(item["name"])
        assert layout.reach_ok(name), (
            f"{name}: {layout.target_distance(name):.3f} m peste raza {layout.ARM_REACH_M} m")


# ---------------------------------------------------------------- dulapul pe familii


def test_ordinea_compartimentelor_este_ordinea_familiilor() -> None:
    """Planul, generatorul și scena folosesc aceeași ordine a familiilor."""
    assert layout.RACK_FAMILIES == tuple(item.name for item in gear.FAMILIES)
    for index, family in enumerate(gear.FAMILIES):
        name = layout.rack_target(family.name)
        item = layout.target(name)
        assert item["key"] == "arm2", f"{name} nu aparține brațului care sortează"
        assert int(item["slot"]) == index, "compartimentele se umplu în ordinea familiilor"
        assert layout.rack_slot_index(name) == index
        entry = layout.rack_entry(index + 1)
        assert entry["family"] == family.name
        assert layout.rack_shelf_of(name) == int(entry["shelf"])
        assert layout.target_z(name) == pytest.approx(float(entry["floor"])), (
            "ținta stă pe podeaua raftului ei, nu pe podeaua de jos a dulapului")
        cx, cy = layout.rack_entry_center(index + 1)
        assert (float(item["x"]), float(item["y"])) == (pytest.approx(cx), pytest.approx(cy))
    with pytest.raises(KeyError):
        layout.rack_target("familie_inexistenta")


def test_dulapul_are_trei_rafturi_cu_doua_compartimente() -> None:
    """Trei rafturi suprapuse, fiecare despărțit în două de un panou vertical."""
    assert layout.RACK_SHELVES == int(layout.RACK["levels"]) == 3
    assert layout.RACK_COLUMNS == int(layout.RACK["columns"]) == 2
    assert layout.RACK_ENTRY_M == pytest.approx(0.5)
    assert layout.RACK_DEPTH_M == pytest.approx(0.30)
    assert len(layout.RACK_ENTRIES) == int(layout.RACK["slots"]) == 6
    assert len(layout.RACK_SLOT_TARGETS) == 6, "fiecare compartiment are o familie"
    assert layout.RACK_ENTRIES[-1]["family"] == "plate_84"

    assert layout.RACK_FLOORS == pytest.approx((0.85, 1.16, 1.41))
    assert layout.RACK_TOP_Z == pytest.approx(1.69)
    assert layout.RACK_SHELF_Z == pytest.approx(layout.BELT_SURFACE_Z), (
        "podeaua raftului de jos stă la nivelul benzii")
    assert layout.rack_floor_z() == pytest.approx(layout.RACK_FLOORS[0])
    assert float(layout.RACK["w"]) == pytest.approx(layout.RACK_DEPTH_M)
    assert float(layout.RACK["h"]) == pytest.approx(2 * layout.RACK_ENTRY_M)

    for shelf, floor in enumerate(layout.RACK_FLOORS, start=1):
        items = [item for item in layout.RACK_ENTRIES if int(item["shelf"]) == shelf]
        assert len(items) == 2, f"raftul {shelf} are două compartimente"
        assert [int(item["column"]) for item in items] == [1, 2]
        for entry in items:
            assert float(entry["floor"]) == pytest.approx(floor)
            assert layout.rack_entry_floor(int(entry["index"])) == pytest.approx(floor)
        y0, y1 = layout.rack_shelf_span(shelf)
        assert y0 == pytest.approx(min(float(item["y"]) for item in items))
        assert y1 - y0 == pytest.approx(2 * layout.RACK_ENTRY_M)
        assert y0 == pytest.approx(float(layout.RACK["y"]))

    with pytest.raises(KeyError):
        layout.rack_entry(7)
    with pytest.raises(KeyError):
        layout.rack_shelf_span(4)


def test_geometria_dulapului_are_carcasa_rafturi_si_despartitor() -> None:
    """Carcasa, cele trei blaturi și despărțitorul, cu fețele de sus la cotele podelelor."""
    geometry = layout.rack_geometry()
    board = geometry["rack_shelf_hz"]
    half_width = layout.RACK_DEPTH_M / 2
    half_length = float(layout.RACK["h"]) / 2
    side = layout.RACK_SIDE_HALF_THICKNESS

    # Lateralele și spatele urcă de la pardoseală până la fața de sus.
    for name in ("rack_side", "rack_back"):
        assert geometry[f"{name}_z"] - geometry[f"{name}_hz"] == pytest.approx(0.0)
        assert geometry[f"{name}_z"] + geometry[f"{name}_hz"] == pytest.approx(
            layout.RACK_TOP_Z)
    assert geometry["rack_side_south_dy"] == pytest.approx(-(half_length - side))
    assert geometry["rack_side_north_dy"] == pytest.approx(half_length - side)
    assert geometry["rack_back_dx"] + geometry["rack_back_hx"] == pytest.approx(half_width), (
        "spatele stă pe fața estică, opusă feței deschise")

    # Fundul și capacul, între laterale.
    assert geometry["rack_bottom_z"] - geometry["rack_board_hz"] == pytest.approx(0.0)
    assert geometry["rack_top_z"] + geometry["rack_board_hz"] == pytest.approx(
        layout.RACK_TOP_Z)

    # Fiecare blat are fața de sus chiar la cota podelei raftului.
    for number, floor in enumerate(layout.RACK_FLOORS, start=1):
        assert geometry[f"rack_shelf_{number}_z"] + board == pytest.approx(floor)
    assert geometry["rack_shelf_dx"] - geometry["rack_shelf_hx"] == pytest.approx(
        -half_width), "raftul ajunge până la fața vestică, pe unde intră scula"
    assert geometry["rack_shelf_dx"] + geometry["rack_shelf_hx"] == pytest.approx(
        half_width - 2 * side), "raftul se sprijină pe fața din spate a carcasei"

    # Despărțitorul urcă de la podeaua de jos până sub capac.
    assert geometry["rack_divider_z"] - geometry["rack_divider_hz"] == pytest.approx(
        layout.RACK_FLOORS[0])
    assert geometry["rack_divider_z"] + geometry["rack_divider_hz"] == pytest.approx(
        layout.RACK_TOP_Z - 2 * geometry["rack_board_hz"])
    assert geometry["rack_divider_dx"] == pytest.approx(geometry["rack_shelf_dx"])

    for entry in layout.RACK_ENTRIES:
        index = int(entry["index"])
        assert f"rack_e{index}_dx" in geometry and f"rack_e{index}_dy" in geometry
        assert geometry[f"rack_e{index}_floor"] == pytest.approx(float(entry["floor"]))
    assert not any(re.match(r"rack_(leg|lip|panel|block|rail)_", key) for key in geometry), (
        "dulapul are carcasă și rafturi, fără picioare ori buze")


def test_fiecare_compartiment_incape_cea_mai_mare_piesa() -> None:
    """Compartimentul de 0,5 m ține discul cel mai mare, așezat lângă fața deschisă."""
    front_x = float(layout.RACK["x"])
    for item in gear.FAMILIES:
        diameter = 2 * item.nominal.outer_radius * part.MESH_SCALE
        assert layout.RACK_ENTRY_M - diameter > 0.2, (
            f"{item.name}: Ø{diameter * 1000:.0f} mm într-un compartiment de "
            f"{layout.RACK_ENTRY_M * 1000:.0f} mm")
        radius = diameter / 2
        for entry in layout.RACK_ENTRIES:
            if entry["family"] is None:
                continue
            pick_x, _ = layout.rack_entry_center(int(entry["index"]))
            assert pick_x - front_x == pytest.approx(0.02), (
                "piesa stă la 2 cm în spatele feței deschise")
            supported = (pick_x + radius) - front_x
            assert supported > radius, (
                "centrul discului trebuie să stea pe blat, nu în aer")


def test_sablonul_are_carcasa_rafturi_si_despartitor() -> None:
    """Șablonul desenează exact dulapul: carcasa, trei rafturi și despărțitorul."""
    template = _read(MUJOCO_DIR / "scene" / "cell.xml")
    geometry = layout.scene_geometry()
    for name in ("rack_side_south", "rack_side_north", "rack_back", "rack_bottom",
                 "rack_top", "rack_shelf_1", "rack_shelf_2", "rack_shelf_3",
                 "rack_divider"):
        assert f'name="{name}"' in template, f"lipsește {name} din scenă"
    for prefix in ("rack_entry_", "rack_leg_", "rack_panel_", "rack_lip_", "rack_block_",
                   "rack_rail_"):
        assert prefix not in template, f"{prefix} a rămas din vechiul raft"
    used = {name for name in re.findall(r"\{([a-z0-9_]+)\}", template)
            if name.startswith("rack_")}
    missing = used - set(geometry)
    assert not missing, f"șablonul cere câmpuri lipsă din geometrie: {sorted(missing)}"


def test_lada_de_rebut_sta_sub_capatul_de_vest_al_benzii_2() -> None:
    """Lada e la capătul benzii, lată cât ea, cu marginea sub nivelul benzii."""
    geometry = layout.scrap_bin_geometry()
    rim = geometry["scrap_bin_wall_z"] + geometry["scrap_bin_wall_hz"]
    assert rim == pytest.approx(layout.SCRAP_BIN_RIM_Z)
    assert layout.SCRAP_BIN_RIM_Z < layout.BELT_SURFACE_Z - 0.2, (
        "marginea lăzii prea aproape de bandă: piesa s-ar sprijini pe perete")
    belt_x0, _, belt_y0, belt_y1 = layout.belt_bounds(layout.BELT_OUT)
    east = geometry["scrap_bin_cx"] + geometry["scrap_bin_hx"]
    assert 0.0 < belt_x0 - east < 0.1, (
        "gura lăzii trebuie să fie chiar sub capătul de vest al benzii")
    assert geometry["scrap_bin_cy"] - geometry["scrap_bin_hy"] <= belt_y0
    assert geometry["scrap_bin_cy"] + geometry["scrap_bin_hy"] >= belt_y1


# ---------------------------------------------------------------- benzile


def test_benzile_merg_in_sensurile_din_document() -> None:
    """O bandă alimentează spre est, cealaltă evacuă spre vest; documentul spune la fel."""
    text = _document()
    assert "banda 1 intră din `A7` spre est" in text
    assert "Banda 2 merge într-un singur sens, spre vest" in text
    assert belt.belt("belt_in").direction == (1.0, 0.0)
    assert belt.belt("belt_out").direction == (-1.0, 0.0)
    assert belt.belt("belt_in").key == "belt_in"


def test_viteza_de_suprafata_are_sensul_benzii() -> None:
    """Viteza de suprafață este parametru, ca driftul de uzură să poată intra ca factor."""
    for item in belt.belts():
        vx, vy = item.surface_velocity()
        assert math.copysign(1.0, vx) == item.direction[0]
        assert vy == 0.0
        assert math.hypot(vx, vy) == pytest.approx(item.surface_speed_mps)
        worn = item.surface_velocity(speed_scale=0.92)
        assert math.hypot(*worn) == pytest.approx(0.92 * math.hypot(vx, vy))


def test_forta_de_antrenare_este_limitata_de_frecare() -> None:
    """Banda trage proporțional cu diferența de viteză, dar niciodată peste limita de frecare."""
    item = belt.belt("belt_in")
    mass = 1.0
    at_rest = item.drag_force(mass, (0.0, 0.0))
    assert at_rest[0] == pytest.approx(
        belt.BELT_DRAG_GAIN_N_PER_KG_MPS * mass * item.surface_speed_mps)
    assert at_rest[0] < item.friction * mass * belt.GRAVITY_MPS2, (
        "la frecare nominală banda trebuie să prindă piesa, nu să alunece pe ea")
    # Apăsare mică: forța scade la limita de frecare.
    light = item.drag_force(mass, (0.0, 0.0), normal_force_n=1.0)
    assert math.hypot(*light) == pytest.approx(1.0)
    assert light[0] < at_rest[0]
    # Fără apăsare nu există frecare, deci nici forță.
    assert item.drag_force(mass, (0.0, 0.0), normal_force_n=0.0) == (0.0, 0.0)
    # Piesa deja la viteza benzii nu mai este împinsă.
    assert item.drag_force(mass, item.surface_velocity())[0] == pytest.approx(0.0)
    # Piesa mai rapidă decât banda este frânată, nu accelerată.
    assert item.drag_force(mass, (2 * item.surface_speed_mps, 0.0))[0] < 0.0


def test_banda_franeaza_alunecarea_laterala() -> None:
    """Frecarea se opune oricărei alunecări, nu doar celei din lungul benzii."""
    item = belt.belt("belt_out")
    force = item.drag_force(1.0, (item.surface_velocity()[0], 0.5))
    assert force[1] < 0.0
    slipping = item.drag_force(1.0, (item.surface_velocity()[0], 0.5), normal_force_n=1.0)
    assert math.hypot(*slipping) == pytest.approx(1.0)


def test_punctul_de_pe_banda_se_recunoaste() -> None:
    """Recunoașterea în plan: centrul fiecărei benzi, plus un punct din culoarul liber."""
    for item in belt.belts():
        x0, x1, y0, y1 = item.footprint
        assert belt.belt_at((x0 + x1) / 2, (y0 + y1) / 2) == item.key
        assert belt.belt_at(x0, y0) == item.key
    assert belt.belt_at(4.10, 2.90) is None, "punctul de sub mașină nu e pe nicio bandă"
    assert belt.belt_at(1.60, 3.30 - 0.25) is None, "între benzi nu e bandă"


def test_linia_de_oprire_este_tinta_din_plan() -> None:
    """Linia până la care banda mai împinge piesa este chiar ținta de preluare, pe amândouă."""
    cases = (
        # Banda 1: piesa intră pe la capătul vestic și e dusă spre est.
        ("belt_in", part.blank_stock().radius_m, belt.belt("belt_in").footprint[0]),
        # Banda 2: piesa e așezată de brațul 1 la punctul de așezare și merge spre vest.
        ("belt_out", part.NOMINAL_GEAR.outer_radius * part.MESH_SCALE,
         float(layout.target("belt2_drop")["x"])),
    )
    for key, radius, entry_x in cases:
        item = belt.belt(key)
        stop = item.stop_x()
        assert stop == pytest.approx(float(layout.target(layout.BELT_STOP_TARGETS[key])["x"]))
        x0, x1, _, _ = item.footprint
        assert x0 + radius < stop < x1 - radius, (
            f"{key}: piesa oprită la {stop:.3f} m ar atârna peste capătul benzii")
        # Piesa intră în amonte de linie, iar forța benzii o împinge spre ea.
        assert (entry_x - stop) * item.direction[0] < 0, f"{key}: intrarea nu e în amonte"
        assert math.copysign(1.0, item.drag_force(1.0, (0.0, 0.0))[0]) == item.direction[0]


def test_banda_nu_mai_impinge_piesa_dupa_linia_de_oprire() -> None:
    """Dincolo de linie banda își scoate forța; în amonte de ea, o aplică."""
    for key in ("belt_in", "belt_out"):
        item = belt.belt(key)
        stop = item.stop_x()
        upstream = stop - 0.05 * item.direction[0]
        downstream = stop + 0.05 * item.direction[0]
        assert not item.reached_stop(upstream), (
            f"{key}: piesa din amonte mai are de mers, deci banda o împinge")
        assert item.reached_stop(stop), f"{key}: piesa ajunsă la linie nu mai e împinsă"
        assert item.reached_stop(downstream), (
            f"{key}: piesa trecută de linie nu mai e împinsă")


# ---------------------------------------------------------------- yaw-ul și contactele


def test_yaw_urile_se_compara_pe_cerc() -> None:
    """Unghiurile se compară modulo 360°, altfel 348,5° și −11,5° par diferite."""
    assert part.yaw_delta_deg(348.5, -11.5) == pytest.approx(0.0, abs=1e-9)
    assert part.yaw_delta_deg(-11.5, 348.5) == pytest.approx(0.0, abs=1e-9)
    assert part.yaw_delta_deg(10.0, 350.0) == pytest.approx(20.0)
    assert part.yaw_delta_deg(350.0, 10.0) == pytest.approx(-20.0)
    assert abs(part.yaw_delta_deg(0.0, 180.0)) == pytest.approx(180.0)


def test_yaw_ul_cerut_se_citeste_inapoi_din_scena() -> None:
    """Rotația scrisă în scenă se recuperează din cuaternarul piesei."""
    for yaw in (0.0, 45.0, 90.0, 163.0, 348.5):
        quat = part.yaw_quat(yaw)
        assert sum(value * value for value in quat) == pytest.approx(1.0)
        assert part.yaw_delta_deg(part.yaw_from_quat(quat), yaw) == pytest.approx(0.0, abs=1e-9)


def test_autocontactele_unui_brat_sunt_normale() -> None:
    """Contactele dintre verigile aceluiași braț nu sunt o problemă de așezare."""
    pairs = ["arm1_hand↔arm1_link5", "arm2_hand↔arm2_link5", "belt_in↔blank_01"]
    assert checks.arm_foreign_contacts(pairs, ("arm1_", "arm2_")) == []


def test_bratul_care_atinge_celula_ori_celalalt_brat_este_semnalat() -> None:
    """Ce se caută: un braț care atinge celula, piesa ori brațul celălalt."""
    pairs = ["arm1_hand↔arm1_link5", "arm1_link3↔cnc_cabinet", "arm1_hand↔arm2_link4",
             "arm2_link6↔part_01"]
    assert checks.arm_foreign_contacts(pairs, ("arm1_", "arm2_")) == pairs[1:]


def test_falcile_care_ating_piesa_nu_sunt_o_problema_de_veriga() -> None:
    """Falcile sunt scula: ating piesa, banda și universalul prin definiție."""
    pairs = ["arm1_left_finger↔blank_01", "arm1_right_finger↔belt_out",
             "arm1_hand↔belt_in"]
    assert checks.arm_link_contacts(pairs, ("arm1_", "arm2_")) == pairs[2:]


# ---------------------------------------------------------------- mișcările brațelor


def test_fiecare_punct_de_lucru_are_loc_in_raza_bratului() -> None:
    """Locul de deasupra punctului și punctul însuși trebuie să intre în rază cu scula de față.

    Raza se măsoară până la flanșă, iar scula atârnă sub ea: `moves.approach_height` este exact
    întrebarea „mai am loc să ridic scula deasupra punctului ori port cu 10 cm mai puțin?".
    """
    tool_length_m = 0.103
    for arm in moves.ROUTES:
        base = np.array([layout.arm(arm)["x"], layout.arm(arm)["y"], layout.ARM_BASE_Z])
        for item in moves.route(arm, tool_length_m):
            height = moves.approach_height(item.name, tool_length_m)
            assert 0.0 < height <= moves.APPROACH_HEIGHT_M, (
                f"{item.name}: retragere de {height * 1000:.0f} mm")
            for place in (item.hover, item.point):
                distance = float(np.linalg.norm(np.array(place) - base))
                assert distance <= layout.ARM_REACH_M + 1e-9, (
                    f"{item.name}: {place} este la {distance:.3f} m, peste raza "
                    f"de {layout.ARM_REACH_M} m")


def test_rutele_acopera_tintele_din_plan() -> None:
    """Fiecare punct din rută este o țintă din plan, a brațului care îl deservește."""
    for arm, names in moves.ROUTES.items():
        for name in names:
            assert layout.target(name)["key"] == arm, (
                f"{name} aparține altui braț decât {arm}")


def test_rotirea_sculei_ceruta_de_plan_ajunge_la_punctul_de_lucru() -> None:
    """Rotirile cerute de plan sunt pentru puncte din scenă, iar ruta le duce mai departe."""
    for name in moves.TOOL_SPIN_DEG:
        assert layout.target(name) is not None
    for arm, names in moves.ROUTES.items():
        for item in moves.route(arm):
            expected = moves.TOOL_SPIN_DEG.get(item.name, 0.0)
            assert item.spin_deg == expected


def test_punctele_de_trecere_intra_in_raza_bratului() -> None:
    """O trecere este o poziție atinsă de braț ca oricare alta, cu scula de față.

    Flanșa stă cu lungimea sculei deasupra punctului de trecere, fiindcă trecerea se face cu scula
    îndreptată în jos, ca și coborârea pe punct.
    """
    tool_length_m = 0.103
    for arm in moves.ROUTES:
        base = np.array([layout.arm(arm)["x"], layout.arm(arm)["y"], layout.ARM_BASE_Z])
        for item in moves.route(arm, tool_length_m):
            for point in item.via:
                flange = np.array(point) - np.array(item.approach) * tool_length_m
                distance = float(np.linalg.norm(flange - base))
                assert distance <= layout.ARM_REACH_M + 1e-9, (
                    f"{item.name}: trecerea {point} pune flanșa la {distance:.3f} m, peste raza "
                    f"de {layout.ARM_REACH_M} m")


def test_drumul_unui_punct_urca_si_coboara_prin_toate_trecerile() -> None:
    """Ordinea pașilor: trecerile, apoi sus, jos și ridicarea înapoi la aceeași cotă."""
    for arm in moves.ROUTES:
        for item in moves.route(arm):
            steps = moves.stages(item)
            assert [stage for stage, _ in steps] == (
                [f"trecere {index}" for index, _ in enumerate(item.via, start=1)]
                + ["sus", "jos", "ridicare"])
            assert steps[-1][1] == item.hover
            assert dict(steps)["jos"] == item.point


def test_apropierea_de_dulap_este_inclinata_din_fata() -> None:
    """Dulapul se încarcă pe fața vestică, cu scula înclinată, fiindcă rafturile sunt suprapuse."""
    for entry in layout.RACK_ENTRIES:
        if entry["family"] is None:
            continue
        name = layout.rack_target(str(entry["family"]))
        north = int(entry["column"]) == 2
        expected = moves.RACK_APPROACH_NORTH if north else moves.RACK_APPROACH
        expected_deg = (moves.RACK_APPROACH_NORTH_DEG if north
                        else moves.RACK_APPROACH_DEG)
        assert layout.rack_column_of(name) == int(entry["column"])
        approach = moves.approach(name)
        assert approach == pytest.approx(expected)
        assert approach[0] > 0.0 and approach[1] == 0.0 and approach[2] < 0.0, (
            "scula vine din vest, de sus")
        angle = math.degrees(math.acos(-approach[2]))
        assert angle == pytest.approx(expected_deg)
        assert 0.0 < angle < 90.0, "coborârea de sus ar intra în raftul de deasupra"
    for name in ("belt1_pick", "belt2_drop", "belt2_pick", "cnc_chuck"):
        assert moves.approach(name) == moves.FROM_ABOVE


def test_fiecare_compartiment_are_o_trecere_in_fata_dulapului() -> None:
    """Trecerea stă în aer liber, în fața feței deschise, la cota de lucru plus 5 cm."""
    front_x = float(layout.RACK["x"])
    base = layout.arm("arm2")
    for arm in moves.ROUTES:
        for item in moves.route(arm):
            if item.name not in layout.RACK_TARGETS.values():
                assert item.via == moves.VIA.get(item.name, ())
                continue
            if layout.rack_shelf_of(item.name) == layout.RACK_SHELVES:
                assert item.via == (), "raftul de sus se atinge direct, fără trecere"
                continue
            assert len(item.via) == 1
            x, y, z = item.via[0]
            assert y == pytest.approx(item.point[1])
            assert x >= moves.RACK_VIA_X, "trecerea stă în fața dulapului, spre vest"
            assert x < front_x, "trecerea trebuie să fie în fața dulapului"
            assert math.hypot(x - float(base["x"]), y - float(base["y"])) >= (
                moves.RACK_VIA_MIN_RADIUS_M - 1e-9), (
                "trecerea intră în zona moartă a umărului")
            assert z == pytest.approx(item.point[2] + moves.RACK_VIA_LIFT_M)


# ---------------------------------------------------------------- încadrarea modulelor


def _imported_top_levels(source: str) -> set[str]:
    """Modulele de nivel superior importate de un fișier, inclusiv în interiorul funcțiilor."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


#: Modulele care ating motorul: se validează rulând `view.py --check`, din venv-ul geamănului.
#: `camera.py` randează cadrele sesiunii, `sensors.py` citește mărimile din `MjData`, `grasp.py`
#: leagă piesa de mînă prin egalități de tip weld, iar `grasp_cycle.py` duce ciclul întreg.
ENGINE_MODULES = ("scene.py", "cell.py", "arms.py", "camera.py", "sensors.py",
                  "part_render.py", "grasp.py", "grasp_cycle.py")


def test_modulele_pure_nu_importa_motorul() -> None:
    """`layout`, `part`, `belt`, `checks`, `povs`, `telemetry` și `part_images` se importă fără
    `mujoco`."""
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        if path.name in ENGINE_MODULES:
            continue
        imported = _imported_top_levels(_read(path))
        assert "mujoco" not in imported, (
            f"{path.name} importă mujoco; scena se validează din venv-ul geamănului (ADR 0012)")


def test_doar_modulele_de_motor_ating_motorul() -> None:
    """Lista modulelor care ating motorul este explicită: orice modul nou trebuie declarat aici."""
    touching = {path.name for path in PACKAGE_DIR.glob("*.py")
                if "mujoco" in _imported_top_levels(_read(path))}
    assert touching == set(ENGINE_MODULES)


def test_sablonul_scenei_are_sursa_pentru_fiecare_cota() -> None:
    """Niciun număr din scenă nu vine de altundeva decât din `cncdt_mj/layout.py`."""
    template = _read(MUJOCO_DIR / "scene" / "cell.xml")
    placeholders = set(re.findall(r"\{([a-z][a-z0-9_]*)\}", template))
    geometry = layout.scene_geometry()
    known = set(geometry) | {f"neg_{key}" for key in geometry}
    # Texturile nu sînt cote: `scene_template` le scrie în `runs/textures` și le dă XML-ului ca
    # drumuri absolute, fiindcă altfel compilările repetate corup heap-ul (MuJoCo 3.15.0).
    known |= {f"{name}_texture" for name in textures.TEXTURE_NAMES}
    assert placeholders <= known, (
        f"cote fără sursă în layout.scene_geometry(): {sorted(placeholders - known)}")


def test_comenzile_din_readme_exista_in_makefile() -> None:
    """Comenzile promise în README sunt exact țintele din `Makefile`."""
    readme = _read(MUJOCO_DIR / "README.md")
    makefile = _read(REPO_ROOT / "Makefile")
    targets = set(re.findall(r"^([a-z][a-z0-9-]*):", makefile, flags=re.MULTILINE))
    mentioned = set(re.findall(r"make (mj-[a-z-]+)", readme))
    assert mentioned, "README-ul geamănului nu pomenește nicio comandă `make mj-…`"
    assert mentioned <= targets, f"comenzi fără țintă în Makefile: {sorted(mentioned - targets)}"


# ---------------------------------------------------------------- verificarea cu motor


def test_scena_se_compileaza_si_cadrele_de_pe_flansa_contin_punctul_de_strangere() -> None:
    """Repetare scurtă a lui `view.py --check`, pentru venv-ul geamănului.

    În `.venv`-ul proiectului testul se sare: acolo `mujoco` nu există (ADR 0012). Verificarea
    completă rămâne `make mj-check`.
    """
    mujoco = pytest.importorskip("mujoco", reason="scena cere mujoco, din venv-ul geamănului")
    from cncdt_mj import arms, scene

    model, metadata = scene.build_model()
    data = mujoco.MjData(model)
    scene.apply_nominal(model, data, metadata["placements"])
    scene.settle(model, data)

    assert scene.metrics(model, data)["ncon"] > 0

    prefixes = tuple(f"{prefix}_" for prefix, _ in scene.ARM_MOUNTS)
    assert checks.arm_foreign_contacts(scene.contact_pairs(model, data), prefixes) == []

    # Camerele de pe flanșe privesc punctul de strângere, iar piesa ținută iese destul de mare în
    # cadru ca verdictul să se poată da din ea (`checks.MIN_WRIST_PART_PX`). Obiectivul stă în aer
    # liber, nu îngropat în mâna brațului (`checks.MIN_LENS_CLEARANCE_M`): altfel cadrul iese
    # „prin" material, fiindcă fețele din spate nu se randează.
    diameter_mm = 2 * part.gear_radius_m(metadata["part"]["family"]) * 1000
    for arm in arms.ARMS:
        view = scene.camera_view(model, data, f"{arm}_wrist_camera",
                                 arms.tool_point(model, data, arm))
        assert view["in_frame"], f"camera de pe {arm} nu are punctul de strângere în cadru"
        assert diameter_mm * float(view["px_per_mm"]) >= checks.MIN_WRIST_PART_PX, \
            f"camera de pe {arm} vede prea departe piesa ținută"
        clearance = scene.lens_clearance(model, data, f"{arm}_wrist_camera", arm)
        assert clearance >= checks.MIN_LENS_CLEARANCE_M, \
            f"obiectivul camerei de pe {arm} stă în materialul brațului ({clearance * 1000:.0f} mm)"
        # Razele din cîmp pot ieși prin deschiderea cochiliei mîinii, deci verificarea de mai sus
        # trece și cînd camera stă în cavitate. Măsurarea în toate direcțiile prinde poziția aceea.
        surround = scene.lens_surround_clearance(model, data, f"{arm}_wrist_camera", arm)
        assert surround >= checks.MIN_LENS_SURROUND_M, \
            f"camera de pe {arm} stă în cochilia mîinii ({surround * 1000:.0f} mm)"


def test_piesele_inguste_sint_prinse_cu_ambele_falci() -> None:
    """Ciclul complet trece pentru legătură și placă, cu contact pe ambele falci.

    Sînt cele două forme la care o abatere de 1–2 mm lăsa o falcă în aer: legătura are 28 mm
    lățime, iar placa se prinde pe latura de 56 mm. Testul acoperă centrarea măsurată în scenă și
    marja de ridicare pentru piesele subțiri.
    """
    mujoco = pytest.importorskip("mujoco", reason="ciclul cere mujoco")
    from cncdt_mj import grasp_cycle, scene

    for family in ("link_68", "plate_84"):
        model, metadata = scene.build_model(seed=7, family=family)
        data = mujoco.MjData(model)
        scene.apply_nominal(model, data, metadata["placements"])
        scene.settle(model, data)
        result = grasp_cycle.run_cycle(model, data, family, seed=7)
        assert result.failures == [], f"{family}: {result.failures}"
        assert result.grasp_success, f"{family}: o prindere a rămas fără piesă"
        for row in result.grasps:
            if row["action"] != "pick":
                continue
            assert row["left_contacts"] > 0, f"{family}: falca stîngă nu a atins piesa"
            assert row["right_contacts"] > 0, f"{family}: falca dreaptă nu a atins piesa"


def test_meshul_piesei_are_normale_plate_pe_fiecare_triunghi() -> None:
    """Fiecare triunghi are aceeași normală pe toate cele trei vîrfuri.

    Cu vîrfuri împărtășite între fața de sus și flanc, compilatorul alegea normala flancului
    pentru triunghiurile feței, iar piesa randa ca niște cioburi albe. Vîrfurile repetate per
    triunghi (triangle soup) fac randatorul să păstreze normala feței.
    """
    mujoco = pytest.importorskip("mujoco", reason="meshul compilat cere mujoco")
    from cncdt_mj import scene

    model, _ = scene.build_model()
    mesh_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_MESH, "machined_part_mesh")
    assert mesh_id >= 0
    start = int(model.mesh_vertadr[mesh_id])
    count = int(model.mesh_vertnum[mesh_id])
    normals = np.asarray(model.mesh_normal[start:start + count], dtype=float)
    triangles = normals.reshape(-1, 3, 3)
    assert np.allclose(triangles, triangles[:, :1, :], atol=1e-5), (
        "normalele nu sînt plate: un triunghi are vîrfuri cu normale diferite")
    assert np.allclose(np.linalg.norm(triangles[:, 0, :], axis=1), 1.0, atol=1e-4)


def test_luminile_bratelor_nu_arunca_umbra_pe_piesa() -> None:
    """Doar lumina celulei aruncă umbră; reflectoarele Panda luminează, dar nu umbresc.

    Reflectorul „top" al modelului Panda se lipește de fiecare braț și se rotește cu el. Umbra
    lui cădea peste piesa ținută ca o a doua siluetă, decalată, iar fața de sus ieșea vălurită.
    """
    mujoco = pytest.importorskip("mujoco", reason="luminile compilate cer mujoco")
    from cncdt_mj import scene

    model, _ = scene.build_model()
    for prefix in ("arm1", "arm2"):
        light = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_LIGHT, f"{prefix}_top")
        assert light >= 0, f"lipsa reflectorului {prefix}_top"
        assert not model.light_castshadow[light], f"{prefix}_top aruncă umbră"
    key = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_LIGHT, "key_light")
    assert key >= 0 and model.light_castshadow[key], "lumina celulei trebuie să umbrească"


def test_o_varianta_de_piesa_se_randeaza_ca_imagine_de_calitate(tmp_path: Path) -> None:
    """Randatorul de piese scrie un PNG cu conținut, la unghiul din plan.

    Se rulează doar cînd testele au `mujoco` (venv-ul geamănului). Un cadru negru ar trece de
    existența fișierului, dar ar avea cîteva sute de octeți; o piesă randată trece de pragul de
    mai jos, care prinde și o scenă fără lumină, și una cu camera îndreptată în altă parte.
    """
    pytest.importorskip("mujoco", reason="randarea cere mujoco, din venv-ul geamănului")
    from cncdt_mj import part_images, part_render

    variant = gear.build_variant(7, family="pinion_56")
    view = part_images.plan_views(0, 1)[0]
    path = part_render.render_variant(
        variant, tmp_path / "part.png", view, width=160, height=160)
    assert path.is_file()
    assert path.stat().st_size > 2000, "cadrul pare gol: verifică lumina și camera"
