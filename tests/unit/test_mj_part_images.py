"""Teste pentru planul imaginilor de calitate: unghiuri, distanțe, nume și index.

Modulul `part_images` este pur, deci testele rulează în `.venv`-ul proiectului, fără `mujoco`
(ADR 0012). Randarea propriu-zisă se verifică o dată, în `test_mujoco_scene.py`, cînd testele
rulează din venv-ul geamănului.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cncdt_mj import part_images


def _record(part_id: str = "part-00000007", family: str = "wheel_100",
            defect: str = "PASS", split_seed: int = 7) -> SimpleNamespace:
    return SimpleNamespace(
        part_id=part_id,
        seed=split_seed,
        family=family,
        defect=defect,
        verdict="PASS" if defect == "PASS" else "REJECT",
        params={"outer_radius": 50.0},
    )


def test_unghiurile_sint_deterministe_si_separa_de_geometrie() -> None:
    """Aceeași poziție și sămânță de randare dau aceleași unghiuri; alta poziție, altele."""
    first = part_images.plan_views(3, 4, render_seed=11)
    assert first == part_images.plan_views(3, 4, render_seed=11)
    assert first != part_images.plan_views(4, 4, render_seed=11)
    assert [view.index for view in first] == [0, 1, 2, 3]
    for view in first:
        assert 0.0 <= view.azimuth_deg < 360.0
        assert -90.0 <= view.elevation_deg <= -90.0 + part_images.MAX_TILT_DEG
        assert 45.0 <= view.light_elevation_deg <= 70.0


def test_numarul_de_cadre_este_validat() -> None:
    with pytest.raises(ValueError):
        part_images.plan_views(0, 0)


def test_distanta_creste_cu_familia_si_pastra_umplerea_cadrului() -> None:
    """Piesa mare cere o cameră mai departe, dar ocupă aceeași fracțiune din cadru."""
    small = part_images.camera_distance_m(28.0)
    large = part_images.camera_distance_m(50.0)
    assert large > small > 0.0
    expected = 0.100 / (2.0 * 0.41421356 * part_images.FILL_FRACTION)
    assert large == pytest.approx(expected, rel=1e-6)
    with pytest.raises(ValueError):
        part_images.camera_distance_m(0.0)


def test_calea_unei_imagini_este_relativa_si_stabila() -> None:
    view = part_images.plan_views(0, 1)[0]
    path = part_images.image_relpath(_record(), view)
    assert path == "images/wheel_100/PASS/part-00000007-v00.png"
    assert not Path(path).is_absolute()


def test_feliile_acopera_fiecare_piesa_o_singura_data() -> None:
    """Feliile sînt contigue, disjuncte și acoperă tot manifestul, inclusiv la rest."""
    for total, shards in ((2000, 8), (2001, 8), (10, 3), (3, 8)):
        covered: list[int] = []
        for shard in range(shards):
            start, count = part_images.shard_range(total, shard, shards)
            assert count >= 0
            covered.extend(range(start, start + count))
        assert sorted(covered) == list(range(total)), (total, shards)


def test_o_felie_invalida_este_refuzata() -> None:
    with pytest.raises(ValueError):
        part_images.shard_range(10, 0, 0)
    with pytest.raises(ValueError):
        part_images.shard_range(10, 3, 3)


def test_splitul_se_citeste_din_fisierele_generatorului(tmp_path: Path) -> None:
    (tmp_path / "parts-train.jsonl").write_text(
        json.dumps({"part_id": "part-00000001"}) + "\n", encoding="utf-8")
    (tmp_path / "parts-val.jsonl").write_text(
        json.dumps({"part_id": "part-00000002"}) + "\n", encoding="utf-8")
    splits = part_images.split_map(tmp_path)
    assert splits == {"part-00000001": "train", "part-00000002": "val"}
    assert part_images.split_of(_record("part-00000001"), splits) == "train"
    assert part_images.split_of(_record("part-00000003"), splits) == "all"


def test_acelasi_identificator_in_doua_splituri_este_refuzat(tmp_path: Path) -> None:
    for split in ("train", "test"):
        (tmp_path / f"parts-{split}.jsonl").write_text(
            json.dumps({"part_id": "part-00000001"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="două split-uri"):
        part_images.split_map(tmp_path)


def test_randul_din_index_poarta_eticheta_si_conditiile_de_randare() -> None:
    record = _record(defect="TOOTH_MISSING")
    view = part_images.plan_views(0, 1)[0]
    row = part_images.index_row(record, view, "images/wheel_100/TOOTH_MISSING/x.png", "train",
                                width=640, height=640)
    assert row["part_id"] == record.part_id
    assert row["family"] == "wheel_100"
    assert row["defect"] == "TOOTH_MISSING"
    assert row["verdict"] == "REJECT"
    assert row["split"] == "train"
    assert row["path"].endswith(".png")
    assert row["camera"] == "top"
    assert row["distance_m"] > 0.0


def test_indexul_se_imparte_pe_splituri(tmp_path: Path) -> None:
    rows = [
        {"part_id": "part-1", "split": "train", "path": "a.png"},
        {"part_id": "part-2", "split": "val", "path": "b.png"},
        {"part_id": "part-3", "split": "train", "path": "c.png"},
    ]
    written = part_images.write_indexes(tmp_path, rows)
    assert written["all"] == tmp_path / part_images.INDEX_FILE
    assert written["train"] == tmp_path / "images-train.jsonl"
    assert written["val"] == tmp_path / "images-val.jsonl"
    train = (tmp_path / "images-train.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["part_id"] for line in train] == ["part-1", "part-3"]
    everything = (tmp_path / part_images.INDEX_FILE).read_text(encoding="utf-8").splitlines()
    assert len(everything) == 3


def test_indexul_feliat_se_uneste_si_se_ordoneaza(tmp_path: Path) -> None:
    part_images.write_index(tmp_path / "images-shard-01.jsonl", [
        {"part_id": "part-2", "view": 0, "path": "b-0.png"},
        {"part_id": "part-1", "view": 1, "path": "a-1.png"},
    ])
    part_images.write_index(tmp_path / "images-shard-00.jsonl", [
        {"part_id": "part-1", "view": 0, "path": "a-0.png"},
    ])
    rows = part_images.merge_index(tmp_path)
    assert [row["path"] for row in rows] == ["a-0.png", "a-1.png", "b-0.png"]


def test_acelasi_cadru_in_doua_felii_este_refuzat(tmp_path: Path) -> None:
    for shard in ("00", "01"):
        part_images.write_index(tmp_path / f"images-shard-{shard}.jsonl", [
            {"part_id": "part-1", "view": 0, "path": f"{shard}.png"},
        ])
    with pytest.raises(ValueError, match="de două ori"):
        part_images.merge_index(tmp_path)
