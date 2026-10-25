"""Teste pentru partea de feature engineering offline.

Trei lucruri se verifică aici:

1. rețeta comisă este identică cu cea generată din cod (nu poate rămâne în urmă);
2. `build_features.py` produce coloanele promise, pe JSON Lines, fără dependențe externe;
3. versiunea de rețetă cerută de un pipeline se compară cu cea din cod.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import build_features
import recipe as recipe_module
from cncdt_ml import offline
from cncdt_ml.schema import ENRICHED_COLUMNS

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_RECIPE = REPO_ROOT / "ml" / "feature_engineering" / "flows" / "predictive_maintenance.recipe.json"


def window_record(index: int, *, device: str = "cnc-cell-01", wear: float = 0.1) -> dict:
    """O fereastră din contract, cu valori care cresc monoton."""
    record = {column: None for column in ENRICHED_COLUMNS}
    record.update(
        {
            "schema_version": "1.0.0",
            "device_id": device,
            "cell_id": "cell-a",
            "topic": "cncdt/dev/cnc/cell-a/telemetry",
            "ts_ms": 1_700_000_000_000 + index * 60_000,
            "received_at": "2026-01-01T00:00:00Z",
            "window_s": 60,
            "sample_count": 600,
            "machine_state": "CUTTING",
            "cycle_id": f"cyc-{index:06d}",
            "part_id": f"part-{index:06d}",
            "spindle_load_mean": 0.5 + 0.02 * index,
            "spindle_load_p95": 0.6 + 0.02 * index,
            "spindle_load_std": 0.05,
            "vibration_rms": 1.0 + 0.05 * index,
            "vibration_peak": 3.0 + 0.05 * index,
            "vibration_std": 0.3,
            "tool_wear_index": wear,
            "coolant_flow_lpm": 5.0,
            "temp_c_mean": 60.0 + 0.2 * index,
            "joint_friction_index": 0.1,
            "anomaly_score": 0.1,
            "anomaly_detected": False,
            "model_version": "thresholds",
            "is_synthetic_defect": False,
        }
    )
    return record


def write_input(path: Path, count: int = 12) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for index in range(count):
            handle.write(json.dumps(window_record(index)) + "\n")


# ---------------------------------------------------------------- rețetă
def test_committed_recipe_matches_generated() -> None:
    assert COMMITTED_RECIPE.read_text(encoding="utf-8") == recipe_module.render_recipe_json()


def test_recipe_covers_every_computed_column() -> None:
    assert recipe_module.verify_columns() == []


def test_recipe_steps_are_numbered_in_order() -> None:
    steps = recipe_module.steps()
    assert [step.step for step in steps] == list(range(1, len(steps) + 1))


def test_recipe_declares_the_rolling_and_lag_columns() -> None:
    declared = set(recipe_module.declared_columns())
    assert set(offline.DEFAULT_SPEC.rolling_columns()) <= declared
    assert set(offline.DEFAULT_SPEC.lag_columns()) <= declared
    assert {"health_label", "wear_severity", "is_anomaly", "rul_hours_proxy"} <= declared


def test_rolling_step_is_marked_as_needing_a_custom_transform() -> None:
    rolling = [step for step in recipe_module.steps() if step.step == 6][0]
    assert rolling.requires_custom_transform is True
    assert "cncdt_ml.offline" in rolling.notes


# ---------------------------------------------------------------- build_features
def test_print_recipe_matches_committed_file(capsys) -> None:
    assert build_features.main(["--print-recipe"]) == 0
    assert capsys.readouterr().out == COMMITTED_RECIPE.read_text(encoding="utf-8")


def test_build_features_writes_jsonl(tmp_path) -> None:
    source = tmp_path / "telemetry.jsonl"
    target = tmp_path / "features.jsonl"
    write_input(source)

    assert build_features.main(["--input", str(source), "--output", str(target)]) == 0

    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 12
    for column in offline.feature_columns():
        assert column in rows[0], f"lipsește coloana {column}"
    # Contractul rămâne intact în ieșire.
    assert set(ENRICHED_COLUMNS) <= set(rows[0])
    assert rows[3]["spindle_load_mean_roll3_mean"] == pytest.approx(0.54)


def test_build_features_supports_csv(tmp_path) -> None:
    source = tmp_path / "telemetry.jsonl"
    target = tmp_path / "features.csv"
    write_input(source)
    assert build_features.main(["--input", str(source), "--output", str(target)]) == 0
    header = target.read_text(encoding="utf-8").splitlines()[0]
    assert "health_label" in header
    assert "vibration_rms_roll5_mean" in header


def test_build_features_reads_a_directory(tmp_path) -> None:
    folder = tmp_path / "input"
    folder.mkdir()
    write_input(folder / "part-1.jsonl", 5)
    write_input(folder / "part-2.jsonl", 7)
    target = tmp_path / "out.jsonl"
    assert build_features.main(["--input", str(folder), "--output", str(target)]) == 0
    assert len(target.read_text(encoding="utf-8").splitlines()) == 12


def test_build_features_limit_and_dry_run(tmp_path, capsys) -> None:
    source = tmp_path / "telemetry.jsonl"
    write_input(source)
    target = tmp_path / "features.jsonl"
    code = build_features.main(
        ["--input", str(source), "--output", str(target), "--limit", "4", "--dry-run"]
    )
    assert code == 0
    assert not target.exists()
    out = capsys.readouterr().out
    report = json.loads(out[out.index("{") : out.rindex("}") + 1])
    assert report["rows"] == 4
    assert "health_label" in report["columns"]


def test_build_features_rejects_unknown_spec_version(tmp_path) -> None:
    source = tmp_path / "telemetry.jsonl"
    write_input(source)
    with pytest.raises(SystemExit) as excinfo:
        build_features.main(
            [
                "--input",
                str(source),
                "--output",
                str(tmp_path / "x.jsonl"),
                "--spec-version",
                "pm-features-v99",
            ]
        )
    assert excinfo.value.code == 2


def test_build_features_uses_parquet_for_a_job_directory(tmp_path) -> None:
    # Fără pyarrow, scriptul trebuie să se oprească cu un mesaj clar, nu cu urmă de stivă.
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        pytest.skip("pyarrow nu este instalat în acest mediu")

    source = tmp_path / "telemetry.jsonl"
    write_input(source, 3)
    output_dir = tmp_path / "output"
    assert build_features.main(["--input", str(source), "--output", str(output_dir)]) == 0
    assert (output_dir / "features.parquet").is_file()


def test_column_order_starts_with_the_contract() -> None:
    columns = build_features.column_order([window_record(0), {"coloana_extra": 1}])
    assert columns[: len(ENRICHED_COLUMNS)] == list(ENRICHED_COLUMNS)
    assert columns[-1] == "coloana_extra"


def test_window_features_can_be_limited_to_cutting() -> None:
    """Fereastra numără toate eșantioanele, dar statisticile vin doar din așchiere."""
    from cncdt_ml.features import compute_window_features

    samples = [
        {"machine_state": "IDLE", "spindle_load": 0.0, "vibration": 0.0, "temp_c": 22.0,
         "coolant_flow_lpm": 0.0, "axis_feed": 0.0},
        {"machine_state": "LOADING", "spindle_load": 1.0, "vibration": 9.0, "temp_c": 40.0,
         "coolant_flow_lpm": 9.0, "axis_feed": 1.0},
        {"machine_state": "CUTTING", "spindle_load": 0.6, "vibration": 0.4, "temp_c": 24.0,
         "coolant_flow_lpm": 6.0, "axis_feed": 0.08},
        {"machine_state": "CUTTING", "spindle_load": 0.8, "vibration": 0.6, "temp_c": 26.0,
         "coolant_flow_lpm": 6.0, "axis_feed": 0.08},
    ]

    features = compute_window_features(samples, only_states=("CUTTING",))

    assert features.sample_count == 4
    assert features.extras["conditioned_samples"] == 2
    assert features.spindle_load_mean == pytest.approx(0.7)
    assert features.temp_c_max == pytest.approx(26.0)
    assert features.coolant_flow_lpm == pytest.approx(6.0)


def test_window_without_cutting_has_no_statistics() -> None:
    from cncdt_ml.features import compute_window_features

    features = compute_window_features(
        [{"machine_state": "IDLE", "spindle_load": 0.01, "vibration": 0.02, "temp_c": 22.0,
          "coolant_flow_lpm": 0.0, "axis_feed": 0.0}],
        only_states=("CUTTING",))

    assert features.sample_count == 1
    assert features.extras["conditioned_samples"] == 0
    assert features.spindle_load_mean == 0.0
