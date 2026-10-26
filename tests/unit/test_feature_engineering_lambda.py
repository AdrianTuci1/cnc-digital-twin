"""Teste pentru transformarea Firehose a Lambda-ului de feature engineering.

Testele construiesc loturi JSON exact în formatul primit de la Firehose și verifică
răspunsul, inclusiv regula esențială: fiecare ``recordId`` primit primește un rezultat, altfel
Firehose retrimite tot lotul.
"""

from __future__ import annotations

import base64
import json
import logging

import pytest

import core
import handler
from cncdt_ml.schema import ENRICHED_COLUMNS, PARTITION_KEYS


def encode(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def raw_sample(ts_ms: int, **overrides) -> dict:
    sample = {
        "schema_version": "1.0.0",
        "device_id": "cnc-cell-01",
        "cell_id": "cell-a",
        "topic": "cncdt/dev/cnc/cell-a/telemetry",
        "ts_ms": ts_ms,
        "machine_state": "CUTTING",
        "cycle_id": "cyc-000001",
        "part_id": "part-000001",
        "spindle_load": 0.55,
        "vibration": 1.4,
        "temp_c": 62.0,
        "coolant_flow_lpm": 5.0,
        "joint_effort": 2.0,
        "axis_feed": 0.8,
        "tool_wear_index": 0.1,
        "joint_friction_index": 0.1,
        "surface_quality": 0.9,
        "is_synthetic_defect": False,
    }
    sample.update(overrides)
    return sample


def firehose_event(samples: list[dict]) -> dict:
    return {
        "invocationId": "inv-1",
        "deliveryStreamArn": "arn:aws:firehose:us-east-1:123456789012:deliverystream/cncdt-dev-iot-ingestion",
        "region": "us-east-1",
        "records": [
            {
                "recordId": f"rec-{index}",
                "approximateArrivalTimestamp": 1700000000000,
                "data": encode(sample),
            }
            for index, sample in enumerate(samples)
        ],
    }


# ---------------------------------------------------------------- decodare
def test_decode_data_roundtrip() -> None:
    assert core.decode_data(encode({"a": 1})) == {"a": 1}


def test_decode_data_rejects_invalid_base64() -> None:
    with pytest.raises(core.RecordError, match="base64"):
        core.decode_data("nu-e-base64!!!")


def test_decode_data_rejects_non_object_json() -> None:
    with pytest.raises(core.RecordError, match="obiect JSON"):
        core.decode_data(encode([1, 2, 3]))


def test_decode_batch_separates_failures() -> None:
    event = firehose_event([raw_sample(1_700_000_000_000)])
    event["records"].append({"recordId": "rec-rau", "data": encode([1])})
    decoded, failures = core.decode_batch(event)
    assert [item.record_id for item in decoded] == ["rec-0"]
    assert failures == [{"recordId": "rec-rau", "error": "înregistrarea nu este un obiect JSON"}]


# ---------------------------------------------------------------- agregare
def test_aggregate_produces_one_record_per_window() -> None:
    samples = [raw_sample(1_700_000_000_000 + index * 100) for index in range(120)]
    decoded, _ = core.decode_batch(firehose_event(samples))
    results, untimed = core.aggregate(decoded, window_s=60)

    assert untimed == []
    assert len(results) == 1
    result = results[0]
    assert result.samples == 120
    assert result.record_id == "rec-119"
    assert len(result.dropped_ids) == 119
    assert tuple(result.record) == ENRICHED_COLUMNS


def test_aggregate_splits_windows() -> None:
    samples = [raw_sample(1_700_000_000_000 + offset) for offset in (0, 100, 60_000, 60_100)]
    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60)
    assert len(results) == 2
    assert [result.samples for result in results] == [2, 2]


def test_aggregate_keeps_devices_apart() -> None:
    samples = [
        raw_sample(1_700_000_000_000, device_id="cnc-cell-01"),
        raw_sample(1_700_000_000_000, device_id="cnc-cell-02"),
    ]
    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60)
    assert len(results) == 2
    assert {result.record["device_id"] for result in results} == {"cnc-cell-01", "cnc-cell-02"}


def test_aggregate_marks_records_without_timestamp() -> None:
    samples = [raw_sample(1_700_000_000_000), raw_sample(1_700_000_000_000)]
    samples[1]["ts_ms"] = "lipsă"
    decoded, _ = core.decode_batch(firehose_event(samples))
    results, untimed = core.aggregate(decoded, window_s=60)
    assert untimed == ["rec-1"]
    assert results[0].samples == 1


