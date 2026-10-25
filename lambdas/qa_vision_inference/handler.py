"""Punctul de intrare al Lambda-ului de inspecție vizuală.

Declanșator: notificarea S3 pentru ``raw/qa-snapshots/*.json``.

1. citește metadatele cadrului QA din S3;
2. citește fotografia (``.png``, sub aceeași cheie) și o trimite inline, în base64, către
   endpoint-ul de viziune, cu identificatorii cadrului;
3. construiește verdictul PASS / REJECT din probabilitatea de defect dată de model;
4. scrie verdictul în ``curated/qa-verdicts/``;
5. publică verdictul pe topicul IoT de control, de unde simularea acționează lama ori
   pistonul de rejecție.

Verdictul vine doar din model. Fără endpoint configurat sau cu apelul căzut, Lambda-ul numără
cadrele (``QaModelMissing``, ``QaEndpointFailures``) și nu publică nimic: simularea are ruta ei
de rezervă, iar un verdict inventat local ar citi răspunsul în loc să îl deducă.

O excepție netratată ar face S3 să retrimită tot lotul de notificări, deci fiecare cadru este
tratat separat, iar erorile se jurnalizează și se numără. Toate apelurile AWS trec prin
:mod:`_shared.aws`; logica pură este în :mod:`qa_vision_core`, testabilă fără AWS.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Sequence

from _shared.aws import client
from _shared.logging_utils import configure_logging, log_event, note_error

import qa_vision_core as core

logger = configure_logging("cncdt.qa_vision")

TELEMETRY_NAMESPACE = os.environ.get("TELEMETRY_NAMESPACE", "CNCDT/Telemetry")
SAGEMAKER_ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME", "")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "unversioned")
CONTROL_TOPIC = os.environ.get("CONTROL_TOPIC", "")
DEFECT_THRESHOLD = float(os.environ.get("DEFECT_THRESHOLD", "0.5"))
DATA_LAKE_BUCKET = os.environ.get("DATA_LAKE_BUCKET", "")
VERDICT_PREFIX = os.environ.get("VERDICT_PREFIX", "curated/qa-verdicts")
IOT_DATA_ENDPOINT = os.environ.get("IOT_DATA_ENDPOINT", "")
# Trimiterea fotografiei către endpoint. Se poate opri cât timp modelul este tabular, ca
# cererea să rămână mică.
SEND_IMAGE = os.environ.get("SEND_IMAGE_TO_ENDPOINT", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _read_snapshot(ref: core.SnapshotRef) -> dict[str, Any]:
    s3 = client("s3")
    kwargs: dict[str, Any] = {"Bucket": ref.bucket, "Key": ref.key}
    if ref.version_id:
        kwargs["VersionId"] = ref.version_id
    payload = s3.get_object(**kwargs)["Body"].read()
    return core.parse_snapshot(json.loads(payload.decode("utf-8")))


def _read_image(bucket: str, snapshot: Mapping[str, Any]) -> bytes | None:
    """Citește fotografia cadrului, dacă a fost capturată și încape în cerere.

    Imaginea are același nume ca sidecar-ul, cu extensia ``.png``. Un cadru prea mare este
    sărit: cererea către endpoint are o limită de 6 MB, iar base64 o umflă cu o treime.
    """
    if not SEND_IMAGE or not snapshot.get("image_captured"):
        return None
    key = str(snapshot.get("image_key", ""))
    if not bucket or not key:
        return None
    try:
        payload = client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:  # o poză lipsă nu are voie să blocheze verdictul
        note_error(logger, "image_read_failed", exc, key=key)
        return None
    if len(payload) > core.IMAGE_PAYLOAD_LIMIT_BYTES:
        log_event(
            logger,
            "image_skipped_too_large",
            key=key,
            bytes=len(payload),
            limit=core.IMAGE_PAYLOAD_LIMIT_BYTES,
        )
        return None
    return payload


def _probabilities(
    snapshots: Sequence[Mapping[str, Any]],
    images: Mapping[str, bytes],
) -> tuple[list[float] | None, str, int, int]:
    """Probabilitățile de defect, de unde vin și două numărătoare: apeluri eșuate, model lipsă.

    Când nu există endpoint configurat se întoarce ``None`` pentru probabilități: verdictul nu
    se fabrică local din metadate, fiindcă acela ar citi răspunsul în loc să îl deducă.
    """
    if not SAGEMAKER_ENDPOINT_NAME:
        return None, "model_missing", 0, len(snapshots)

    try:
        # Cold start-ul containerului PyTorch poate depăși un minut; citirea așteaptă mai mult
        # decât timeout-ul implicit, iar funcția are 120 de secunde.
        runtime = client("sagemaker-runtime", read_timeout=110)
        response = runtime.invoke_endpoint(
            EndpointName=SAGEMAKER_ENDPOINT_NAME,
            ContentType=core.CONTENT_TYPE,
            Accept=core.CONTENT_TYPE,
            Body=core.endpoint_body(snapshots, images),
        )
        return core.parse_endpoint_response(response["Body"].read()), MODEL_VERSION, 0, 0
    except Exception as exc:  # modelul nu are voie să blocheze inspecția
        note_error(logger, "endpoint_invoke_failed", exc, endpoint=SAGEMAKER_ENDPOINT_NAME)
        return None, "endpoint_failed", 1, 0


def _write_verdict(verdict: dict[str, Any], snapshot: Mapping[str, Any], captured_at: str) -> str:
    key = core.verdict_key(VERDICT_PREFIX, snapshot, captured_at)
    if not DATA_LAKE_BUCKET:
        return key
    s3 = client("s3")
    s3.put_object(
        Bucket=DATA_LAKE_BUCKET,
        Key=key,
        Body=json.dumps(verdict, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    return key


def _publish(verdict: dict[str, Any]) -> None:
    if not CONTROL_TOPIC:
        log_event(logger, "control_topic_missing", verdict=verdict.get("verdict"))
        return
    iot = client("iot-data", endpoint_url=IOT_DATA_ENDPOINT or None)
    iot.publish(
        topic=CONTROL_TOPIC,
        qos=1,
        payload=json.dumps(verdict, ensure_ascii=False).encode("utf-8"),
    )


def _publish_metrics(processed: int, rejects: int, endpoint_failures: int, images_sent: int,
                     model_missing: int = 0) -> None:
    metrics = core.metric_data(processed, rejects, endpoint_failures, images_sent, model_missing)
    cloudwatch = client("cloudwatch")
    cloudwatch.put_metric_data(Namespace=TELEMETRY_NAMESPACE, MetricData=metrics)


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Procesează notificările S3. Nu ridică excepții pentru un singur cadru invalid."""
    refs = core.decode_s3_event(event)
    log_event(logger, "batch_received", snapshots=len(refs))

    snapshots: list[dict[str, Any]] = []
    images: dict[str, bytes] = {}
    skipped = 0
    for ref in refs:
        try:
            snapshot = _read_snapshot(ref)
        except Exception as exc:
            skipped += 1
            note_error(logger, "snapshot_read_failed", exc, key=ref.key)
            continue
        snapshots.append(snapshot)
        image = _read_image(ref.bucket, snapshot)
        if image is not None:
            images[str(snapshot.get("part_id", ""))] = image

    if not snapshots:
        return {"processed": 0, "rejects": 0, "skipped": skipped}

    probabilities, source, endpoint_failures, model_missing = _probabilities(snapshots, images)
    if probabilities is None:
        # Fără model nu se inventează un verdict: simularea are ruta ei de rezervă (ADR 0009),
        # iar aici se numără cazurile, ca lipsa modelului să se vadă în dashboard.
        log_event(
            logger,
            "no_verdict_without_a_model",
            snapshots=len(snapshots),
            source=source,
            endpoint_failures=endpoint_failures,
            model_missing=model_missing,
        )
        _publish_metrics(0, 0, endpoint_failures, 0, model_missing)
        return {
            "processed": 0,
            "rejects": 0,
            "skipped": skipped,
            "model_missing": model_missing,
            "endpoint_failures": endpoint_failures,
        }
    if len(probabilities) != len(snapshots):
        endpoint_failures += 1
        log_event(
            logger,
            "prediction_count_mismatch",
            snapshots=len(snapshots),
            predictions=len(probabilities),
        )
        _publish_metrics(0, 0, endpoint_failures, len(images))
        return {
            "processed": 0,
            "rejects": 0,
            "skipped": skipped,
            "endpoint_failures": endpoint_failures,
        }

    # Un cadru fără fotografie primește `judged: false` de la model. Absența judecății nu devine
    # verdict: se numără ca model lipsă, iar piesa urmează ruta de rezervă din simulare.
    judged = [(snapshot, probability) for snapshot, probability in zip(snapshots, probabilities)
              if probability is not None]
    unjudged = len(snapshots) - len(judged)
    if unjudged:
        model_missing += unjudged

    rejects = 0
    published = 0
    for snapshot, probability in judged:
        verdict = core.verdict_message(snapshot, probability, DEFECT_THRESHOLD, source)
        captured_at = str(snapshot.get("captured_at", ""))
        if verdict["verdict"] == core.VERDICT_REJECT:
            rejects += 1
        try:
            _write_verdict(verdict, snapshot, captured_at)
        except Exception as exc:  # verdictul se publică chiar dacă arhivarea eșuează
            note_error(logger, "verdict_write_failed", exc, part_id=verdict.get("part_id"))
        try:
            _publish(verdict)
            published += 1
        except Exception as exc:
            note_error(logger, "verdict_publish_failed", exc, part_id=verdict.get("part_id"))

    log_event(
        logger,
        "verdicts_ready",
        processed=len(judged),
        rejects=rejects,
        published=published,
        skipped=skipped,
        images=len(images),
        source=source,
        unjudged=unjudged,
    )
    try:
        _publish_metrics(len(judged), rejects, endpoint_failures, len(images), model_missing)
    except Exception as exc:  # metricile nu sunt critice pentru buclă
        note_error(logger, "metrics_publish_failed", exc)

    return {
        "processed": len(judged),
        "rejects": rejects,
        "published": published,
        "skipped": skipped,
        "images": len(images),
        "source": source,
        "model_missing": model_missing,
        "unjudged": unjudged,
    }
