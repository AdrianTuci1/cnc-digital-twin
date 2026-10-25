"""Teste pentru contractul containerului de inferență vizuală, fără torch."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = load_module(
    "vision_inference_core",
    "ml/inference/vision_qa/vision_inference_core.py",
)


def test_parse_instances_accepts_the_lambda_body() -> None:
    body = json.dumps({"instances": [{"part_id": "part-1"}, {"part_id": "part-2"}]})
    assert [row["part_id"] for row in core.parse_instances(body)] == ["part-1", "part-2"]


def test_parse_instances_accepts_bytes_and_rejects_a_bad_shape() -> None:
    assert core.parse_instances(b'{"instances": [{}]}') == [{}]
    with pytest.raises(core.RequestError):
        core.parse_instances(json.dumps({"altceva": []}))
    with pytest.raises(core.RequestError):
        core.parse_instances("nu e json")


def test_image_bytes_decodes_base64_and_tolerates_a_missing_photo() -> None:
    payload = base64.b64encode(b"png").decode("ascii")
    instance = {"image": {"format": "png", "encoding": "base64", "data": payload}}
    assert core.image_bytes(instance) == b"png"
    assert core.image_bytes({}) is None
    assert core.image_bytes({"image": {"encoding": "base64", "data": "?"}}) is None
    assert core.image_bytes({"image": {"encoding": "raw", "data": payload}}) is None


def test_predictions_are_clamped_and_keep_the_part_id() -> None:
    assert core.prediction("part-1", 1.7)["defect_probability"] == 1.0
    assert core.prediction("part-1", -0.2)["defect_probability"] == 0.0
    assert core.prediction("part-1", 0.4, judged=False)["judged"] is False
    assert core.response([core.prediction("part-1", 0.4)]) == {
        "predictions": [
            {"part_id": "part-1", "defect_probability": 0.4, "judged": True}
        ]
    }


def test_predictions_carry_the_family_only_when_the_model_saw_the_image() -> None:
    judged = core.prediction("part-1", 0.2, family="hex_68", family_probability=1.4)
    assert judged["family"] == "hex_68"
    assert judged["family_probability"] == 1.0
    assert "family" not in core.prediction("part-1", 0.2)
    assert "family" not in core.prediction("part-1", 0.2, judged=False,
                                           family="hex_68", family_probability=0.9)

