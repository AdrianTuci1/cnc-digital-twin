"""Teste pentru generatorul procedural de piese.

Verifică determinismul, validitatea topologică a meshului (muchii, volum orientat) și faptul
că fiecare defect chiar schimbă geometria în felul așteptat. Nu cer Isaac Sim.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from cncdt.cnc_cell.parts import gear
from cncdt.cnc_cell.parts.gear import (
    DEFAULT_FAMILY,
    DEFECT_CLASSES,
    DEFECT_BOLT_PATTERN_ROTATED,
    DEFECT_BORE_MISSING,
    DEFECT_BORE_OVERSIZE,
    DEFECT_HOLE_MISSING,
    DEFECT_KEYWAY_MISSING,
    DEFECT_PASS,
    DEFECT_TOOTH_CHIPPED,
    DEFECT_TOOTH_COUNT_WRONG,
    DEFECT_TOOTH_MISSING,
    FAMILIES,
    FAMILY_BY_NAME,
    NOMINAL,
    VERDICT_PASS,
    VERDICT_REJECT,
    PartFamily,
    build_loops,
    build_variant,
    build_variant_from_params,
    family_of,
    label_of,
)
from cncdt.cnc_cell.parts.manifest import LabelRecord, read_manifest, write_manifest

SEEDS = (0, 1, 2, 7, 42, 1234)

# Familiile, ca listă de nume, pentru parametrizarea testelor.
FAMILY_IDS = tuple(family.name for family in FAMILIES)

# Volumul orientat, măsurat pe 200 de seed-uri per familie, cu marjă. Fereastra prinde o
# orientare întoarsă ori o piesă degenerată, nu o diferență fină de toleranță.
VOLUME_BOUNDS_MM3: dict[str, tuple[float, float]] = {
    "wheel_100": (60_000.0, 110_000.0),
    "flange_72": (20_000.0, 40_000.0),
    "bushing_68": (23_000.0, 48_000.0),
    "wheel_60": (21_000.0, 38_000.0),
    "pinion_56": (14_000.0, 24_000.0),
}


def family_cases() -> list[tuple[PartFamily, str]]:
    """Toate perechile (familie, clasă) care au sens. Pana lipsește la piesele fără pană."""
    return [
        (family, defect)
        for family in FAMILIES
        for defect in family.defect_classes()
    ]


# ------------------------------------------------------------------ ajutoare de verificare


def _edge_histogram(faces: np.ndarray) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = {}
    for triangle in faces:
        for first, second in (
            (int(triangle[0]), int(triangle[1])),
            (int(triangle[1]), int(triangle[2])),
            (int(triangle[2]), int(triangle[0])),
        ):
            key = (min(first, second), max(first, second))
            counts[key] = counts.get(key, 0) + 1
    return counts


def _signed_volume(vertices: np.ndarray, faces: np.ndarray) -> float:
    a = vertices[faces[:, 0]]
    b = vertices[faces[:, 1]]
    c = vertices[faces[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def _tip_cluster_count(params: gear.GearParams) -> int:
    """Câte sectoare de vârf are conturul. Un dinte lipsă scoate unul."""
    points, ends = build_loops(params)
    outer = points[: ends[0]]
    radius = np.linalg.norm(outer, axis=1)
    angles = np.arctan2(outer[:, 1], outer[:, 0]) % (2 * math.pi)
    tips = np.sort(angles[radius >= params.outer_radius - 1e-6])
    if len(tips) < 2:
        return len(tips)
    gaps = np.diff(tips)
    wrap = (tips[0] + 2 * math.pi) - tips[-1]
    return int(np.count_nonzero(gaps > math.radians(6.0)) + (1 if wrap > math.radians(6.0) else 0))


def _hole_centroids(params: gear.GearParams) -> list[tuple[float, float]]:
    points, ends = build_loops(params)
    starts = [0, *ends[:-1]]
    first_hole_ring = 2 if params.bore_radius > 0 else 1
    centroids: list[tuple[float, float]] = []
    for index in range(first_hole_ring, len(ends)):
        chunk = points[starts[index] : ends[index]]
        centroids.append((float(chunk[:, 0].mean()), float(chunk[:, 1].mean())))
    return centroids


# ------------------------------------------------------------------ determinism și validitate


def test_same_seed_gives_identical_mesh() -> None:
    first = build_variant(7)
    second = build_variant(7)
    assert first.defect == second.defect
    assert np.array_equal(first.vertices, second.vertices)
    assert np.array_equal(first.faces, second.faces)


def test_seeds_produce_variety() -> None:
    meshes = {build_variant(seed).vertices.tobytes() for seed in range(40)}
    assert len(meshes) > 20


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("family", FAMILIES, ids=FAMILY_IDS)
def test_mesh_is_closed_manifold(family: PartFamily, seed: int) -> None:
    variant = build_variant(seed, family=family.name)
    counts = _edge_histogram(variant.faces)
    wrong = {edge: count for edge, count in counts.items() if count != 2}
    assert not wrong, f"muchii care nu apar de exact două ori: {list(wrong.items())[:5]}"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("family", FAMILIES, ids=FAMILY_IDS)
def test_mesh_is_oriented_and_plausible(family: PartFamily, seed: int) -> None:
    variant = build_variant(seed, family=family.name)
    assert variant.faces.min() >= 0
    assert variant.faces.max() < len(variant.vertices)
    assert np.isfinite(variant.vertices).all()
    # Volum orientat pozitiv: normele ies spre exterior, meshul nu e întors pe dos.
    volume = _signed_volume(variant.vertices, variant.faces)
    low, high = VOLUME_BOUNDS_MM3[family.name]
    assert low < volume < high, f"volum neplauzibil la {family.name}: {volume}"


def test_meshes_have_no_duplicate_consecutive_vertices() -> None:
    for seed in SEEDS:
        variant = build_variant(seed)
        points, ends = build_loops(variant.params)
        start = 0
        for end in ends:
            ring = points[start:end]
            wrapped = np.vstack([ring, ring[:1]])
            distances = np.linalg.norm(np.diff(wrapped, axis=0), axis=1)
            assert distances.min() > 1e-9, f"inel cu vertecși duplicați în seed {seed}"
            start = end


# ------------------------------------------------------------------ etichete


@pytest.mark.parametrize("family,defect", family_cases(), ids=lambda item: getattr(item, "name", item))
def test_every_class_builds_with_matching_label(family: PartFamily, defect: str) -> None:
    for seed in SEEDS:
        variant = build_variant(seed, defect=defect, family=family.name)
        assert variant.family == family.name
        assert variant.defect == defect
        expected = VERDICT_PASS if defect == DEFECT_PASS else VERDICT_REJECT
        assert variant.verdict == expected
        assert variant.part_id == f"part-{seed:08d}"
        counts = _edge_histogram(variant.faces)
        assert counts and all(count == 2 for count in counts.values())


def test_tooth_count_never_multiple_of_five() -> None:
    # Simetria de ordin 5 ar face invizibilă dezalinierea dintre găuri și dinți. Regula este
    # a piesei de referință: 5 găuri la pas de 72° și un număr de dinți care nu e multiplu
    # de 5, deci ansamblul are simetrie de ordin 1.
    for seed in range(200):
        assert build_variant(seed).params.teeth % 5 != 0
    for seed in range(200):
        assert build_variant(seed, defect=DEFECT_TOOTH_COUNT_WRONG).params.teeth % 5 != 0


# ------------------------------------------------------------------ familiile de piese


@pytest.mark.parametrize("family", FAMILIES, ids=FAMILY_IDS)
def test_family_nominal_is_valid_and_leaves_room_for_defects(family: PartFamily) -> None:
    """Fiecare familie trece validarea și rămâne la distanță de acțiunea fiecărui defect.

    Marjele sunt cele care contează: alezajul mărit cu 2 mm nu are voie să ajungă în cercul
    de găuri, gaura deplasată cu 2,5 mm nu are voie să iasă din coroană, iar lățimile
    dinților trebuie să rămână valide și la 17 ori 19 dinți.
    """
    params = family.nominal
    keyway_depth = params.keyway_depth if params.keyway else 0.0
    assert params.bore_radius + keyway_depth + 2.0 < params.root_radius
    assert params.bolt_circle_radius - params.hole_radius > params.bore_radius + keyway_depth + 2.0
    assert params.bolt_circle_radius + params.hole_radius + 2.5 <= params.root_radius
    for teeth in (params.teeth, *gear.TEETH_WRONG):
        tip_half = math.radians(params.tooth_tip_width_deg) / 2
        root_half = math.radians(params.tooth_root_width_deg) / 2
        assert tip_half < root_half < math.pi / teeth, f"dinții nu ies la {teeth} dinți"


@pytest.mark.parametrize("family,defect", family_cases(), ids=lambda item: getattr(item, "name", item))
def test_every_defect_changes_the_geometry(family: PartFamily, defect: str) -> None:
    """Un defect care nu schimbă nimic ar da etichetă de rebut pe geometrie corectă.

    Este testul care apără tabelul de familii: o familie prea apropiată de o margine ar
    eșua aici, pentru că piesa ar ieși din propria siluetă ori ar rămâne neschimbată.
    """
    if defect == DEFECT_PASS:
        return
    for seed in SEEDS:
        reference = build_variant(seed, defect=DEFECT_PASS, family=family.name)
        variant = build_variant(seed, defect=defect, family=family.name)
        assert not np.array_equal(variant.vertices, reference.vertices), (
            f"{family.name}/{defect} nu schimbă geometria"
        )


def test_families_are_distinct_in_plan_view() -> None:
    """Familiile se deosebesc prin ce se vede de sus: diametru, dinți, alezaj și găuri."""
    signatures = {
        (
            family.diameter_mm,
            family.teeth,
            family.nominal.bore_radius,
            family.nominal.bolt_count,
        )
        for family in FAMILIES
    }
    assert len(signatures) == len(FAMILIES)
    diameters = sorted(family.diameter_mm for family in FAMILIES)
    gaps = [later - earlier for earlier, later in itertools.pairwise(diameters)]
    assert all(gap >= 4.0 for gap in gaps), diameters
    assert len({family.teeth for family in FAMILIES}) >= 4


def test_wrong_tooth_count_never_matches_another_family() -> None:
    """Un „număr greșit de dinți" nu are voie să arate ca piesa nominală a altei familii."""
    nominal_teeth = {family.teeth for family in FAMILIES}
    assert not nominal_teeth & set(gear.TEETH_WRONG)


