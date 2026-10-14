"""Punctul de intrare al Lambda-ului de feature engineering.

Rolul funcției: transformare Firehose, pe microbatch-ul de 60 de secunde.

1. decodifică lotul de eșantioane brute;
2. agregă fiecare fereastră în câte o înregistrare din contract;
3. cere scorul de anomalie modelului, dacă endpoint-ul este configurat;
4. publică metricele în CloudWatch;
5. scrie ferestrele pentru care modelul a eșuat în ``replay/manual/``;
6. întoarce înregistrările agregate către Firehose, care le scrie în Parquet în S3.

Funcția nu ridică excepții pentru o singură înregistrare: fiecare primește un rezultat
propriu în răspuns. O excepție netratată ar face Firehose să retrimită tot lotul.

Toate apelurile AWS trec prin :mod:`_shared.aws`; logica de transformare este în
:mod:`core`, testabilă fără AWS.
"""

from __future__ import annotations

import json
import os
from typing import Any

from _shared.aws import client
from _shared.logging_utils import configure_logging, log_event, note_error

from cncdt_ml.features import AnomalyThresholds

import core

logger = configure_logging("cncdt.feature_engineering")

TELEMETRY_NAMESPACE = os.environ.get("TELEMETRY_NAMESPACE", "CNCDT/Telemetry")
SAGEMAKER_ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME", "")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "unversioned")
ANOMALY_SCORE_THRESHOLD = float(os.environ.get("ANOMALY_SCORE_THRESHOLD", "0.65"))
DATA_LAKE_BUCKET = os.environ.get("DATA_LAKE_BUCKET", "")
WINDOW_SECONDS = int(os.environ.get("WINDOW_SECONDS", "60"))
# Caracteristicile brute nu se publică implicit: nu le citește niciun widget sau alarmă, iar
# fiecare metrică custom costă lunar. Se pornesc doar pentru analiză exploratorie.
PUBLISH_FEATURE_METRICS = os.environ.get("PUBLISH_FEATURE_METRICS", "false").lower() in {
    "1",
    "true",
    "yes",
}
REPLAY_PREFIX = os.environ.get("REPLAY_PREFIX", "replay/manual")
# Pantele degradării, aceleași valori pe care le folosește simularea. Din ele se estimează
# RUL publicat în CloudWatch; zero înseamnă că metrica de RUL nu se publică.
DEGRADATION_RATES = {
    "cnc": float(os.environ.get("TOOL_WEAR_RATE", "0")),
    "robot": float(os.environ.get("JOINT_FRICTION_RATE", "0")),
}

_THRESHOLDS = AnomalyThresholds()


def _endpoint_provider(failure_counter: list[int]) -> core.PredictionProvider | None:
    """Funcția care cheamă endpoint-ul, cu buget de apeluri pe invocare.

    ``failure_counter`` primește câte un element pentru fiecare apel eșuat, ca handlerul să
    publice metrica ``EndpointFailures`` pe care o citește dashboard-ul de infrastructură.
    """
    if not SAGEMAKER_ENDPOINT_NAME:
        return None

    def provider(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        budget = records[: core.MAX_ENDPOINT_CALLS]
        if len(records) > len(budget):
            log_event(
                logger,
                "endpoint_budget_exceeded",
                windows=len(records),
                called=len(budget),
                fallback="thresholds",
            )
        try:
            runtime = client("sagemaker-runtime")
            response = runtime.invoke_endpoint(
                EndpointName=SAGEMAKER_ENDPOINT_NAME,
                ContentType=core.CONTENT_TYPE,
                Accept=core.CONTENT_TYPE,
                Body=core.endpoint_body(budget),
            )
            predictions = core.parse_endpoint_response(response["Body"].read())
        except Exception as exc:  # endpoint-ul nu are voie să blocheze livrarea
            note_error(logger, "endpoint_invoke_failed", exc, endpoint=SAGEMAKER_ENDPOINT_NAME)
            failure_counter.append(1)
            return []
        for prediction in predictions:
            prediction.setdefault("model_version", MODEL_VERSION)
        return predictions

    return provider


def _publish_metrics(metrics: list[dict[str, Any]]) -> None:
    if not metrics:
        return
    cloudwatch = client("cloudwatch")
    for batch in core.batch_metrics(metrics):
        cloudwatch.put_metric_data(Namespace=TELEMETRY_NAMESPACE, MetricData=list(batch))


def _write_replay(results: list[core.WindowResult], reason: str) -> None:
    """Salvează ferestrele cu probleme, ca să poată fi reluate."""
    if not DATA_LAKE_BUCKET or not results:
        return
    s3 = client("s3")
    for result in results:
        payload = core.replay_payload(result, reason=reason)
        key = f"{REPLAY_PREFIX}/dt={payload['captured_at'][:10]}/{result.record_id}.json"
        s3.put_object(
            Bucket=DATA_LAKE_BUCKET,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Transformarea Firehose. Întoarce un rezultat pentru fiecare recordId primit."""
    decoded, failures = core.decode_batch(event)
    log_event(logger, "batch_received", records=len(decoded), malformed=len(failures))

    if not decoded:
        return core.assemble_response([], failures)

    endpoint_failures: list[int] = []
    results, untimed = core.aggregate(
        decoded,
        window_s=WINDOW_SECONDS,
        thresholds=_THRESHOLDS,
        prediction_provider=_endpoint_provider(endpoint_failures),
        score_threshold=ANOMALY_SCORE_THRESHOLD,
        model_version=MODEL_VERSION,
    )

    log_event(
        logger,
        "windows_aggregated",
        windows=len(results),
        samples=sum(result.samples for result in results),
        anomalies=sum(1 for result in results if result.record.get("anomaly_detected")),
    )

    # Filtrul de metrice `AnomalyDetectedWindows` numără liniile de jurnal cu
    # `event = "anomaly_detected"` și `anomaly_detected = true`, deci fiecare fereastră
    # marcată de model se jurnalizează separat: un rând, un contor.
    for result in results:
        if result.record.get("anomaly_detected"):
            log_event(
                logger,
                "anomaly_detected",
                anomaly_detected=True,
                device_id=result.key[0],
                cell_id=result.key[1],
                window=result.key[2],
                score=result.record.get("anomaly_score"),
                model_version=result.record.get("model_version"),
            )

    try:
        _publish_metrics(
            core.metric_data(
                results,
                TELEMETRY_NAMESPACE,
                rates=DEGRADATION_RATES,
                include_features=PUBLISH_FEATURE_METRICS,
                endpoint_failures=len(endpoint_failures),
            )
        )
    except Exception as exc:  # metricile nu au voie să oprească livrarea către S3
        note_error(logger, "metrics_publish_failed", exc)

    # Ferestrele marcate de praguri merg la replay doar când modelul nu a putut decide.
    replay_needed = [
        result
        for result in results
        if result.record.get("model_version") == "thresholds" and result.record.get("anomaly_detected")
    ]
    try:
        _write_replay(replay_needed, reason="threshold_fallback")
    except Exception as exc:  # replay-ul este best-effort
        note_error(logger, "replay_write_failed", exc)

    return core.assemble_response(results, failures, untimed_ids=untimed)
