"""Logica pură a Lambda-ului de inspecție vizuală.

Lambda-ul pornește de la notificarea S3 pentru fiecare cadru QA scris în
``raw/qa-snapshots/*.json``, trimite fotografia către endpoint-ul de viziune, transformă
răspunsul într-un verdict și îl publică pe topicul IoT de control, de unde simularea acționează
lama ori pistonul de rejecție.

Verdictul vine exclusiv din modelul antrenat. Când modelul lipsește ori apelul eșuează, nu se
inventează un verdict local: cadrele se numără, se jurnalizează și se lasă în seama rutei de
rezervă din simulare (ADR 0009). Nicio mărime care poartă răspunsul nu intră în cerere — vezi
:data:`LABEL_FIELDS`.

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

#: Câmpuri care poartă răspunsul, sub aceleași nume ca în `cncdt_mj/telemetry.py` și în
#: `cnc_cell/telemetry/qa_camera.py`. Cadrul QA nu are ce căuta cu ele, iar cererea către model
#: le refuză zgomotos: dacă ajung aici, cineva a pus eticheta pe calea de inferență.
LABEL_FIELDS: tuple[str, ...] = (
    "is_synthetic_defect",
    "tool_wear_index",
    "joint_friction_index",
    "surface_quality",
    "family",
    "defect",
    "verdict",
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
# Cadrele pentru care nu există model: fără el nu se inventează un verdict, iar numărătoarea
# face lipsa vizibilă în dashboard.
METRIC_MODEL_MISSING = "QaModelMissing"
# Producția, numărată pe verdict: piesa judecată ajunge în lada de piese bune ori în rebut.
METRIC_PARTS_GOOD = "PartsGood"
METRIC_PARTS_SCRAP = "PartsScrap"


class SnapshotError(ValueError):
    """Cadrul QA nu poate fi citit sau nu respectă contractul."""


class LeakedLabelError(SnapshotError):
    """Cererea către model ar duce cu ea ceva din răspuns."""


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
    doar câmpurile fără de care verdictul nu are sens, plus o singură interdicție: cadrul nu
    are voie să conțină nimic din răspuns (vezi :data:`LABEL_FIELDS`). Un cadru cu etichetă este
    refuzat zgomotos, ca o scurgere din simulator să nu ajungă tăcut la model.
    """
    if not isinstance(payload, Mapping):
        raise SnapshotError("cadrul QA nu este un obiect JSON")

    leaked = sorted(name for name in LABEL_FIELDS if name in payload)
    if leaked:
        raise LeakedLabelError(
            "cadrul QA poartă răspunsul: " + ", ".join(leaked)
            + ". Snapshot-ul pornește inferența, deci eticheta stă în datasetul de antrenare."
        )

    missing = [field for field in ("cycle_id", "part_id") if not payload.get(field)]
    if missing:
        raise SnapshotError(f"cadrul QA nu are câmpurile obligatorii: {', '.join(missing)}")

    snapshot = dict(payload)
    snapshot["image_captured"] = bool(payload.get("image_captured", False))
    return snapshot


def instance_payload(snapshot: Mapping[str, Any], image: bytes | None = None) -> dict[str, Any]:
    """O intrare din cererea către endpoint: identificatorii, camera și, când există, poza.

    Inspecția este vizuală: modelul primește fotografia ca PNG în base64, plus contextul de care
    are nevoie ca să știe ce judecă (piesa, ciclul, postul de inspecție, rezoluția). Nimic din
    ce ar conține răspunsul nu intră în cerere — vezi :data:`LABEL_FIELDS`.
    """
    instance: dict[str, Any] = {
        "part_id": str(snapshot.get("part_id", "")),
        "cycle_id": str(snapshot.get("cycle_id", "")),
        "camera_role": str(snapshot.get("camera_role", "")),
        "camera": str(snapshot.get("camera", "")),
        "resolution": [int(value) for value in snapshot.get("resolution", [])],
        "image_captured": bool(snapshot.get("image_captured", False)),
    }
    if image:
        instance["image"] = {
            "format": IMAGE_FORMAT,
            "encoding": "base64",
            "data": base64.b64encode(image).decode("ascii"),
        }
    leaked = sorted(set(instance) & set(LABEL_FIELDS))
    if leaked:
        raise LeakedLabelError(
            "cererea către model ar duce eticheta: " + ", ".join(leaked)
        )
    return instance


def endpoint_body(
    snapshots: Sequence[Mapping[str, Any]],
    images: Mapping[str, bytes] | None = None,
) -> bytes:
    """Corpul cererii către endpoint.

    ``images`` asociază identificatorul piesei cu octeții cadrului. O piesă fără imagine pleacă
    totuși, cu ``image_captured: false``: modelul poate răspunde „nu pot judeca", iar absența
    cadrului este o informație, nu un motiv de verdict inventat local.
    """
    by_part = images or {}
    instances = [
        instance_payload(snapshot, by_part.get(str(snapshot.get("part_id", ""))))
        for snapshot in snapshots
    ]
    return json.dumps(
        {
            "contract": FEATURE_CONTRACT,
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
    model_missing: int = 0,
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
    if model_missing:
        metrics.append(
            {"MetricName": METRIC_MODEL_MISSING, "Value": float(model_missing), "Unit": "Count"}
        )
    return metrics