@pytest.mark.parametrize("family", FAMILIES, ids=FAMILY_IDS)
def test_pattern_rotation_moves_the_holes_far_enough(family: PartFamily) -> None:
    """Rotirea de 15° a cercului de găuri trebuie să se vadă.

    Regula „numărul de dinți nu e multiplu de 5" este cazul particular al piesei de
    referință, cu 5 găuri: ea garantează că rotirea nu e o simetrie a ansamblului.
    Verificarea de aici prinde orice familie, măsurând direct deplasarea găurilor.
    """
    params = family.nominal
    offset_mm = params.bolt_circle_radius * math.radians(15.0)
    assert offset_mm >= 3.0, f"{family.name}: rotirea mută găurile doar {offset_mm:.1f} mm"


def test_family_without_keyway_has_no_keyway_defect() -> None:
    family = FAMILY_BY_NAME["flange_72"]
    assert family.has_keyway is False
    assert DEFECT_KEYWAY_MISSING not in family.defect_classes()
    with pytest.raises(ValueError):
        build_variant(1, defect=DEFECT_KEYWAY_MISSING, family=family.name)
    # Restul claselor rămân disponibile.
    assert set(family.defect_classes()) == set(DEFECT_CLASSES) - {DEFECT_KEYWAY_MISSING}


