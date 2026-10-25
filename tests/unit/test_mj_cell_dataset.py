"""Teste pentru datasetul de inspecție al celulei, fără MuJoCo și fără AWS.

Se apără trei lucruri: se păstrează numai cadrul care decide ruta, ciclurile cu prinderi ratate
nu ajung în dataset, iar aceeași variantă nu apare în două seturi.
"""

from __future__ import annotations

import json
from pathlib import Path

from cncdt_mj import cell_dataset


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _session(root: Path, name: str = "wheel_100-1000") -> Path:
    directory = root / name
    frames = directory / "frames" / "cyc-000001"
    frames.mkdir(parents=True)
    (frames / "arm2_wrist-belt2_pick.png").write_bytes(b"wrist-1")
    (frames / "arm2_wrist-belt2_pick-2.png").write_bytes(b"wrist-2")
    (frames / "arm1_wrist-belt1_pick.png").write_bytes(b"arm1")
    (directory / "session.json").write_text(
        json.dumps({
            "cycles": [
                {"cycle_id": "cyc-000001", "grasp_success": True},
                {"cycle_id": "cyc-000002", "grasp_success": False},
            ]
        }),
        encoding="utf-8",
    )
    _write_jsonl(directory / "frames.jsonl", [
        {"pov": "arm2_wrist", "stage": "belt2_pick", "cycle_id": "cyc-000001",
         "path": "frames/cyc-000001/arm2_wrist-belt2_pick.png",
         "variant_id": "part-00001000", "part_id": "part-000001",
         "family": "wheel_100", "defect": "PASS", "verdict": "PASS"},
        {"pov": "arm2_wrist", "stage": "belt2_pick", "cycle_id": "cyc-000002",
         "path": "frames/cyc-000001/arm2_wrist-belt2_pick-2.png",
         "variant_id": "part-00001001", "part_id": "part-000002",
         "family": "wheel_100", "defect": "BORE_MISSING", "verdict": "REJECT"},
        {"pov": "arm1_wrist", "stage": "belt1_pick", "cycle_id": "cyc-000001",
         "path": "frames/cyc-000001/arm1_wrist-belt1_pick.png",
         "variant_id": "part-00001000", "part_id": "part-000001",
         "family": "wheel_100", "defect": "PASS", "verdict": "PASS"},
    ])
    return directory


def test_only_the_sorting_frame_of_a_successful_cycle_is_kept(tmp_path: Path) -> None:
    rows = cell_dataset.sorting_frames(_session(tmp_path))

    assert len(rows) == 1
    assert rows[0]["cycle_id"] == "cyc-000001"
    assert rows[0]["pov"] == "arm2_wrist"
    assert Path(rows[0]["source"]).exists()


def test_failed_grasp_can_be_included_on_request(tmp_path: Path) -> None:
    rows = cell_dataset.sorting_frames(_session(tmp_path), require_success=False)

    assert [row["cycle_id"] for row in rows] == ["cyc-000001", "cyc-000002"]


def test_collect_walks_every_session(tmp_path: Path) -> None:
    _session(tmp_path, "wheel_100-1000")
    _session(tmp_path, "hex_68-1002")

    rows = cell_dataset.collect(tmp_path)

    assert len(rows) == 2
    assert {row["session"] for row in rows} == {"wheel_100-1000", "hex_68-1002"}


def _rows(family: str, count: int) -> list[dict]:
    return [{
        "variant_id": f"{family}-{index}",
        "family": family,
        "source": "/tmp/does-not-matter.png",
        "path": "frames/x.png",
        "part_id": f"part-{index}",
        "defect": "PASS" if index % 2 else "BORE_MISSING",
        "verdict": "PASS" if index % 2 else "REJECT",
        "cycle_id": f"cyc-{index}",
        "session": f"{family}-1",
    } for index in range(count)]


def test_split_keeps_variants_apart_and_covers_the_families() -> None:
    rows = _rows("wheel_100", 30) + _rows("hex_68", 30)

    splits = cell_dataset.split_variants(rows, seed=7)

    seen = [row["variant_id"] for name in cell_dataset.SPLITS for row in splits[name]]
    assert len(seen) == len(set(seen)) == 60
    for split in cell_dataset.SPLITS:
        assert {row["family"] for row in splits[split]} == {"wheel_100", "hex_68"}


def test_small_family_still_gets_all_three_sets() -> None:
    splits = cell_dataset.split_variants(_rows("plate_84", 3), seed=7)

    assert [len(splits[name]) for name in cell_dataset.SPLITS] == [1, 1, 1]


def test_write_dataset_copies_images_and_writes_the_training_indexes(tmp_path: Path) -> None:
    session = _session(tmp_path)
    rows = cell_dataset.sorting_frames(session)
    splits = cell_dataset.split_variants(rows, seed=7)

    counts = cell_dataset.write_dataset(splits, tmp_path / "dataset")

    assert counts == {"train": 1, "val": 0, "test": 0}
    index = (tmp_path / "dataset" / "images-train.jsonl").read_text(encoding="utf-8")
    item = json.loads(index)
    assert item["path"] == "images/wheel_100-1000-part-00001000.png"
    assert item["family"] == "wheel_100"
    assert (tmp_path / "dataset" / item["path"]).read_bytes() == b"wrist-1"
