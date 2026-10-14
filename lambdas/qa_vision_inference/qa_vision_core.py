"""Logica pură a Lambda-ului de inspecție vizuală.

Lambda-ul pornește de la notificarea S3 pentru fiecare cadru QA scris în
``raw/qa-snapshots/*.json``, trimite fotografia și metadatele către endpoint-ul de viziune,
transformă răspunsul într-un verdict și îl publică pe topicul IoT de control, de unde
simularea acționează lama ori pistonul de rejecție.

Modulul nu importă boto3: citirea din S3, apelul endpoint-ului și publicarea sunt injectate
de ``handler.py``. Numele modulului este unic intenționat: toate Lambda-urile ajung pe
același ``sys.path`` în teste, iar ``core`` este deja folosit de feature engineering.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

VERDICT_PASS = "PASS"
VERDICT_REJECT = "REJECT"

CONTENT_TYPE = "application/json"

# Ordinea intrărilor modelului de viziune. Coincide cu antrenarea
# (``ml/training/vision_qa``) și cu ``ml/inference/vision_qa``.
MODEL_FEATURE_ORDER: tuple[str, ...] = (
    "tool_wear_index",
    "joint_friction_index",
    "surface_quality",
    "image_available",
)

# Imaginea pleacă inline, în base64, în corpul cererii. InvokeEndpoint acceptă cel mult 6 MB,
# iar base64 umflă datele cu o treime: de aceea pragul este sub limită, nu la ea.
IMAGE_FORMAT = "png"
FEATURE_CONTRACT = "cnc.qa.snapshot.v1"
IMAGE_PAYLOAD_LIMIT_BYTES = 4 * 1024 * 1024

METRIC_PROCESSED = "QaVerdictsProcessed"
METRIC_REJECTS = "QaRejects"
METRIC_ENDPOINT_FAILURES = "QaEndpointFailures"
METRIC_IMAGES_SENT = "QaImagesSent"
# Producția, numărată pe verdict: piesa judecată ajunge în lada de piese bune ori în rebut.
METRIC_PARTS_GOOD = "PartsGood"
METRIC_PARTS_SCRAP = "PartsScrap"


class SnapshotError(ValueError):
    """Cadrul QA nu poate fi citit sau nu respectă contractul."""


@dataclass(frozen=True)
class SnapshotRef:
    """Referința unui cadru QA, extrasă din notificarea S3."""

    bucket: str
    key: str
    version_id: str = ""


def decode_s3_event(event: Mapping[str, Any]) -> list[SnapshotRef]:
    """Extrage referințele din notificarea S3, ignorând evenimentele incomplete."""
    refs: list[SnapshotRef] = []
    for record in event.get("Records") or []:
        s3 = record.get("s3") or {}
        bucket = (s3.get("bucket") or {}).get("name", "")
        obj = s3.get("object") or {}
        key = obj.get("key", "")
        if not bucket or not key:
            continue
        refs.append(
            SnapshotRef(
                bucket=bucket,
                key=urllib.parse.unquote_plus(key),
                version_id=obj.get("versionId", "") or "",
            )
        )
    return refs


def parse_snapshot(payload: Any) -> dict[str, Any]:
    """Validează câmpurile de care depinde verdictul.

    Contractul complet este în ``telemetry/schemas/cnc.qa.snapshot.v1.json``. Aici se cer
    doar câmpurile fără de care verdictul nu are sens.
    """
    if not isinstance(payload, Mapping):
        raise SnapshotError("cadrul QA nu este un obiect JSON")

    missing = [field for field in ("cycle_id", "part_id") if not payload.get(field)]
    if missing:
        raise SnapshotError(f"cadrul QA nu are câmpurile obligatorii: {', '.join(missing)}")

    snapshot = dict(payload)
    snapshot["image_captured"] = bool(payload.get("image_captured", False))
    return snapshot


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def snapshot_features(snapshot: Mapping[str, Any]) -> list[float]:
    """Vectorul de intrare al modelului, în ordinea ``MODEL_FEATURE_ORDER``."""
    return [
        _number(snapshot.get("tool_wear_index")) or 0.0,
        _number(snapshot.get("joint_friction_index")) or 0.0,
        _number(snapshot.get("surface_quality")) or 0.0,
        1.0 if snapshot.get("image_captured") else 0.0,
    ]


def instance_payload(snapshot: Mapping[str, Any], image: bytes | None = None) -> dict[str, Any]:
    """O intrare din cererea către endpoint: metadate, caracteristici și, când există, poza.

    Imaginea este cea care face inspecția vizuală: modelul o primește ca PNG în base64,
    alături de vectorul de caracteristici, care rămâne disponibil pentru un model tabular
    ori ca rezervă când cadrul lipsește.
    """
    instance: dict[str, Any] = {
        "part_id": str(snapshot.get("part_id", "")),
        "cycle_id": str(snapshot.get("cycle_id", "")),
        "camera_role": str(snapshot.get("camera_role", "")),
        "features": snapshot_features(snapshot),
    }
    if image:
        instance["image"] = {
            "format": IMAGE_FORMAT,
            "encoding": "base64",
            "data": base64.b64encode(image).decode("ascii"),
        }
    return instance


def endpoint_body(
    snapshots: Sequence[Mapping[str, Any]],
    images: Mapping[str, bytes] | None = None,
) -> bytes:
    """Corpul cererii către endpoint.

    ``images`` asociază identificatorul piesei cu octeții cadrului; o piesă fără imagine
    trimite doar vectorul de caracteristici, iar verdictul rămâne posibil pe praguri.
    """
    by_part = images or {}
    instances = [
        instance_payload(snapshot, by_part.get(str(snapshot.get("part_id", ""))))
        for snapshot in snapshots
    ]
    return json.dumps(
        {
            "contract": FEATURE_CONTRACT,
            "feature_names": list(MODEL_FEATURE_ORDER),
            "instances": instances,
        }
    ).encode("utf-8")


def parse_endpoint_response(body: bytes | str) -> list[float]:
    """Extrage probabilitatea de defect din răspunsul endpoint-ului.

    Acceptă ``{"predictions": [...]}``, ``{"outputs": [...]}`` sau o listă simplă. Fiecare
    element poate fi un număr ori un dicționar cu ``defect_probability`` / ``score``. Un
    vector (softmax) este tratat ca atare: clasa de defect este ultima.
    """
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    try:
        payload = json.loads(body or "{}")
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"răspunsul endpoint-ului nu este JSON: {exc}") from exc

    if isinstance(payload, Mapping):
        values = payload.get("predictions", payload.get("outputs", []))
    else:
        values = payload
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise SnapshotError("răspunsul endpoint-ului nu conține o listă de predicții")

    probabilities: list[float] = []
    for item in values:
        raw: Any = item
        if isinstance(item, Mapping):
            raw = item.get("defect_probability", item.get("score", item.get("probability")))
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            raw = raw[-1] if raw else None
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            raise SnapshotError("predicția nu conține o probabilitate numerică")
        probabilities.append(min(max(float(raw), 0.0), 1.0))
    return probabilities


def heuristic_probability(snapshot: Mapping[str, Any]) -> float:
    """Scor de rezervă, când endpoint-ul nu este configurat sau invocarea eșuează.

    Combină indicii de degradare cu calitatea suprafeței. Formula este deliberat simplă și
    deterministă: în demo contează că reacția are loc, nu calibrarea exactă a scorului.
    """
    wear = _number(snapshot.get("tool_wear_index")) or 0.0
    friction = _number(snapshot.get("joint_friction_index")) or 0.0
    quality = _number(snapshot.get("surface_quality"))
    score = 0.55 * wear + 0.25 * friction + 0.35 * (1.0 - (quality if quality is not None else 1.0))
    if snapshot.get("is_synthetic_defect"):
        score = max(score, 0.9)
    if not snapshot.get("image_captured"):
        # Fără imagine, verdictul rămâne prudent: nu se rejecționează piese doar pentru că
        # lipsește captura, dar nici nu se declară PASS cu încredere mare.
        score = min(score, 0.49)
    return min(max(score, 0.0), 1.0)


def verdict_message(
    snapshot: Mapping[str, Any],
    probability: float,
    threshold: float,
    model_version: str,
    reason: str = "",
) -> dict[str, Any]:
    """Mesajul de control din contractul ``cnc.control.v1`` (varianta verdict)."""
    message: dict[str, Any] = {
        "verdict": VERDICT_REJECT if probability >= threshold else VERDICT_PASS,
        "part_id": str(snapshot.get("part_id", "")),
        "cycle_id": str(snapshot.get("cycle_id", "")),
        "confidence": round(min(max(probability, 0.0), 1.0), 4),
        "model_version": model_version,
    }
    if reason:
        message["reason"] = reason
    return message


def verdict_key(prefix: str, snapshot: Mapping[str, Any], captured_at: str) -> str:
    """Cheia din data lake, partiționată pe zi și identificată prin ciclu și piesă."""
    day = (captured_at or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cycle = str(snapshot.get("cycle_id") or "unknown")
    part = str(snapshot.get("part_id") or "unknown")
    return f"{prefix}/dt={day}/{cycle}-{part}.json"


def metric_data(
    processed: int,
    rejects: int,
    endpoint_failures: int,
    images_sent: int = 0,
) -> list[dict[str, Any]]:
    """Metricile publicate în CloudWatch după fiecare invocare."""
    metrics = [
        {"MetricName": METRIC_PROCESSED, "Value": float(processed), "Unit": "Count"},
        {"MetricName": METRIC_REJECTS, "Value": float(rejects), "Unit": "Count"},
        # Producția pe oră se citește direct din aceste două contoare, însumate pe o oră.
        {"MetricName": METRIC_PARTS_GOOD, "Value": float(max(0, processed - rejects)), "Unit": "Count"},
        {"MetricName": METRIC_PARTS_SCRAP, "Value": float(rejects), "Unit": "Count"},
    ]
    if endpoint_failures:
        metrics.append(
            {"MetricName": METRIC_ENDPOINT_FAILURES, "Value": float(endpoint_failures), "Unit": "Count"}
        )
    if images_sent:
        metrics.append(
            {"MetricName": METRIC_IMAGES_SENT, "Value": float(images_sent), "Unit": "Count"}
        )
    return metrics