def test_family_lookup_defaults_and_rejects_unknown_names() -> None:
    assert family_of(None).name == DEFAULT_FAMILY
    assert family_of(FAMILY_BY_NAME["pinion_56"]).name == "pinion_56"
    with pytest.raises(ValueError):
        family_of("wheel_999")
    # Fără familie, generatorul lucrează pe piesa de referință.
    assert build_variant(7).family == DEFAULT_FAMILY
    assert np.array_equal(build_variant(7).vertices, build_variant(7, family=DEFAULT_FAMILY).vertices)


def test_family_does_not_disturb_the_reference_part() -> None:
    """Piesa de referință rămâne exact ce era înainte de familii."""
    reference = build_variant(7, defect=DEFECT_PASS, family="wheel_100")
    assert reference.params.teeth == NOMINAL.teeth
    assert reference.params.outer_radius == pytest.approx(NOMINAL.outer_radius, abs=0.3)
    assert _tip_cluster_count(reference.params) == NOMINAL.teeth


def test_smaller_families_fit_the_gripper() -> None:
    """Peste 83 mm piesa nu intră între fălci; sub asta prinderea poate fi reală."""
    for family in FAMILIES:
        if family.name == "wheel_100":
            assert family.diameter_mm > 83.0
            continue
        assert family.diameter_mm <= 83.0, family.name


