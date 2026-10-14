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

from cncdt_mj import REPO_ROOT, belt, checks, layout, moves, part

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
        pattern = rf"{height.keyword}\w*[^.\n]{{0,40}}?la {value} m"
        assert re.search(pattern, paragraph), (
            f"{height.name} = {height.value:.2f} m nu are propoziția așteptată în paragraful "
            f"înălțimilor (căutat: „{height.keyword}… la {value} m”): {height.quote}"
        )


def test_documentul_și_scena_cad_de_acord_pe_camera_de_inspecție() -> None:
    """Cifrele scrise în document sunt exact cele calculate din geometrie."""
    text = _document()
    intrinsics = layout.inspection_camera_intrinsics()
    coverage = (f"{intrinsics['coverage_x_m'] * 1000:.0f} × "
                f"{intrinsics['coverage_y_m'] * 1000:.0f} mm")
    assert coverage in text, f"documentul nu spune „{coverage}”"
    px_per_mm = f"{intrinsics['px_per_mm']:.1f}".replace(".", ",")
    assert f"~{px_per_mm} px/mm" in text, f"documentul nu spune „~{px_per_mm} px/mm”"
    assert f"{layout.INSPECTION_CAMERA_FOVY:.0f}° pe verticală" in text
    assert f"{layout.INSPECTION_CAMERA_FRAME[0]}×{layout.INSPECTION_CAMERA_FRAME[1]}" in text
    clearance = f"{layout.INSPECTION_CAMERA_CLEARANCE_Z:.2f}".replace(".", ",")
    assert f"de la **{clearance} m**" in text, f"documentul nu spune „de la {clearance} m”"


# ---------------------------------------------------------------- camera de inspecție


def test_obiectivul_vede_defectele_din_catalog() -> None:
    """Rezoluția pe piesă: cât acoperă obiectivul și câți pixeli prinde cel mai fin defect."""
    intrinsics = layout.inspection_camera_intrinsics()
    assert intrinsics["distance_m"] == pytest.approx(layout.INSPECTION_CAMERA_CLEARANCE_Z)
    assert intrinsics["fovy_deg"] == layout.INSPECTION_CAMERA_FOVY
    assert intrinsics["coverage_x_m"] * 1000 == pytest.approx(178, abs=1)
    assert intrinsics["coverage_y_m"] * 1000 == pytest.approx(134, abs=1)
    assert intrinsics["px_per_mm"] == pytest.approx(3.6, abs=0.05)
    # Cel mai fin defect din catalog este o gaură dezaxată cu 2,5 mm; cel mai grosier, un dinte
    # ciobit de 4 mm. Amândouă trebuie să treacă de câțiva pixeli, altfel clasele nu se separă.
    assert 2.5 * intrinsics["px_per_mm"] >= 8
    assert 4.0 * intrinsics["px_per_mm"] >= 14
    # Câmpul pe orizontală iese din raportul de aspect, nu se afirmă separat.
    expected_fovx = math.degrees(2 * math.atan(
        math.tan(math.radians(intrinsics["fovy_deg"]) / 2)
        * layout.INSPECTION_CAMERA_FRAME[0] / layout.INSPECTION_CAMERA_FRAME[1]))
    assert intrinsics["fovx_deg"] == pytest.approx(expected_fovx)


def test_capul_optic_sta_deasupra_axei_benzii_2() -> None:
    """Camera privește în jos, deci axul ei optic stă peste subiectul din plan."""
    geometry = layout.camera_geometry()
    assert geometry["cam_z"] == pytest.approx(layout.inspection_camera_z())
    assert geometry["cam_z"] - layout.BELT_SURFACE_Z == pytest.approx(
        layout.INSPECTION_CAMERA_CLEARANCE_Z)
    assert (geometry["cam_x"], geometry["cam_y"]) == (
        pytest.approx(float(layout.CAMERAS[0]["x"])),
        pytest.approx(layout.center(layout.BELT_OUT)[1]))
    # Obiectivul atârnă sub corpul camerei, iar tija care îl ține are lungime pozitivă: capul nu
    # poate sta chiar la consolă.
    assert geometry["cam_lens_dz"] < 0
    assert geometry["cam_strut_hz"] > 0
    assert geometry["cam_z"] + layout.CAMERA_BODY_HALF < layout.MACHINE_COVER_Z


