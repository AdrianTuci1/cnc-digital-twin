"""Teste pentru Lambda-ul de inspecție vizuală.

Acoperă logica pură din ``qa_vision_core``: citirea notificării S3, validarea cadrului QA,
construcția verdictului și contractul de control.

Modulele se încarcă prin cale, cu nume unice: directorul Lambda-ului nu se pune pe
``sys.path``, pentru că are și el un ``handler.py`` care ar umbri modulul de feature
engineering. Modulul pur se înregistrează în ``sys.modules``, ca ``import qa_vision_core``
din handler să îl găsească.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROL_SCHEMA = ROOT / "telemetry" / "schemas" / "cnc.control.v1.json"


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load_module("qa_vision_core", "lambdas/qa_vision_inference/qa_vision_core.py")


def snapshot(**overrides) -> dict:
    payload = {
        "schema_version": "1.1.0",
        "captured_at": "2026-01-01T00:00:00Z",
        "cycle_id": "cyc-000001",
        "part_id": "part-000001",
        "camera_role": "sorting",
        "image_captured": True,
        "image_key": "raw/qa-snapshots/cyc-000001-part-000001.png",
        "camera": "/World/QA/DiverterCamera",
        "resolution": [640, 480],
        "tool_wear_index": 0.1,
        "joint_friction_index": 0.1,
        "surface_quality": 0.95,
    }
    payload.update(overrides)
    return payload


def s3_event(keys: list[str], bucket: str = "cncdt-dev-data-lake") -> dict:
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
            }
            for key in keys
        ]
    }


# ---------------------------------------------------------------- notificarea S3
def test_decode_s3_event_extracts_and_unquotes_keys():
    event = s3_event(["raw/qa-snapshots/cyc%2B1-part+1.json"])
    refs = core.decode_s3_event(event)
    assert len(refs) == 1
    assert refs[0].bucket == "cncdt-dev-data-lake"
    assert refs[0].key == "raw/qa-snapshots/cyc+1-part 1.json"


def test_decode_s3_event_ignores_incomplete_records():
    event = {"Records": [{"s3": {"bucket": {"name": "b"}}}, {"s3": {}}, {}]}
    assert core.decode_s3_event(event) == []


# ---------------------------------------------------------------- validarea cadrului
def test_parse_snapshot_requires_cycle_and_part():
    with pytest.raises(core.SnapshotError):
        core.parse_snapshot({"cycle_id": "", "part_id": "part-1"})
    with pytest.raises(core.SnapshotError):
        core.parse_snapshot("nu este obiect")


def test_parse_snapshot_defaults_image_captured_to_false():
    parsed = core.parse_snapshot(snapshot(image_captured=None))
    assert parsed["image_captured"] is False


def test_snapshot_features_follows_model_order():
    features = core.snapshot_features(
        snapshot(tool_wear_index=0.2, joint_friction_index=0.3, surface_quality=0.8)
    )
    assert features == [0.2, 0.3, 0.8, 1.0]
    assert core.MODEL_FEATURE_ORDER == (
        "tool_wear_index",
        "joint_friction_index",
        "surface_quality",
        "image_available",
    )


def test_snapshot_features_defaults_missing_values_to_zero():
    assert core.snapshot_features({"image_captured": False}) == [0.0, 0.0, 0.0, 0.0]


# ---------------------------------------------------------------- răspunsul modelului
def test_endpoint_body_carries_features_and_the_photo():
    png = b"\x89PNG\r\n\x1a\n-cadru"
    body = json.loads(
        core.endpoint_body([snapshot(tool_wear_index=0.4)], {"part-000001": png})
    )

    assert body["contract"] == core.FEATURE_CONTRACT
    assert body["feature_names"] == list(core.MODEL_FEATURE_ORDER)
    instance = body["instances"][0]
    assert instance["features"][0] == 0.4
    assert instance["camera_role"] == "sorting"
    assert instance["image"]["format"] == "png"
    assert base64.b64decode(instance["image"]["data"]) == png


def test_endpoint_body_sends_features_alone_without_a_photo():
    body = json.loads(core.endpoint_body([snapshot()]))
    instance = body["instances"][0]
    assert "image" not in instance
    # Ordinea caracteristicilor rămâne cea din contract, chiar fără imagine.
    assert len(instance["features"]) == len(core.MODEL_FEATURE_ORDER)


def test_endpoint_body_skips_a_photo_that_belongs_to_another_part():
    body = json.loads(core.endpoint_body([snapshot()], {"part-999999": b"alt cadru"}))
    assert "image" not in body["instances"][0]


def test_parse_endpoint_response_accepts_dict_and_list():
    assert core.parse_endpoint_response(b'{"predictions": [0.8]}') == [0.8]
    assert core.parse_endpoint_response(b'{"outputs": [{"defect_probability": 0.2}]}') == [0.2]
    assert core.parse_endpoint_response(b"[[0.3, 0.7]]") == [0.7]


def test_parse_endpoint_response_clamps_and_validates():
    assert core.parse_endpoint_response(b'{"predictions": [1.7]}') == [1.0]
    with pytest.raises(core.SnapshotError):
        core.parse_endpoint_response(b'{"predictions": ["mult"]}')
    with pytest.raises(core.SnapshotError):
        core.parse_endpoint_response(b"nu e json")


# ---------------------------------------------------------------- scor de rezervă
def test_heuristic_probability_reacts_to_degradation():
    clean = core.heuristic_probability(snapshot())
    worn = core.heuristic_probability(snapshot(tool_wear_index=0.9, surface_quality=0.4))
    assert worn > clean


def test_heuristic_probability_forces_defect_when_flagged():
    assert core.heuristic_probability(snapshot(is_synthetic_defect=True)) >= 0.9


def test_heuristic_probability_stays_cautious_without_image():
    score = core.heuristic_probability(snapshot(image_captured=False, tool_wear_index=0.95))
    assert score <= 0.49


# ---------------------------------------------------------------- verdict
def test_verdict_message_rejects_above_threshold():
    message = core.verdict_message(snapshot(), probability=0.7, threshold=0.5, model_version="v1")
    assert message["verdict"] == "REJECT"
    assert message["part_id"] == "part-000001"
    assert message["cycle_id"] == "cyc-000001"
    assert message["confidence"] == 0.7
    assert message["model_version"] == "v1"


def test_verdict_message_passes_below_threshold():
    message = core.verdict_message(snapshot(), probability=0.2, threshold=0.5, model_version="v1")
    assert message["verdict"] == "PASS"
    assert "reason" not in message


def test_verdict_matches_control_contract():
    schema = json.loads(CONTROL_SCHEMA.read_text(encoding="utf-8"))
    verdict_enum = {
        value
        for variant in schema["oneOf"]
        for value in variant.get("properties", {}).get("verdict", {}).get("enum", [])
    }
    assert {"PASS", "REJECT"} <= verdict_enum

    required = {
        key
        for variant in schema["oneOf"]
        if "verdict" in variant.get("properties", {})
        for key in variant["required"]
    }
    message = core.verdict_message(snapshot(), 0.9, 0.5, "v1")
    assert required <= set(message)


def test_verdict_key_is_partitioned_by_day():
    key = core.verdict_key("curated/qa-verdicts", snapshot(), "2026-01-02T10:00:00Z")
    assert key == "curated/qa-verdicts/dt=2026-01-02/cyc-000001-part-000001.json"


def test_metric_data_counts_processed_and_rejects():
    metrics = core.metric_data(processed=3, rejects=1, endpoint_failures=0)
    by_name = {metric["MetricName"]: metric["Value"] for metric in metrics}
    assert by_name[core.METRIC_PROCESSED] == 3.0
    assert by_name[core.METRIC_REJECTS] == 1.0
    assert by_name[core.METRIC_PARTS_GOOD] == 2.0
    assert by_name[core.METRIC_PARTS_SCRAP] == 1.0
    assert all(metric["Unit"] == "Count" for metric in metrics)
    assert core.METRIC_ENDPOINT_FAILURES not in by_name
    assert core.METRIC_IMAGES_SENT not in by_name


def test_metric_data_includes_failures_when_present():
    metrics = core.metric_data(1, 0, endpoint_failures=2)
    failures = [m for m in metrics if m["MetricName"] == core.METRIC_ENDPOINT_FAILURES]
    assert failures and failures[0]["Value"] == 2.0


def test_metric_data_counts_the_photos_sent():
    metrics = core.metric_data(1, 0, endpoint_failures=0, images_sent=1)
    images = [m for m in metrics if m["MetricName"] == core.METRIC_IMAGES_SENT]
    assert images and images[0]["Value"] == 1.0

    without = core.metric_data(1, 0, endpoint_failures=0)
    assert core.METRIC_IMAGES_SENT not in {m["MetricName"] for m in without}


# ---------------------------------------------------------------- fotografia din S3
class FakeS3:
    """Client S3 minimal, ca testul să vadă exact ce chei se citesc."""

    def __init__(self, objects: dict[str, bytes], fail: set[str] | None = None) -> None:
        self.objects = objects
        self.fail = fail or set()
        self.read: list[str] = []

    def get_object(self, Bucket: str, Key: str):  # numele vin din semnătura boto3
        self.read.append(Key)
        if Key in self.fail:
            raise RuntimeError("cheie indisponibilă")
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": BytesIO(self.objects[Key])}


def run_handler(objects: dict[str, bytes], fail: set[str] | None = None, name: str = ""):
    """Rulează handler-ul cu un S3 fals și întoarce (rezultat, imagini trimise, S3 fals).

    Se înlocuiește doar `_probabilities`, ca testul să nu cheme niciun endpoint: imaginile
    ajung oricum la ea, deci se vede exact ce s-ar fi trimis către model.
    """
    handler = load_module(
        f"qa_vision_handler_s3_{name or len(objects)}",
        "lambdas/qa_vision_inference/handler.py",
    )
    fake = FakeS3(objects, fail)
    handler.client = lambda service, **kwargs: fake
    handler.SEND_IMAGE = True
    handler._write_verdict = lambda *args, **kwargs: "cheie"
    handler._publish = lambda verdict: None
    captured: dict[str, Any] = {}

    def fake_probabilities(snapshots, images):
        captured["images"] = dict(images)
        return [0.9], "model", 0

    handler._probabilities = fake_probabilities
    result = handler.lambda_handler(s3_event(["raw/qa-snapshots/cyc-000001-part-000001.json"]))
    return result, captured["images"], fake


def test_handler_sends_the_photo_to_the_endpoint():
    snap = snapshot()
    objects = {
        "raw/qa-snapshots/cyc-000001-part-000001.json": json.dumps(snap).encode("utf-8"),
        snap["image_key"]: b"\x89PNG-cadru",
    }

    result, images, fake = run_handler(objects, name="photo")

    assert result["images"] == 1
    assert images["part-000001"] == b"\x89PNG-cadru"
    assert snap["image_key"] in fake.read


def test_handler_skips_a_missing_photo_without_failing_the_verdict():
    snap = snapshot()
    objects = {
        "raw/qa-snapshots/cyc-000001-part-000001.json": json.dumps(snap).encode("utf-8"),
    }

    result, images, _ = run_handler(objects, fail={snap["image_key"]}, name="missing")

    assert result["processed"] == 1
    assert result["images"] == 0
    assert images == {}


def test_handler_skips_a_photo_over_the_payload_limit():
    snap = snapshot()
    objects = {
        "raw/qa-snapshots/cyc-000001-part-000001.json": json.dumps(snap).encode("utf-8"),
        snap["image_key"]: b"x" * (core.IMAGE_PAYLOAD_LIMIT_BYTES + 1),
    }

    result, images, _ = run_handler(objects, name="oversized")

    assert result["images"] == 0
    assert images == {}


def test_handler_can_skip_the_photo_on_request():
    snap = snapshot()
    handler = load_module("qa_vision_handler_no_image", "lambdas/qa_vision_inference/handler.py")
    handler.SEND_IMAGE = False

    assert handler._read_image("cncdt-dev-data-lake", snap) is None


# ---------------------------------------------------------------- handler
def test_handler_exposes_lambda_handler():
    handler = load_module("qa_vision_handler_under_test", "lambdas/qa_vision_inference/handler.py")
    assert callable(handler.lambda_handler)


def test_handler_ignores_empty_event():
    handler = load_module("qa_vision_handler_empty_event", "lambdas/qa_vision_inference/handler.py")
    assert handler.lambda_handler({"Records": []}) == {"processed": 0, "rejects": 0, "skipped": 0}
