"""Teste pentru citirea corpusului MuJoCo, fără MuJoCo și fără AWS."""

from __future__ import annotations

import json
from pathlib import Path

from cncdt_ml.mujoco_corpus import (
    build_windows,
    corpus_summary,
    frame_path,
    load_events,
    load_frames,
    read_jsonl,
    session_directories,
    session_info,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _session(tmp_path: Path) -> Path:
    directory = tmp_path / "wheel_100-7"
    frames = directory / "frames" / "cyc-000001"
    frames.mkdir(parents=True)
    (frames / "process-on_belt.png").write_bytes(b"png")
    (directory / "session.json").write_text(
        json.dumps(
            {
                "scenario": {"family": "wheel_100", "seed": 7},
                "totals": {"samples": 4, "frames": 1, "sim_time_s": 120.0},
                "cycles": [{"defect": "PASS"}],
            }
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        directory / "samples.jsonl",
        [
            {"ts_ms": 1000, "machine_state": "CUTTING", "spindle_load": 0.2, "vibration": 0.1, "temp_c": 30.0,
             "coolant_flow_lpm": 4.0, "joint_effort": 5.0, "axis_feed": 0.2,
             "tool_wear_index": 0.1, "joint_friction_index": 0.2},
            {"ts_ms": 2000, "machine_state": "CUTTING", "spindle_load": 0.4, "vibration": 0.3, "temp_c": 32.0,
             "coolant_flow_lpm": 4.0, "joint_effort": 6.0, "axis_feed": 0.3,
             "tool_wear_index": 0.1, "joint_friction_index": 0.2},
            {"ts_ms": 61000, "machine_state": "CUTTING", "spindle_load": 0.6, "vibration": 0.2, "temp_c": 40.0,
             "coolant_flow_lpm": 3.0, "joint_effort": 7.0, "axis_feed": 0.4,
             "tool_wear_index": 0.8, "joint_friction_index": 0.2, "is_synthetic_defect": True},
            {"ts_ms": 62000, "machine_state": "CUTTING", "spindle_load": 0.8, "vibration": 0.5, "temp_c": 42.0,
             "coolant_flow_lpm": 3.0, "joint_effort": 8.0, "axis_feed": 0.5,
             "tool_wear_index": 0.8, "joint_friction_index": 0.2, "is_synthetic_defect": True},
        ],
    )
    _write_jsonl(directory / "wear.jsonl", [{"ts_ms": 1000, "tool_health": 0.9}])
    _write_jsonl(directory / "events.jsonl", [{"event": "failure", "kind": "belt_slip"}])
    _write_jsonl(
        directory / "frames.jsonl",
        [
            {
                "cycle_id": "cyc-000001",
                "path": "frames/cyc-000001/process-on_belt.png",
                "family": "wheel_100",
                "defect": "PASS",
                "verdict": "PASS",
            }
        ],
    )
    return directory


def test_read_jsonl_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "lipsa.jsonl") == []


def test_session_directories_finds_nested_session(tmp_path: Path) -> None:
    directory = _session(tmp_path)
    assert session_directories(tmp_path) == (directory,)


def test_session_info_reads_declared_totals(tmp_path: Path) -> None:
    info = session_info(_session(tmp_path))
    assert info.name == "wheel_100-7"
    assert info.family == "wheel_100"
    assert info.seed == 7
    assert info.cycles == 1
    assert info.samples == 4
    assert info.frames == 1
    assert info.sim_time_s == 120.0


def test_build_windows_splits_on_utc_minute(tmp_path: Path) -> None:
    windows = build_windows(_session(tmp_path))
    assert len(windows) == 2
    assert windows[0]["window_key"] == 0
    assert windows[0]["window_start_ms"] == 0
    assert windows[0]["sample_count"] == 2
    assert windows[0]["family"] == "wheel_100"
    assert windows[0]["session"] == "wheel_100-7"
    assert windows[0]["defect_share"] == 0.0
    assert windows[0]["twin_label"] == "healthy"
    assert windows[0]["healthy"] is True
    assert windows[0]["axis_feed_mean"] == 0.25
    assert windows[1]["window_key"] == 1
    assert windows[1]["sample_count"] == 2
    assert windows[1]["tool_wear_index"] == 0.8
    assert windows[1]["tool_wear_index_max"] == 0.8
    assert windows[1]["defect_share"] == 1.0
    assert windows[1]["twin_label"] == "defect"
    assert windows[1]["healthy"] is False


def test_frame_helpers_resolve_relative_paths(tmp_path: Path) -> None:
    directory = _session(tmp_path)
    frames = load_frames(directory)
    assert len(frames) == 1
    assert frame_path(directory, frames[0]).is_file()
    assert load_events(directory)[0]["kind"] == "belt_slip"


def test_corpus_summary_lists_sessions(tmp_path: Path) -> None:
    _session(tmp_path)
    summary = corpus_summary(tmp_path)
    assert len(summary) == 1
    assert summary[0]["family"] == "wheel_100"