def test_consola_se_prinde_de_coltul_sud_vest_al_masinii() -> None:
    """Consola pornește de pe mașină, se așază pe capac și ajunge deasupra axului benzii 2."""
    geometry = layout.camera_geometry()
    machine = layout.machine_geometry()
    assert machine["cnc_cx"] - machine["cnc_hx"] < geometry["cam_mount_x"] < machine["cnc_cx"]
    assert machine["cnc_cy"] - machine["cnc_hy"] < geometry["cam_mount_y"] < machine["cnc_cy"]
    assert geometry["cam_boom_z"] == pytest.approx(
        layout.MACHINE_COVER_Z + layout.CAMERA_BOOM_HALF_Z)

    # Lungimea consolei este distanța dintre montaj și ax, centrul ei stă la jumătatea ei, iar
    # capătul ei ajunge deasupra axului optic.
    reach = math.hypot(geometry["cam_dx"], geometry["cam_dy"])
    assert geometry["cam_boom_hx"] == pytest.approx(reach / 2)
    assert (geometry["cam_mount_x"] + geometry["cam_boom_dx"],
            geometry["cam_mount_y"] + geometry["cam_boom_dy"]) == (
        pytest.approx((geometry["cam_mount_x"] + geometry["cam_x"]) / 2),
        pytest.approx((geometry["cam_mount_y"] + geometry["cam_y"]) / 2))
    assert (geometry["cam_mount_x"] + 2 * geometry["cam_boom_dx"],
            geometry["cam_mount_y"] + 2 * geometry["cam_boom_dy"]) == (
        pytest.approx(geometry["cam_x"]), pytest.approx(geometry["cam_y"]))

    # Orientarea consolei este un cuaternar de yaw, iar unghiul lui este chiar direcția consolei.
    assert geometry["cam_boom_qw"] ** 2 + geometry["cam_boom_qz"] ** 2 == pytest.approx(1.0)
    yaw = 2 * math.atan2(geometry["cam_boom_qz"], geometry["cam_boom_qw"])
    assert math.cos(yaw) == pytest.approx(geometry["cam_dx"] / reach)
    assert math.sin(yaw) == pytest.approx(geometry["cam_dy"] / reach)


def test_o_cota_prea_mare_a_capului_optic_este_refuzata() -> None:
    """Capul optic nu poate trece peste consolă: geometria refuză, în loc să dea numere absurde."""
    with pytest.raises(ValueError):
        layout.camera_geometry(camera_z=layout.MACHINE_COVER_Z)
    with pytest.raises(ValueError):
        layout.camera_geometry(camera_z=layout.MACHINE_COVER_Z + 0.5)


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
    """Blocul brut stă în ținta de preluare a benzii 1, piesa mașinată sub axul camerei."""
    placements = {item.state: item for item in layout.part_placements()}
    blank = placements[layout.STATE_BLANK]
    machined = placements[layout.STATE_MACHINED]
    pick = layout.target("belt1_pick")
    assert blank.x == pytest.approx(belt.belt("belt_in").stop_x()), (
        "blocul brut stă la linia de oprire a benzii 1, adică în dreptul țintei de preluare")
    assert blank.y == pytest.approx(float(pick["y"]))
    assert blank.surface_z == layout.BELT_SURFACE_Z
    assert machined.x == pytest.approx(float(layout.CAMERAS[0]["x"]))
    assert machined.y == pytest.approx(layout.center(layout.BELT_OUT)[1])
    assert machined.surface_z == layout.BELT_SURFACE_Z


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
ENGINE_MODULES = ("scene.py", "cell.py", "arms.py")


def test_modulele_pure_nu_importa_motorul() -> None:
    """`layout`, `part`, `belt` și `checks` se importă fără `mujoco`, deci merg testate în `.venv`."""
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


def test_scena_se_compileaza_si_camera_vede_piesa() -> None:
    """Repetare scurtă a lui `view.py --check`, pentru venv-ul geamănului.

    În `.venv`-ul proiectului testul se sare: acolo `mujoco` nu există (ADR 0012). Verificarea
    completă, cu eșantionarea implicită, rămâne `make mj-check`.
    """
    mujoco = pytest.importorskip("mujoco", reason="scena cere mujoco, din venv-ul geamănului")
    from cncdt_mj import scene

    model, metadata = scene.build_model()
    data = mujoco.MjData(model)
    scene.apply_nominal(model, data, metadata["placements"])
    scene.settle(model, data)

    assert scene.metrics(model, data)["ncon"] > 0

    clearance = scene.camera_clearance(model, data, samples=50)
    for arm, distance in clearance["sampled"].items():
        assert distance >= checks.MIN_CAMERA_CLEARANCE_M, f"{arm}: {distance:.3f} m"

    machined = next(body for body, place in metadata["placements"].items()
                    if place["state"] == layout.STATE_MACHINED)
    framing = scene.camera_framing(model, data, "camera_quality", machined,
                                   layout.INSPECTION_CAMERA_FRAME[1])
    assert framing["in_frame"]
    assert framing["px_per_mm"] >= checks.MIN_INSPECTION_PX_PER_MM

    prefixes = tuple(f"{prefix}_" for prefix, _ in scene.ARM_MOUNTS)
    assert checks.arm_foreign_contacts(scene.contact_pairs(model, data), prefixes) == []
