"""Logica pură a Lambda-ului de dispecer de comenzi.

Lambda-ul consumă coada SQS de comenzi (operator sau UI de demo) și publică fiecare comandă
pe topicul IoT potrivit către simulare: comenzile de degradare merg pe topicul de chaos, iar
celelalte pe topicul de comandă. Fiecare comandă primește și o urmă de audit în data lake.

Modulul nu importă boto3; publicarea și scrierea în S3 sunt injectate de ``handler.py``.
Numele modulului este unic intenționat: toate Lambda-urile ajung pe același ``sys.path`` în
teste, iar ``core`` este deja folosit de feature engineering.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

# Comenzile acceptate. Lista este identică cu enum-ul din contractul
# ``telemetry/schemas/cnc.control.v1.json`` (varianta „comandă de operator”).
ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "inject_tool_wear",
        "inject_joint_friction",
        "force_defect",
        "replace_tool",
        "repair_joints",
        "set_defect_probability",
        "pause",
        "resume",
        "start_cycle",
        "stop_cycle",
        "reset",
    }
)

# Comenzile care schimbă starea de degradare sau injectează defecte. Ajung pe topicul de
# chaos, ca simularea să le poată trata separat de fluxul normal de producție.
CHAOS_COMMANDS: frozenset[str] = frozenset(
    {"inject_tool_wear", "inject_joint_friction", "force_defect", "set_defect_probability"}
)

# Câmpurile numerice acceptate și intervalul lor.
NUMERIC_FIELDS: dict[str, tuple[float, float]] = {
    "amount": (0.0, 1.0),
    "value": (0.0, 1.0),
}


class CommandError(ValueError):
    """Mesajul din coadă nu poate fi citit sau nu este o comandă validă."""


def decode_record(record: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Întoarce ``(message_id, payload)`` dintr-o înregistrare SQS.

    Acceptă și mesaje împachetate de SNS (``{"Message": "{...}"}``), pentru cazul în care
    coada este alimentată printr-un topic.
    """
    message_id = str(record.get("messageId", ""))
    body = record.get("body", "")
    if isinstance(body, Mapping):
        payload: Any = dict(body)
    else:
        try:
            payload = json.loads(body)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CommandError(f"corpul mesajului nu este JSON: {exc}") from exc

    if isinstance(payload, Mapping) and isinstance(payload.get("Message"), str):
        try:
            payload = json.loads(payload["Message"])
        except json.JSONDecodeError as exc:
            raise CommandError(f"mesajul SNS conține un corp invalid: {exc}") from exc

    if not isinstance(payload, Mapping):
        raise CommandError("comanda nu este un obiect JSON")
    return message_id, dict(payload)


def validate_command(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validează comanda și întoarce varianta normalizată, gata de publicat."""
    command = payload.get("command")
    if command not in ALLOWED_COMMANDS:
        raise CommandError(f"comandă necunoscută sau lipsă: {command!r}")

    clean: dict[str, Any] = {"command": command}
    for field, (low, high) in NUMERIC_FIELDS.items():
        if field not in payload or payload[field] is None:
            continue
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CommandError(f"câmpul {field} trebuie să fie numeric")
        if not low <= float(value) <= high:
            raise CommandError(f"câmpul {field} trebuie să fie între {low} și {high}")
        clean[field] = float(value)
    return clean


def topic_for(command: str, control_topic: str, chaos_topic: str) -> str:
    """Topicul pe care se publică comanda."""
    if command in CHAOS_COMMANDS and chaos_topic:
        return chaos_topic
    if not control_topic:
        raise CommandError("topicul de control nu este configurat")
    return control_topic


def audit_key(prefix: str, command: str, message_id: str, ts_ms: int) -> str:
    """Cheia urmei de audit, partiționată pe zi."""
    day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    safe_id = message_id or "unknown"
    return f"{prefix}/dt={day}/{ts_ms}-{command}-{safe_id}.json"


def audit_record(
    command: Mapping[str, Any],
    message_id: str,
    topic: str,
    ts_ms: int,
    outcome: str,
) -> dict[str, Any]:
    """Urmă de audit scrisă în data lake pentru fiecare comandă."""
    return {
        "schema_version": "1.0.0",
        "message_id": message_id,
        "command": command.get("command"),
        "payload": dict(command),
        "topic": topic,
        "outcome": outcome,
        "dispatched_at_ms": ts_ms,
        "dispatched_at": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
    }


def batch_result(failed_message_ids: Sequence[str]) -> dict[str, Any]:
    """Răspunsul cerut de ``ReportBatchItemFailures``: doar mesajele căzute se reiau."""
    return {"batchItemFailures": [{"itemIdentifier": mid} for mid in failed_message_ids]}


def sent_timestamp_ms(record: Mapping[str, Any]) -> int:
    """Momentul trimiterii, din atributele SQS. Cade pe ora curentă dacă lipsește."""
    attributes = record.get("attributes") or {}
    raw = attributes.get("SentTimestamp")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(datetime.now(timezone.utc).timestamp() * 1000)