def test_aggregate_uses_threshold_fallback_without_model() -> None:
    samples = [raw_sample(1_700_000_000_000, vibration=9.0) for _ in range(10)]
    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60)
    record = results[0].record
    assert record["model_version"] == "thresholds"
    assert record["anomaly_detected"] is True
    assert record["anomaly_score"] > 0.0


def test_aggregate_applies_model_predictions() -> None:
    samples = [raw_sample(1_700_000_000_000 + index) for index in range(30)]

    def provider(records):
        assert len(records) == 1
        # Coloană din MODEL_FEATURE_ORDER care nu face parte din contractul Firehose.
        assert records[0]["axis_feed_mean"] == 0.8
        return [{"anomaly_score": 0.91, "anomaly_detected": True, "model_version": "pm-2026-01"}]

    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60, prediction_provider=provider)
    record = results[0].record
    assert record["anomaly_score"] == pytest.approx(0.91)
    assert record["model_version"] == "pm-2026-01"


def test_aggregate_skips_model_when_window_has_no_cutting() -> None:
    """O fereastră de repaus nu are semnal de uzură, deci nu consumă un apel de endpoint."""
    samples = [raw_sample(1_700_000_000_000 + index, machine_state="IDLE")
               for index in range(40)]
    calls: list[list[dict]] = []

    def provider(records):
        calls.append(records)
        return [{"anomaly_score": 0.99, "anomaly_detected": True}]

    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60, prediction_provider=provider)
    assert calls == []
    assert results[0].record["model_version"] == "thresholds"
    assert results[0].record["anomaly_detected"] is False


def test_aggregate_survives_provider_failure() -> None:
    samples = [raw_sample(1_700_000_000_000 + index) for index in range(30)]

    def provider(_records):
        raise RuntimeError("endpoint indisponibil")

    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60, prediction_provider=provider)
    assert results[0].record["model_version"] == "thresholds"


def test_score_threshold_marks_anomaly_without_physical_violation() -> None:
    samples = [raw_sample(1_700_000_000_000) for _ in range(30)]
    decoded, _ = core.decode_batch(firehose_event(samples))

    def provider(_records):
        return [{"anomaly_score": 0.8, "anomaly_detected": False}]

    results, _ = core.aggregate(decoded, window_s=60, prediction_provider=provider, score_threshold=0.65)
    assert results[0].record["anomaly_detected"] is True


# ---------------------------------------------------------------- vector și endpoint
def test_feature_vector_order_is_stable() -> None:
    record = {name: float(index) for index, name in enumerate(core.MODEL_FEATURE_ORDER)}
    assert core.feature_vector(record) == [float(index) for index in range(len(core.MODEL_FEATURE_ORDER))]


def test_model_feature_order_is_the_training_contract() -> None:
    """Ordinea pozițională din containerul RCF; o schimbare cere reantrenare și bump de versiune."""
    assert core.MODEL_FEATURE_ORDER == (
        "spindle_load_mean",
        "spindle_load_p95",
        "spindle_load_std",
        "vibration_rms",
        "vibration_peak",
        "vibration_std",
        "coolant_flow_lpm",
        "temp_c_mean",
        "axis_feed_mean",
    )


def test_feature_vector_zero_fills_missing() -> None:
    assert core.feature_vector({}) == [0.0] * len(core.MODEL_FEATURE_ORDER)


def test_endpoint_body_and_response_roundtrip() -> None:
    body = core.endpoint_body([{name: 1.0 for name in core.MODEL_FEATURE_ORDER}])
    instance = json.loads(body)["instances"][0]
    assert instance["data"]["features"]["values"] == [1.0] * len(core.MODEL_FEATURE_ORDER)

    scores = core.parse_endpoint_response(json.dumps({"predictions": [0.1, 0.9]}))
    assert [item["anomaly_score"] for item in scores] == [0.1, 0.9]
    assert all(isinstance(item["anomaly_detected"], bool) for item in scores)

    decisions = core.parse_endpoint_response(
        json.dumps({"predictions": [{"anomaly_score": 0.7, "anomaly_detected": True}]})
    )
    assert decisions == [{"anomaly_score": 0.7, "anomaly_detected": True}]


def test_rcf_scores_are_read_as_anomaly_scores() -> None:
    scores = core.parse_endpoint_response(
        json.dumps({"scores": [{"score": 1.87}, {"score": 0.42}]})
    )
    assert [item["anomaly_score"] for item in scores] == [1.87, 0.42]


def test_parse_endpoint_response_rejects_unknown_shape() -> None:
    with pytest.raises(core.RecordError):
        core.parse_endpoint_response(json.dumps({"altceva": []}))