# ------------------------------------------------------------------ efectul fiecărui defect


def test_pass_matches_nominal_outline() -> None:
    variant = build_variant(3, defect=DEFECT_PASS)
    assert _tip_cluster_count(variant.params) == NOMINAL.teeth


def test_missing_tooth_removes_one_tip_sector() -> None:
    variant = build_variant(5, defect=DEFECT_TOOTH_MISSING)
    assert len(variant.params.missing_teeth) == 1
    assert _tip_cluster_count(variant.params) == NOMINAL.teeth - 1


def test_chipped_tooth_has_a_notch_at_tip() -> None:
    variant = build_variant(5, defect=DEFECT_TOOTH_CHIPPED)
    params = variant.params
    assert len(params.chipped_teeth) == 1
    radius = np.linalg.norm(variant.vertices[:, :2], axis=1)
    target = params.outer_radius - params.chip_depth
    assert np.any(np.abs(radius - target) < 1.5), "vârful ciobit nu apare în contur"
    assert _tip_cluster_count(params) == NOMINAL.teeth


def test_wrong_tooth_count_changes_number_of_teeth() -> None:
    variant = build_variant(11, defect=DEFECT_TOOTH_COUNT_WRONG)
    assert variant.params.teeth in gear.TEETH_WRONG
    assert _tip_cluster_count(variant.params) == variant.params.teeth


def test_bore_oversize_opens_the_hole() -> None:
    variant = build_variant(2, defect=DEFECT_BORE_OVERSIZE)
    radius = np.linalg.norm(variant.vertices[:, :2], axis=1)
    assert radius.min() > NOMINAL.bore_radius + 1.0


def test_bore_missing_has_no_inner_ring() -> None:
    variant = build_variant(2, defect=DEFECT_BORE_MISSING)
    _points, ends = build_loops(variant.params)
    assert len(ends) == 1 + variant.params.bolt_count


def test_keyway_missing_removes_the_notch() -> None:
    with_keyway = build_variant(4, defect=DEFECT_PASS)
    without = build_variant(4, defect=DEFECT_KEYWAY_MISSING)
    assert without.params.keyway is False
    points, ends = build_loops(with_keyway.params)
    bore = points[ends[0] : ends[1]]
    assert np.linalg.norm(bore, axis=1).max() > with_keyway.params.bore_radius + 1.0
    points_wo, ends_wo = build_loops(without.params)
    bore_wo = points_wo[ends_wo[0] : ends_wo[1]]
    assert np.linalg.norm(bore_wo, axis=1).max() <= without.params.bore_radius + 1e-6


