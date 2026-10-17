"""Teste pentru verificarea și planul de urcare al corpusului de cadre.

`publish_frames.py` este stdlib-only plus `cncdt_mj.telemetry` (pur), deci testele rulează în
`.venv`-ul proiectului, fără `mujoco` și fără AWS.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MUJOCO_DIR = Path(__file__).resolve().parents[2] / "simulation" / "mujoco"
if str(MUJOCO_DIR) not in sys.path:
    sys.path.insert(0, str(MUJOCO_DIR))

import publish_frames  # noqa: E402


def _row(index: int, pov: str = "arm1_grasp", stage: str = "belt1_pick-jos") -> dict:
    return {
        "part_id": f"part-{index:08d}",
        "family": "wheel_100",
        "defect": "TOOTH_MISSING" if index % 2 else "PASS",
        "verdict": "REJECT" if index % 2 else "PASS",
        "pov": pov,
        "stage": stage,
        "camera": "arm1_wrist_camera",
        "path": f"frames/cyc-{index:06d}/frame-{index}.png",
        "width": 640,
        "height": 480,
        "split": "train",
    }


def _session(root: Path, name: str = "wheel_100-7", count: int = 3) -> Path:
    directory = root / name
    rows = [_row(index, stage=f"belt1_pick-{stage}") for index, stage in
            enumerate(("sus", "jos", "ridicare"))][:count]
    for row in rows:
        target = directory / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"PNG")
    (directory / "frames.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (directory / "session.json").write_text(json.dumps({
        "scenario": {"family": "wheel_100", "seed": 7},
        "totals": {"cycles": 1, "frames": len(rows), "sim_time_s": 58.0},
    }), encoding="utf-8")
    return directory


def test_sesiunea_completa_trece_verificarea(tmp_path: Path) -> None:
    directory = _session(tmp_path)
    session = publish_frames.load_session(directory)
    assert publish_frames.validate_session(session) == []
    assert [row["stage"] for row in session["rows"]] == [
        "belt1_pick-sus", "belt1_pick-jos", "belt1_pick-ridicare"]


def test_un_png_lipsa_opreste_urcarea(tmp_path: Path) -> None:
    directory = _session(tmp_path)
    (directory / "frames/cyc-000001/frame-1.png").unlink()
    problems = publish_frames.validate_session(publish_frames.load_session(directory))
    assert any("lipsește" in problem for problem in problems)


def test_indexul_nu_poate_trimite_la_altceva_decit_png(tmp_path: Path) -> None:
    directory = _session(tmp_path)
    rows = publish_frames.read_jsonl(directory / "frames.jsonl")
    rows[0]["path"] = "frames/cyc-000001/frame-0.txt"
    (directory / "frames/cyc-000001/frame-0.txt").write_bytes(b"x")
    (directory / "frames.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    problems = publish_frames.validate_session(publish_frames.load_session(directory))
    assert any("nu este PNG" in problem for problem in problems)


def test_manifestul_numara_cadrele_si_clasele(tmp_path: Path) -> None:
    directory = _session(tmp_path)
    session = publish_frames.load_session(directory)
    manifest = publish_frames.run_manifest(
        [session], run="run-test", bucket="exemplu", prefix="training-data/grasp",
        day="2026-10-17", revision="abc1234")
    assert manifest["totals"]["frames"] == 3
    assert manifest["dataset"]["grasp_frames"] == 3
    assert manifest["defects"] == {"PASS": 2, "TOOTH_MISSING": 1}
    assert manifest["location"] == (
        "s3://exemplu/training-data/grasp/dt=2026-10-17/run-test/")


def test_urcarea_include_doar_cadrele_indexul_si_sesiunea(tmp_path: Path) -> None:
    directory = _session(tmp_path)
    session = publish_frames.load_session(directory)
    commands = publish_frames.upload_commands(
        [session], "exemplu", "training-data/grasp", "2026-10-17", "run-test")
    assert len(commands) == 3
    joined = "\n".join(" ".join(command) for command in commands)
    assert "frames --recursive" in joined
    assert "frames.jsonl" in joined
    assert "session.json" in joined
    assert "samples.jsonl" not in joined
