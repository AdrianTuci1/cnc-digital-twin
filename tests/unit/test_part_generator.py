"""Teste pentru generatorul procedural de piese.

Verifică determinismul, validitatea topologică a meshului (muchii, volum orientat) și faptul
că fiecare defect chiar schimbă geometria în felul așteptat. Nu cer Isaac Sim.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from cncdt.cnc_cell.parts import gear
from cncdt.cnc_cell.parts.gear import (
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
    NOMINAL,
    VERDICT_PASS,
    VERDICT_REJECT,
    build_loops,
    build_variant,
    build_variant_from_params,
)
from cncdt.cnc_cell.parts.manifest import LabelRecord, read_manifest, write_manifest

SEEDS = (0, 1, 2, 7, 42, 1234)


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
def test_mesh_is_closed_manifold(seed: int) -> None:
    variant = build_variant(seed)
    counts = _edge_histogram(variant.faces)
    wrong = {edge: count for edge, count in counts.items() if count != 2}
    assert not wrong, f"muchii care nu apar de exact două ori: {list(wrong.items())[:5]}"


@pytest.mark.parametrize("seed", SEEDS)
def test_mesh_is_oriented_and_plausible(seed: int) -> None:
    variant = build_variant(seed)
    assert variant.faces.min() >= 0
    assert variant.faces.max() < len(variant.vertices)
    assert np.isfinite(variant.vertices).all()
    # Volum orientat pozitiv: normele ies spre exterior, meshul nu e întors pe dos.
    volume = _signed_volume(variant.vertices, variant.faces)
    assert 40_000 < volume < 120_000, f"volum neplauzibil: {volume}"


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


@pytest.mark.parametrize("defect", DEFECT_CLASSES)
def test_every_class_builds_with_matching_label(defect: str) -> None:
    for seed in SEEDS:
        variant = build_variant(seed, defect=defect)
        assert variant.defect == defect
        expected = VERDICT_PASS if defect == DEFECT_PASS else VERDICT_REJECT
        assert variant.verdict == expected
        assert variant.part_id == f"part-{seed:08d}"
        counts = _edge_histogram(variant.faces)
        assert counts and all(count == 2 for count in counts.values())


def test_tooth_count_never_multiple_of_five() -> None:
    # Simetria de ordin 5 ar face invizibilă dezalinierea dintre găuri și dinți.
    for seed in range(200):
        assert build_variant(seed).params.teeth % 5 != 0
    for seed in range(200):
        assert build_variant(seed, defect=DEFECT_TOOTH_COUNT_WRONG).params.teeth % 5 != 0


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


def test_manifest_keeps_seed_and_defect_for_regeneration(tmp_path) -> None:
    path = tmp_path / "parts.jsonl"
    originals = [build_variant(seed) for seed in range(5)]
    write_manifest(path, [
        LabelRecord(
            part_id=v.part_id,
            seed=v.seed,
            verdict=v.verdict,
            defect=v.defect,
            params=v.params.as_dict(),
        )
        for v in originals
    ])
    for record, original in zip(read_manifest(path), originals):
        # Forțarea clasei dă aceeași geometrie ca rularea aleatoare care a produs clasa.
        forced = build_variant(record.seed, defect=record.defect)
        assert np.array_equal(forced.vertices, original.vertices)
        assert np.array_equal(forced.faces, original.faces)
        # Iar din parametrii din manifest rezultă meshul exact.
        rebuilt = build_variant_from_params(record.seed, record.defect, record.params)
        assert np.array_equal(rebuilt.vertices, original.vertices)
        assert np.array_equal(rebuilt.faces, original.faces)