# ---------------------------------------------------------------- răspuns Firehose
def test_assemble_response_covers_every_record_id() -> None:
    samples = [raw_sample(1_700_000_000_000 + index * 100) for index in range(5)]
    samples.append({"ts_ms": "rău"})
    event = firehose_event(samples)
    decoded, failures = core.decode_batch(event)
    results, untimed = core.aggregate(decoded, window_s=60)

    response = core.assemble_response(results, failures, untimed_ids=untimed)
    produced = {item["recordId"] for item in response["records"]}
    expected = {record["recordId"] for record in event["records"]}
    assert produced == expected

    by_id = {item["recordId"]: item for item in response["records"]}
    ok = by_id[results[0].record_id]
    assert ok["result"] == "Ok"
    assert set(ok["metadata"]["partitionKeys"]) == set(PARTITION_KEYS)
    assert tuple(json.loads(base64.b64decode(ok["data"]))) == ENRICHED_COLUMNS
    assert by_id["rec-5"]["result"] == "ProcessingFailed"
    assert all(
        by_id[dropped]["result"] == "Dropped" for dropped in results[0].dropped_ids
    )


def test_assemble_response_marks_malformed_records() -> None:
    _decoded, failures = core.decode_batch({"records": [{"recordId": "x", "data": encode([1])}]})
    response = core.assemble_response([], failures)
    assert response["records"][0]["result"] == "ProcessingFailed"
    assert "obiect JSON" in json.loads(base64.b64decode(response["records"][0]["data"]))["error"]


# ---------------------------------------------------------------- metrice și replay
def test_metric_data_contains_score_and_counters() -> None:
    samples = [raw_sample(1_700_000_000_000 + index) for index in range(3)]
    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60)
    metrics = core.metric_data(results, "CNCDT/Telemetry")
    names = {metric["MetricName"] for metric in metrics}
    assert core.METRIC_ANOMALY_SCORE in names
    assert core.METRIC_RECORDS in names
    assert core.METRIC_WINDOWS in names
    assert all(isinstance(metric["Value"], float) for metric in metrics)


def test_feature_metrics_are_opt_in() -> None:
    """Caracteristicile brute nu se publică implicit: nimeni nu le citește, dar costă lunar."""
    samples = [raw_sample(1_700_000_000_000 + index) for index in range(3)]
    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60)

    default_names = {metric["MetricName"] for metric in core.metric_data(results, "CNCDT/Telemetry")}
    assert not set(core.MODEL_FEATURE_ORDER) & default_names

    opt_in_names = {
        metric["MetricName"]
        for metric in core.metric_data(results, "CNCDT/Telemetry", include_features=True)
    }
    assert set(core.MODEL_FEATURE_ORDER) <= opt_in_names


def test_endpoint_failures_are_published_when_the_call_fails(monkeypatch) -> None:
    """Dashboard-ul de infrastructură citește `EndpointFailures`, deci metrica are producător."""
    published: list[list[dict]] = []
    monkeypatch.setattr(handler, "SAGEMAKER_ENDPOINT_NAME", "ep-de-test")
    monkeypatch.setattr(handler, "_publish_metrics", published.append)

    def broken_client(service: str, *args, **kwargs):
        raise RuntimeError("endpoint indisponibil")

    monkeypatch.setattr(handler, "client", broken_client)

    calm = firehose_event([raw_sample(1_700_000_000_000 + index) for index in range(30)])
    handler.lambda_handler(calm, context=None)

    names = [metric["MetricName"] for batch in published for metric in batch]
    assert core.METRIC_ENDPOINT_FAILURES in names
    assert names.count(core.METRIC_ENDPOINT_FAILURES) == 1

    published.clear()
    monkeypatch.setattr(handler, "SAGEMAKER_ENDPOINT_NAME", "")
    handler.lambda_handler(calm, context=None)
    assert core.METRIC_ENDPOINT_FAILURES not in [
        metric["MetricName"] for batch in published for metric in batch
    ]


def test_batch_metrics_respects_batch_size() -> None:
    metrics = [{"MetricName": "x", "Value": 1.0} for _ in range(core.METRIC_BATCH_SIZE * 2 + 1)]
    batches = core.batch_metrics(metrics)
    assert [len(batch) for batch in batches] == [core.METRIC_BATCH_SIZE, core.METRIC_BATCH_SIZE, 1]


