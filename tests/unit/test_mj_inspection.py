"""Teste pentru clientul de inspecție al geamănului, fără AWS și fără MuJoCo.

Contractul este cel al containerului de inferență: cererea duce măsurători și poza, răspunsul
aduce probabilitatea de defect și familia, iar ruta se decide în celulă. Aici se apără două
lucruri: eticheta nu pleacă niciodată în cerere, iar orice răspuns nesigur alege rebutul.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest
from cncdt_mj import inspection


def _reply(**overrides: object) -> bytes:
    prediction = {
        "part_id": "part-000001",
        "defect_probability": 0.12,
        "judged": True,
        "family": "wheel_100",
        "family_probability": 0.98,
    }
    prediction.update(overrides)
    return json.dumps({"predictions": [prediction]}).encode("utf-8")


def test_request_carries_measurements_and_the_image_but_no_label() -> None:
    body = json.loads(inspection.request_body(
        part_id="part-000001", cycle_id="cyc-000001", image=b"\x89PNG\r\n",
        camera="arm2_wrist_camera", resolution=(640, 480)))

    instance = body["instances"][0]
    assert instance["part_id"] == "part-000001"
    assert instance["camera_role"] == "sorting"
    assert instance["resolution"] == [640, 480]
    assert base64.b64decode(instance["image"]["data"]) == b"\x89PNG\r\n"
    assert not {"family", "defect", "verdict", "is_synthetic_defect"} & set(instance)


def test_confident_pass_routes_to_the_family_compartment() -> None:
    result = inspection.parse_response(_reply(), expected_part_id="part-000001")

    assert result.verdict == inspection.VERDICT_PASS
    assert result.route == inspection.ROUTE_RACK
    assert result.family == "wheel_100"
    assert result.reason == "model"


def test_defect_above_the_threshold_routes_to_scrap() -> None:
    result = inspection.parse_response(_reply(defect_probability=0.95))

    assert result.verdict == inspection.VERDICT_REJECT
    assert result.route == inspection.ROUTE_SCRAP
    assert result.reason == "verdict"


def test_threshold_is_the_one_deployed_with_the_endpoint() -> None:
    """Pragul clientului este cel din `dev.tfvars`, ca verdictul să nu difere de Lambda."""
    assert inspection.DEFAULT_THRESHOLD == 0.91
    just_below = inspection.parse_response(_reply(defect_probability=0.90))
    assert just_below.route == inspection.ROUTE_RACK


def test_unjudged_frame_takes_the_fallback_route() -> None:
    result = inspection.parse_response(_reply(judged=False, defect_probability=0.5))

    assert result.verdict == inspection.VERDICT_REJECT
    assert result.route == inspection.ROUTE_SCRAP
    assert result.reason == "unjudged"


def test_uncertain_family_takes_the_fallback_route() -> None:
    result = inspection.parse_response(_reply(family_probability=0.4))

    assert result.route == inspection.ROUTE_SCRAP
    assert result.reason == "family_uncertain"


def test_missing_family_takes_the_fallback_route() -> None:
    payload = json.loads(_reply())
    del payload["predictions"][0]["family"]
    del payload["predictions"][0]["family_probability"]

    result = inspection.parse_response(payload)

    assert result.route == inspection.ROUTE_SCRAP
    assert result.reason == "family_missing"


def test_response_for_another_part_is_refused() -> None:
    with pytest.raises(inspection.ResponseError, match="altă piesă|part-000002"):
        inspection.parse_response(_reply(part_id="part-000002"), expected_part_id="part-000001")


def test_probability_outside_the_unit_interval_is_refused() -> None:
    with pytest.raises(inspection.ResponseError, match="defect_probability"):
        inspection.parse_response(_reply(defect_probability=1.4))


def test_inspect_frame_sends_the_sorting_frame_and_returns_the_verdict(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Viewer-ul și sesiunea folosesc același drum: cadrul pleacă, verdictul se validează."""
    frame = tmp_path / "sorting.png"
    frame.write_bytes(b"\x89PNG\r\n\x1a\n")
    seen: dict[str, object] = {}

    def fake_invoke(endpoint: str, body: bytes, *, region: str) -> bytes:
        seen["endpoint"] = endpoint
        seen["region"] = region
        seen["body"] = json.loads(body)
        return _reply()

    monkeypatch.setattr(inspection, "invoke", fake_invoke)
    verdict = inspection.inspect_frame(
        frame, endpoint="cncdt-dev-vision-qa", region="eu-central-1",
        part_id="part-000001", cycle_id="cyc-000001",
        camera="arm2_wrist_camera", resolution=(640, 480))

    assert seen["endpoint"] == "cncdt-dev-vision-qa"
    assert seen["region"] == "eu-central-1"
    instance = seen["body"]["instances"][0]  # type: ignore[index]
    assert instance["camera_role"] == "sorting"
    assert instance["image"]["data"]
    assert not {"family", "defect", "verdict"} & set(instance)
    assert verdict.route == inspection.ROUTE_RACK
    assert verdict.family == "wheel_100"


def test_invoke_reads_the_body_written_by_the_cli(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        seen["request"] = Path(
            command[command.index("--body") + 1].removeprefix("fileb://")).read_bytes()
        Path(command[-1]).write_bytes(_reply())
        return subprocess.CompletedProcess(command, 0, "", "")

    body = inspection.request_body(
        part_id="part-000001", cycle_id="cyc-000001", image=b"png",
        camera="arm2_wrist_camera", resolution=(640, 480))
    answer = inspection.invoke("cncdt-dev-vision-qa", body, runner=runner)

    command = seen["command"]
    assert isinstance(command, list)
    assert command[:3] == ["aws", "sagemaker-runtime", "invoke-endpoint"]
    assert "cncdt-dev-vision-qa" in command
    assert seen["request"] == body
    assert inspection.parse_response(answer).route == inspection.ROUTE_RACK


def test_invoke_reports_a_cli_failure() -> None:
    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 255, "", "Unable to locate credentials")

    with pytest.raises(inspection.ResponseError, match="Unable to locate credentials"):
        inspection.invoke("cncdt-dev-vision-qa", b"{}", runner=runner)
