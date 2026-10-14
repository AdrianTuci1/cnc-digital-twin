"""Teste pentru contractul de date și pentru caracteristicile comune.

Cel mai important test din fișier este ``test_glue_table_matches_contract``: compară lista de
coloane din cod cu tabelul definit în Terraform. Dacă cineva adaugă o coloană într-un loc și
uită de celălalt, Firehose fie respinge înregistrările, fie scrie coloane fără schemă.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cncdt_ml import offline, schema
from cncdt_ml.features import AnomalyThresholds, compute_window_features, enriched_record
from cncdt_ml.labels import health_label, rul_proxy, severity_from_wear
from cncdt_ml.windows import group_windows, window_bounds, window_key

REPO_ROOT = Path(__file__).resolve().parents[2]
IOT_MODULE = REPO_ROOT / "infra" / "terraform" / "modules" / "iot-ingestion" / "main.tf"


def _terraform_columns() -> list[str]:
    """Coloanele tabelului din catalogul de schemă, citite din Terraform."""
    text = IOT_MODULE.read_text(encoding="utf-8")
    start = text.index('resource "aws_glue_catalog_table" "telemetry"')
    block = text[start:]
    storage = block.index("storage_descriptor")
    return re.findall(r'columns \{\s*name = "([^"]+)"', block[storage:])


def _terraform_partition_keys() -> list[str]:
    text = IOT_MODULE.read_text(encoding="utf-8")
    start = text.index('resource "aws_glue_catalog_table" "telemetry"')
    block = text[start:]
    storage = block.index("storage_descriptor")
    return re.findall(r'partition_keys \{\s*name = "([^"]+)"', block[:storage])


def test_glue_table_matches_contract() -> None:
    assert _terraform_columns() == list(schema.ENRICHED_COLUMNS)


def test_partition_keys_match_contract() -> None:
    assert _terraform_partition_keys() == list(schema.PARTITION_KEYS)


def test_schema_version_is_present_and_versioned() -> None:
    assert schema.SCHEMA_VERSION.count(".") == 2
    record = schema.conform({"schema_version": schema.SCHEMA_VERSION})
    assert record["schema_version"] == schema.SCHEMA_VERSION


def test_conform_drops_unknown_fields_and_keeps_order() -> None:
    raw = {
        "device_id": "cnc-cell-01",
        "spindle_load_max": 9.9,
        "vibration_crest": 7.7,
        "temp_c_max": 101.0,
        "joint_effort_mean": 3.3,
        "axis_feed_mean": 1.1,
        "ceva_necunoscut": True,
        "ts_ms": 1_700_000_000_000,
    }
    record = schema.conform(raw)
    assert tuple(record) == schema.ENRICHED_COLUMNS
    assert "spindle_load_max" not in record
    assert schema.dropped_fields(raw) == [
        "axis_feed_mean",
        "joint_effort_mean",
        "spindle_load_max",
        "temp_c_max",
        "vibration_crest",
    ]


def test_conform_strict_reports_extra_fields() -> None:
    with pytest.raises(ValueError, match="câmpuri în afara contractului"):
        schema.conform({"device_id": "x", "extra": 1}, strict=True)


def test_partition_for_is_utc_and_zero_padded() -> None:
    moment = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    partition = schema.partition_for(int(moment.timestamp() * 1000))
    assert partition == {"dt_year": "2026", "dt_month": "03", "dt_day": "04", "dt_hour": "05"}
    assert schema.partition_keys_are_strings(partition)


def test_missing_columns_reports_absent_names() -> None:
    assert schema.missing_columns({"device_id": "x"})[:2] == ["schema_version", "cell_id"]


# ---------------------------------------------------------------- ferestre
def test_window_key_and_bounds_align_to_epoch() -> None:
    assert window_key(0, 60) == 0
    assert window_key(59_999, 60) == 0
    assert window_key(60_000, 60) == 1
    assert window_bounds(125_000, 60) == (120_000, 180_000)


def test_window_key_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError):
        window_key(1, 0)


def test_group_windows_ignores_records_without_timestamp() -> None:
    records = [
        {"ts_ms": 0},
        {"ts_ms": 61_000},
        {"ts_ms": "invalid"},
        {"fără": "timestamp"},
    ]
    groups = group_windows(records, 60)
    assert sorted(groups) == [0, 1]
    assert len(groups[0]) == 1 and len(groups[1]) == 1


# ---------------------------------------------------------------- caracteristici
def test_window_features_on_known_values() -> None:
    samples = [
        {"spindle_load": 0.5, "vibration": 1.0, "temp_c": 60.0, "coolant_flow_lpm": 5.0},
        {"spindle_load": 1.0, "vibration": 2.0, "temp_c": 64.0, "coolant_flow_lpm": 3.0},
    ]
    features = compute_window_features(samples)
    assert features.sample_count == 2
    assert features.spindle_load_mean == pytest.approx(0.75)
    assert features.spindle_load_p95 == pytest.approx(0.975)
    assert features.vibration_rms == pytest.approx((2.5) ** 0.5)
    assert features.temp_c_mean == pytest.approx(62.0)
    assert features.coolant_flow_lpm == pytest.approx(4.0)


def test_enriched_record_uses_contract_columns() -> None:
    samples = [{"spindle_load": 0.4, "ts_ms": 1_000, "device_id": "cnc-cell-01", "cell_id": "cell-a"}]
    record = enriched_record(compute_window_features(samples), raw_samples=samples, window_s=60)
    assert tuple(record) == schema.ENRICHED_COLUMNS
    assert record["device_id"] == "cnc-cell-01"
    assert record["window_s"] == 60


def test_thresholds_flag_extreme_window() -> None:
    samples = [{"vibration": 9.0, "temp_c": 20.0, "coolant_flow_lpm": 5.0} for _ in range(10)]
    features = compute_window_features(samples)
    thresholds = AnomalyThresholds()
    assert thresholds.is_anomalous(features)
    assert thresholds.violations(features) == ["vibration_rms"]


# ---------------------------------------------------------------- caracteristici offline
def _series() -> list[dict]:
    rows = []
    for index in range(6):
        rows.append(
            {
                "device_id": "cnc-cell-01",
                "ts_ms": 1_700_000_000_000 + index * 60_000,
                "spindle_load_mean": 0.5 + index * 0.1,
                "spindle_load_std": 0.05,
                "vibration_rms": 1.0 + index * 0.2,
                "temp_c_mean": 60.0 + index,
                "coolant_flow_lpm": 5.0,
                "tool_wear_index": index * 0.1,
                "joint_friction_index": 0.1,
                "vibration_peak": 3.0,
            }
        )
    return rows


def test_derived_ratios_are_finite_and_named() -> None:
    ratios = offline.derived_ratios(_series()[0])
    assert set(ratios) == {
        "load_per_friction",
        "vibration_per_load",
        "crest_ratio",
        "thermal_margin_c",
        "coolant_margin_lpm",
        "wear_x_load",
        "friction_x_temp",
    }
    assert all(isinstance(value, float) for value in ratios.values())


def test_rolling_features_on_three_point_window() -> None:
    rows = offline.add_rolling_features(_series(), offline.OfflineFeatureSpec(rolling_windows=(3,), lag_steps=()))
    third = rows[2]
    assert third["spindle_load_mean_roll3_mean"] == pytest.approx(0.6)  # (0.5 + 0.6 + 0.7) / 3
    assert third["spindle_load_mean_roll3_delta"] == pytest.approx(0.1)
    assert third["vibration_rms_roll3_std"] == pytest.approx(0.2)
    assert rows[0]["spindle_load_mean_roll3_mean"] == pytest.approx(0.5)


def test_lag_features_repeat_first_value() -> None:
    rows = offline.add_lag_features(
        _series(), offline.OfflineFeatureSpec(rolling_windows=(), lag_steps=(1, 2))
    )
    assert rows[0]["spindle_load_mean_lag1"] == pytest.approx(0.5)
    assert rows[1]["spindle_load_mean_lag1"] == pytest.approx(0.5)
    assert rows[2]["spindle_load_mean_lag2"] == pytest.approx(0.5)
    assert rows[3]["spindle_load_mean_lag2"] == pytest.approx(0.6)


def test_build_offline_features_is_pure_and_complete() -> None:
    source = _series()
    snapshot = [dict(row) for row in source]
    result = offline.build_offline_features(source)
    assert source == snapshot, "intrarea nu trebuie modificată"
    assert len(result) == len(source)
    assert "health_label" in result[0]
    for column in offline.feature_columns():
        assert column in result[0], f"lipsește {column}"


def test_feature_columns_have_no_duplicates() -> None:
    columns = offline.feature_columns()
    assert len(columns) == len(set(columns))


def test_offline_drops_nothing_from_the_contract() -> None:
    rows = [
        {column: 0 for column in schema.ENRICHED_COLUMNS} | {"device_id": "cnc-cell-01", "ts_ms": 1}
    ]
    assert offline.dropped_contract_columns(rows) == []


# ---------------------------------------------------------------- etichete
def test_wear_severity_thresholds() -> None:
    assert severity_from_wear(0.1) == "ok"
    assert severity_from_wear(0.5) == "watch"
    assert severity_from_wear(0.9) == "replace"


def test_health_label_priority() -> None:
    assert health_label({"is_synthetic_defect": True, "tool_wear_index": 0.9}) == "defect"
    assert health_label({"tool_wear_index": 0.8}) == "tool_wear"
    assert health_label({"joint_friction_index": 0.75}) == "joint_friction"
    assert health_label({"temp_c_mean": 80.0}) == "thermal"
    assert health_label({"coolant_flow_lpm": 1.0}) == "coolant"
    assert health_label({}) == "healthy"


def test_rul_proxy_decreases_with_wear() -> None:
    assert rul_proxy({"tool_wear_index": 0.0}, 0.05) == pytest.approx(20.0)
    assert rul_proxy({"tool_wear_index": 0.5}, 0.05) == pytest.approx(10.0)
    assert rul_proxy({"tool_wear_index": 1.0}, 0.05) == 0.0
    assert rul_proxy({"tool_wear_index": 0.5}, 0.0) == 0.0


# ---------------------------------------------------------------- scheme JSON
SCHEMAS_DIR = REPO_ROOT / "telemetry" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def test_schemas_are_valid_json_with_identity() -> None:
    files = sorted(SCHEMAS_DIR.glob("*.json"))
    assert files, "trebuie să existe cel puțin o schemă"
    for path in files:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"].endswith("2020-12/schema")
        assert schema["$id"].endswith(path.name)


def test_enriched_schema_matches_the_contract_exactly() -> None:
    enriched = _schema("cnc.telemetry.enriched.v1.json")
    assert tuple(enriched["properties"]) == schema.ENRICHED_COLUMNS
    assert enriched["required"] == list(schema.ENRICHED_COLUMNS)
    assert enriched["additionalProperties"] is False


def test_raw_schema_required_fields_are_published_by_the_simulation() -> None:
    schema = _schema("cnc.telemetry.v1.json")
    from cncdt.cnc_cell.degradation.model import DegradationState
    from cncdt.cnc_cell.telemetry.sensors import SceneSensors

    sample = SceneSensors(degradation=DegradationState()).read(0.1)
    sample.update({"ts_ms": 1, "schema_version": "1.0.0", "topic": "t", "cycle_id": "cyc-000001", "part_id": "part-000001"})
    missing = [name for name in schema["required"] if name not in sample]
    assert missing == []


def test_control_schema_lists_the_commands_the_controller_handles() -> None:
    schema = _schema("cnc.control.v1.json")
    commands = set(schema["oneOf"][0]["properties"]["command"]["enum"])
    source = (
        REPO_ROOT / "simulation/extensions/cncdt.cnc_cell/cncdt/cnc_cell/sim_control.py"
    ).read_text(encoding="utf-8")
    for command in commands:
        assert f'"{command}"' in source, f"comanda {command} nu apare în controler"
    assert schema["oneOf"][1]["properties"]["verdict"]["enum"] == ["PASS", "REJECT", "FAIL", "DEFECT"]
