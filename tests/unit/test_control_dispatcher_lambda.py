"""Teste pentru Lambda-ul de dispecer de comenzi.

Acoperă logica pură din ``control_core``: decodarea mesajelor SQS, validarea comenzilor față
de contractul ``cnc.control.v1``, alegerea topicului și urma de audit.

Modulele se încarcă prin cale, cu nume unice: directorul Lambda-ului nu se pune pe
``sys.path``, pentru că are și el un ``handler.py`` care ar umbri modulul de feature
engineering. Modulul pur se înregistrează în ``sys.modules``, ca ``import control_core``
din handler să îl găsească.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

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


core = load_module("control_core", "lambdas/control_dispatcher/control_core.py")


def sqs_record(body, message_id: str = "msg-1", sent_ms: str = "1767225600000") -> dict:
    return {
        "messageId": message_id,
        "receiptHandle": "rh-1",
        "body": body if isinstance(body, str) else json.dumps(body),
        "attributes": {"SentTimestamp": sent_ms},
    }


# ---------------------------------------------------------------- contract
def test_allowed_commands_match_the_control_schema():
    schema = json.loads(CONTROL_SCHEMA.read_text(encoding="utf-8"))
    enums = [
        variant["properties"]["command"]["enum"]
        for variant in schema["oneOf"]
        if "command" in variant.get("properties", {})
    ]
    assert len(enums) == 1, "contractul trebuie să aibă o singură listă de comenzi"
    assert set(enums[0]) == set(core.ALLOWED_COMMANDS)


def test_chaos_commands_are_a_subset_of_allowed():
    assert core.CHAOS_COMMANDS <= core.ALLOWED_COMMANDS


# ---------------------------------------------------------------- decodare
def test_decode_record_parses_json_body():
    message_id, payload = core.decode_record(sqs_record({"command": "pause"}))
    assert message_id == "msg-1"
    assert payload == {"command": "pause"}


def test_decode_record_unwraps_sns_message():
    wrapped = {"Type": "Notification", "Message": json.dumps({"command": "resume"})}
    _, payload = core.decode_record(sqs_record(wrapped))
    assert payload == {"command": "resume"}


def test_decode_record_rejects_invalid_body():
    with pytest.raises(core.CommandError):
        core.decode_record(sqs_record("nu e json"))
    with pytest.raises(core.CommandError):
        core.decode_record(sqs_record("[1, 2, 3]"))


# ---------------------------------------------------------------- validare
def test_validate_command_normalizes_numeric_fields():
    clean = core.validate_command({"command": "inject_tool_wear", "amount": 0.5})
    assert clean == {"command": "inject_tool_wear", "amount": 0.5}


def test_validate_command_drops_unknown_fields():
    clean = core.validate_command({"command": "pause", "cineva": "necunoscut"})
    assert clean == {"command": "pause"}


def test_validate_command_rejects_unknown_or_missing():
    with pytest.raises(core.CommandError):
        core.validate_command({"command": "autodistrugere"})
    with pytest.raises(core.CommandError):
        core.validate_command({})


def test_validate_command_enforces_numeric_bounds():
    with pytest.raises(core.CommandError):
        core.validate_command({"command": "inject_tool_wear", "amount": 1.5})
    with pytest.raises(core.CommandError):
        core.validate_command({"command": "inject_tool_wear", "amount": "mult"})
    with pytest.raises(core.CommandError):
        core.validate_command({"command": "inject_tool_wear", "amount": True})


# ---------------------------------------------------------------- rutare
def test_topic_for_sends_degradation_commands_to_chaos():
    assert core.topic_for("inject_tool_wear", "cmd", "chaos") == "chaos"
    assert core.topic_for("set_defect_probability", "cmd", "chaos") == "chaos"


def test_topic_for_sends_production_commands_to_control():
    assert core.topic_for("pause", "cmd", "chaos") == "cmd"
    assert core.topic_for("replace_tool", "cmd", "chaos") == "cmd"


def test_topic_for_falls_back_to_control_without_chaos_topic():
    assert core.topic_for("inject_tool_wear", "cmd", "") == "cmd"


def test_topic_for_requires_a_control_topic():
    with pytest.raises(core.CommandError):
        core.topic_for("pause", "", "chaos")


# ---------------------------------------------------------------- audit
def test_audit_key_is_partitioned_by_day():
    key = core.audit_key("curated/control-audit", "pause", "msg-1", 1767225600000)
    assert key == "curated/control-audit/dt=2026-01-01/1767225600000-pause-msg-1.json"


def test_audit_record_captures_outcome_and_topic():
    record = core.audit_record({"command": "pause"}, "msg-1", "cmd-topic", 1767225600000, "published")
    assert record["command"] == "pause"
    assert record["topic"] == "cmd-topic"
    assert record["outcome"] == "published"
    assert record["schema_version"] == "1.0.0"


def test_sent_timestamp_falls_back_to_now():
    assert core.sent_timestamp_ms(sqs_record({"command": "pause"})) == 1767225600000
    assert core.sent_timestamp_ms({"messageId": "m"}) > 0


def test_batch_result_lists_only_failures():
    result = core.batch_result(["msg-2"])
    assert result == {"batchItemFailures": [{"itemIdentifier": "msg-2"}]}
    assert core.batch_result([]) == {"batchItemFailures": []}


# ---------------------------------------------------------------- handler
def test_handler_exposes_lambda_handler():
    handler = load_module("control_handler_under_test", "lambdas/control_dispatcher/handler.py")
    assert callable(handler.lambda_handler)


def test_handler_reports_invalid_command_without_retry():
    handler = load_module("control_handler_invalid", "lambdas/control_dispatcher/handler.py")
    event = {"Records": [sqs_record({"command": "necunoscuta"})]}
    assert handler.lambda_handler(event) == {"batchItemFailures": []}
