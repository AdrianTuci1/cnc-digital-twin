"""Punctul de intrare al Lambda-ului de dispecer de comenzi.

Declanșator: coada SQS de comenzi (maxim 10 mesaje per lot, fereastră de 5 secunde).

1. validează fiecare comandă față de contractul ``cnc.control.v1``;
2. publică pe topicul IoT potrivit: chaos pentru degradare, comandă pentru restul;
3. scrie o urmă de audit în data lake;
4. întoarce ``batchItemFailures``, ca SQS să reia doar mesajele care au căzut.

Funcția nu ridică excepții pentru un singur mesaj: o excepție netratată ar face SQS să
retrimită tot lotul. Apelurile AWS trec prin :mod:`_shared.aws`; logica pură este în
:mod:`control_core`, testabilă fără AWS.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from _shared.aws import client
from _shared.logging_utils import configure_logging, log_event, note_error

import control_core as core

logger = configure_logging("cncdt.control_dispatcher")

CONTROL_TOPIC = os.environ.get("CONTROL_TOPIC", "")
CHAOS_TOPIC = os.environ.get("CHAOS_TOPIC", "")
AUDIT_PREFIX = os.environ.get("AUDIT_PREFIX", "curated/control-audit")
DATA_LAKE_BUCKET = os.environ.get("DATA_LAKE_BUCKET", "")
IOT_DATA_ENDPOINT = os.environ.get("IOT_DATA_ENDPOINT", "")


def _iot_client() -> Any:
    return client("iot-data", endpoint_url=IOT_DATA_ENDPOINT or None)


def _publish(topic: str, command: Mapping[str, Any]) -> None:
    payload = json.dumps(dict(command), ensure_ascii=False).encode("utf-8")
    _iot_client().publish(topic=topic, qos=1, payload=payload)


def _write_audit(record: Mapping[str, Any]) -> None:
    if not DATA_LAKE_BUCKET:
        return
    key = core.audit_key(
        AUDIT_PREFIX,
        str(record.get("command", "unknown")),
        str(record.get("message_id", "")),
        int(record.get("dispatched_at_ms", 0)),
    )
    s3 = client("s3")
    s3.put_object(
        Bucket=DATA_LAKE_BUCKET,
        Key=key,
        Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Consumă lotul SQS și întoarce doar mesajele care trebuie reluate."""
    records = event.get("Records") or []
    log_event(logger, "batch_received", messages=len(records))

    failed: list[str] = []
    dispatched = 0
    rejected = 0

    for raw_record in records:
        message_id = str(raw_record.get("messageId", ""))
        try:
            _, payload = core.decode_record(raw_record)
            command = core.validate_command(payload)
        except core.CommandError as exc:
            rejected += 1
            note_error(logger, "command_rejected", exc, message_id=message_id)
            # Un mesaj invalid nu se reia: ar cădea la nesfârșit. Ajunge în DLQ prin
            # politica de redrive doar dacă îl declarăm eșuat, deci îl consumăm.
            continue

        ts_ms = core.sent_timestamp_ms(raw_record)
        try:
            topic = core.topic_for(str(command["command"]), CONTROL_TOPIC, CHAOS_TOPIC)
            _publish(topic, command)
        except Exception as exc:
            failed.append(message_id)
            note_error(logger, "command_publish_failed", exc, message_id=message_id)
            continue

        dispatched += 1
        try:
            _write_audit(core.audit_record(command, message_id, topic, ts_ms, outcome="published"))
        except Exception as exc:  # auditul este best-effort, comanda a plecat deja
            note_error(logger, "audit_write_failed", exc, message_id=message_id)

    log_event(logger, "batch_dispatched", dispatched=dispatched, rejected=rejected, failed=len(failed))
    return core.batch_result(failed)