def test_missing_hole_removes_one_ring() -> None:
    variant = build_variant(9, defect=DEFECT_HOLE_MISSING)
    assert len(_hole_centroids(variant.params)) == variant.params.bolt_count - 1


def test_hole_off_circle_shifts_one_centroid() -> None:
    reference = build_variant(9, defect=DEFECT_PASS)
    shifted = build_variant(9, defect=gear.DEFECT_HOLE_OFF_CIRCLE)
    radii = [math.hypot(x, y) for x, y in _hole_centroids(shifted.params)]
    assert len(radii) == shifted.params.bolt_count
    assert max(radii) > reference.params.bolt_circle_radius + 1.0


def test_bolt_pattern_rotated_shifts_all_holes() -> None:
    variant = build_variant(9, defect=DEFECT_BOLT_PATTERN_ROTATED)
    assert variant.params.pattern_phase_deg == 15.0
    angles = sorted(
        math.degrees(math.atan2(y, x)) % 360 for x, y in _hole_centroids(variant.params)
    )
    # Prima gaură a unui cerc de 5 are pasul de 72°; fără rotire ar fi la 0°.
    assert min(min(abs(angle - 15.0), abs(angle - 15.0 + 360)) for angle in angles) < 2.0


# ------------------------------------------------------------------ manifest


def test_manifest_roundtrip(tmp_path) -> None:
    records = [
        LabelRecord(
            part_id=variant.part_id,
            seed=variant.seed,
            family=variant.family,
            verdict=variant.verdict,
            defect=variant.defect,
            params=variant.params.as_dict(),
        )
        for variant in (build_variant(seed) for seed in SEEDS)
    ]
    path = tmp_path / "parts.jsonl"
    written = write_manifest(path, records)
    assert written == len(records)
    restored = read_manifest(path)
    assert restored == records


def test_manifest_keeps_seed_defect_and_family_for_regeneration(tmp_path) -> None:
    path = tmp_path / "parts.jsonl"
    originals = [build_variant(seed, family=name)
                 for seed, name in zip(range(5), FAMILY_IDS)]
    write_manifest(path, [
        LabelRecord(
            part_id=v.part_id,
            seed=v.seed,
            family=v.family,
            verdict=v.verdict,
            defect=v.defect,
            params=v.params.as_dict(),
        )
        for v in originals
    ])
    for record, original in zip(read_manifest(path), originals):
        assert record.family == original.family
        # Forțarea clasei dă aceeași geometrie ca rularea aleatoare care a produs clasa.
        forced = build_variant(record.seed, defect=record.defect, family=record.family)
        assert np.array_equal(forced.vertices, original.vertices)
        assert np.array_equal(forced.faces, original.faces)
        # Iar din parametrii din manifest rezultă meshul exact.
        rebuilt = build_variant_from_params(
            record.seed, record.defect, record.params, record.family
        )
        assert rebuilt.family == record.family
        assert np.array_equal(rebuilt.vertices, original.vertices)
        assert np.array_equal(rebuilt.faces, original.faces)


def test_label_of_carries_the_family() -> None:
    for name in FAMILY_IDS:
        label = label_of(build_variant(3, family=name))
        assert label["family"] == name
        assert label["params"]["teeth"] == FAMILY_BY_NAME[name].teeth


def test_manifest_without_family_reads_as_the_reference_family() -> None:
    """Un rând din schema 1.0.0, de dinainte de familii, descrie piesa de referință."""
    line = (
        '{"part_id": "part-00000007", "params": {}, "schema_version": "1.0.0",'
        ' "seed": 7, "verdict": "PASS", "defect": "PASS"}'
    )
    record = LabelRecord.from_json(line)
    assert record.family == DEFAULT_FAMILY
    assert record.verdict == VERDICT_PASS