def test_degradation_metrics_carry_the_element_dimension() -> None:
    samples = [raw_sample(1_700_000_000_000 + index) for index in range(3)]
    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60)

    metrics = core.metric_data(results, "CNCDT/Telemetry", rates={"cnc": 0.0015, "robot": 0.0008})
    by_name: dict[str, list[dict]] = {}
    for metric in metrics:
        by_name.setdefault(metric["MetricName"], []).append(metric)

    elements = {metric["Dimensions"][-1]["Value"] for metric in by_name[core.METRIC_DEGRADATION_INDEX]}
    assert elements == {"cnc", "robot"}
    assert len(by_name[core.METRIC_RUL_MINUTES]) == len(core.DEGRADATION_ELEMENTS)
    # RUL este o limită de jos, în minute de operare continuă: pozitiv cât timp există prag.
    assert all(metric["Value"] > 0 for metric in by_name[core.METRIC_RUL_MINUTES])


def test_rul_minutes_needs_a_positive_rate() -> None:
    assert core.rul_minutes(0.0, 0.0) is None
    assert core.rul_minutes(0.0, 0.0015) == pytest.approx(1.0 / 0.0015 / 60.0)
    assert core.rul_minutes(1.0, 0.0015) == 0.0
    # Un indice peste prag nu produce un RUL negativ.
    assert core.rul_minutes(1.4, 0.0015) == 0.0


def test_degradation_metrics_are_skipped_without_rates() -> None:
    samples = [raw_sample(1_700_000_000_000)]
    decoded, _ = core.decode_batch(firehose_event(samples))
    results, _ = core.aggregate(decoded, window_s=60)

    metrics = core.metric_data(results, "CNCDT/Telemetry")
    names = {metric["MetricName"] for metric in metrics}
    assert core.METRIC_DEGRADATION_INDEX not in names
    assert core.METRIC_RUL_MINUTES not in names


def test_replay_payload_is_serializable() -> None:
    decoded, _ = core.decode_batch(firehose_event([raw_sample(1_700_000_000_000)]))
    results, _ = core.aggregate(decoded, window_s=60)
    payload = core.replay_payload(results[0], reason="threshold_fallback", region="us-east-1")
    assert payload["reason"] == "threshold_fallback"
    assert json.dumps(payload)  # trebuie să fie serializabil ca atare


def test_contract_columns_match_schema() -> None:
    assert core.contract_columns() == ENRICHED_COLUMNS


# ---------------------------------------------------------------- handler
def test_handler_processes_a_batch_without_aws() -> None:
    """Fără endpoint, fără bucket și fără boto3, livrarea către Firehose trebuie să reușească."""
    event = firehose_event([raw_sample(1_700_000_000_000 + index * 100) for index in range(30)])
    response = handler.lambda_handler(event, context=None)
    assert len(response["records"]) == 30
    ok_records = [item for item in response["records"] if item["result"] == "Ok"]
    assert len(ok_records) == 1
    body = json.loads(base64.b64decode(ok_records[0]["data"]))
    assert tuple(body) == ENRICHED_COLUMNS
    assert body["sample_count"] == 30


def test_handler_logs_anomaly_events_for_the_metric_filter(caplog, monkeypatch) -> None:
    """Filtrul de metrice `AnomalyDetectedWindows` citește linia de jurnal, deci ea există.

    Fără acest eveniment, alarma `process_anomaly` și panoul de anomalii ar rămâne veșnic
    goale, cu `treat_missing_data = notBreaching`.
    """
    anomalous = firehose_event([raw_sample(1_700_000_000_000, vibration=9.0) for _ in range(10)])
    with caplog.at_level(logging.INFO):
        handler.lambda_handler(anomalous, context=None)

    detected = [
        fields
        for fields in (getattr(record, "fields", {}) for record in caplog.records)
        if fields.get("event") == "anomaly_detected"
    ]
    assert detected
    assert all(fields["anomaly_detected"] is True for fields in detected)
    assert all(fields["device_id"] and fields["cell_id"] for fields in detected)

    # Pragul urcat ține lotul liniștit departe de orice marcare, ca să verificăm că evenimentul
    # nu se scrie pentru o fereastră sănătoasă: filtrul de metrice ar număra greșit.
    monkeypatch.setattr(handler, "ANOMALY_SCORE_THRESHOLD", 0.99)
    caplog.clear()
    calm = firehose_event([raw_sample(1_700_000_000_000 + index) for index in range(30)])
    with caplog.at_level(logging.INFO):
        handler.lambda_handler(calm, context=None)
    assert not [
        fields
        for fields in (getattr(record, "fields", {}) for record in caplog.records)
        if fields.get("event") == "anomaly_detected"
    ]


def test_handler_returns_failure_for_empty_batch() -> None:
    response = handler.lambda_handler({"records": []}, context=None)
    assert response == {"records": []}


def test_handler_reports_malformed_records() -> None:
    event = {"records": [{"recordId": "bad", "data": "%%%"}]}
    response = handler.lambda_handler(event, context=None)
    assert response["records"][0]["result"] == "ProcessingFailed"
