"""Logica pură a Lambda-ului de feature engineering.

Funcția rulează ca transformare Firehose, pe microbatch-ul de 60 de secunde. Primește
eșantioanele brute publicate de simulare și întoarce, pentru fiecare fereastră, o singură
înregistrare agregată, cu cheile de partiționare cerute de Firehose.

Contractul Firehose pentru transformări Lambda (pe scurt):

- fiecare înregistrare din cerere are ``recordId`` și ``data`` (base64);
- răspunsul trebuie să conțină un rezultat pentru **fiecare** ``recordId`` primit;
- pentru o fereastră agregată, o înregistrare primește ``result = "Ok"`` cu datele
  agregate, iar celelalte primesc ``result = "Dropped"``;
- ``metadata.partitionKeys`` sunt folosite de dynamic partitioning.

Modulul nu importă boto3. Apelul către endpoint și publicarea metricilor sunt injectate de
``handler.py`` prin funcții, ca să poată fi testate separat.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from cncdt_ml.features import (
    AnomalyThresholds,
    WindowFeatures,
    compute_window_features,
    enriched_record,
)
from cncdt_ml.schema import ENRICHED_COLUMNS, conform, partition_for
from cncdt_ml.windows import window_key

# Ordinea intrărilor modelului. Trebuie să coincidă cu lista ``FEATURES`` din notebook-ul de
# antrenare (``ml/experiments/notebooks/pm-02-rcf.ipynb``): containerul built-in Random Cut
# Forest primește vectorul pozițional, fără numele coloanelor.
#
# Aici stau doar măsurători. ``tool_wear_index`` și ``joint_friction_index`` sunt `1 - h`, adică
# parametrul intern al simulării, nu o citire de senzor: un model care le primește are răspunsul
# în intrare, iar pe linia reală coloanele nu există. Ele rămân în contract și hrănesc
# dashboard-ul de degradare (`DEGRADATION_ELEMENTS`), dar nu intră în vectorul modelului.
MODEL_FEATURE_ORDER: tuple[str, ...] = (
    "spindle_load_mean",
    "spindle_load_p95",
    "spindle_load_std",
    "vibration_rms",
    "vibration_peak",
    "vibration_std",
    "coolant_flow_lpm",
    "temp_c_mean",
    "axis_feed_mean",
)

# Stările în care mașina așchiază. Doar atunci sarcina axului, vibrația și debitul de răcire
# depind de sănătatea sculei și a pompei; în repaus sunt constante și nu spun nimic despre uzură.
CUTTING_STATES: tuple[str, ...] = ("CUTTING",)
# Sub atâtea eșantioane de așchiere (la 10 Hz, adică sub 3 secunde) fereastra nu are destul
# semnal: modelul nu se cheamă, iar rândul rămâne cu pragurile absolute.
MIN_CUTTING_SAMPLES = 30

CONTENT_TYPE = "application/json"
# Numărul maxim de apeluri către endpoint într-o invocare. Peste acest prag se folosesc
# pragurile absolute: o invocare Lambda are 120 de secunde, iar un microbatch mare nu are
# voie să depășească timpul.
MAX_ENDPOINT_CALLS = 25
# Lot mic la publicarea metricilor, ca o eroare să nu piardă toate metricele invocării.
METRIC_BATCH_SIZE = 20

# Numele metricele publicate în CloudWatch.
METRIC_ANOMALY_SCORE = "AnomalyScore"
METRIC_WINDOWS = "WindowsProcessed"
METRIC_RECORDS = "SamplesAggregated"
METRIC_ENDPOINT_FAILURES = "EndpointFailures"
# Starea de sănătate a celulei, pentru dashboard-ul operațional de fabrică.
METRIC_DEGRADATION_INDEX = "DegradationIndex"
METRIC_RUL_MINUTES = "RULMinutes"

# Elementele urmărite pentru degradare: numele din metrici → câmpul din fereastra agregată.
# Uzura sculei crește doar în timpul așchierii, frecarea articulațiilor doar cât timp robotul
# se mișcă, deci RUL este exprimat în minute de operare continuă: o limită de jos, care nu
# crește când celula stă.
DEGRADATION_ELEMENTS: tuple[tuple[str, str], ...] = (
    ("cnc", "tool_wear_index"),
    ("robot", "joint_friction_index"),
)


class RecordError(ValueError):
    """O înregistrare din lot nu poate fi decodată sau validată."""


@dataclass
class DecodedRecord:
    """O înregistrare Firehose decodată."""

    record_id: str
    payload: dict[str, Any]


@dataclass
class WindowResult:
    """Rezultatul agregării unei ferestre: o înregistrare Ok și restul Dropped."""

    key: tuple[str, str, int]
    record_id: str
    dropped_ids: list[str]
    record: dict[str, Any]
    features: WindowFeatures
    samples: int


def model_view(result: WindowResult) -> dict[str, Any]:
    """Înregistrarea plus măsurătorile care intră în model, dar nu în contractul Firehose.

    ``axis_feed_mean`` este în ``MODEL_FEATURE_ORDER``, însă ``conform`` o scoate din rândul
    scris în Parquet. Endpoint-ul trebuie să o vadă, deci vectorul se compune din vedere, nu
    din ``record``.
    """
    view = dict(result.record)
    view["axis_feed_mean"] = round(result.features.axis_feed_mean, 6)
    return view


def decode_data(data: str | bytes) -> dict[str, Any]:
    """Decodează base64 + JSON. Ridică :class:`RecordError` cu motiv clar."""
    if isinstance(data, str):
        try:
            raw = base64.b64decode(data)
        except (binascii.Error, ValueError) as exc:
            raise RecordError(f"base64 invalid: {exc}") from exc
    else:
        raw = data

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecordError(f"JSON invalid: {exc}") from exc

    if not isinstance(payload, dict):
        raise RecordError("înregistrarea nu este un obiect JSON")
    return payload


def decode_batch(event: Mapping[str, Any]) -> tuple[list[DecodedRecord], list[dict[str, str]]]:
    """Decodifică toate înregistrările. Întoarce (reușite, erori indexate pe recordId)."""
    decoded: list[DecodedRecord] = []
    failures: list[dict[str, str]] = []
    for record in event.get("records", []) or []:
        record_id = str(record.get("recordId", ""))
        try:
            decoded.append(DecodedRecord(record_id=record_id, payload=decode_data(record.get("data", ""))))
        except RecordError as exc:
            failures.append({"recordId": record_id, "error": str(exc)})
    return decoded, failures


def threshold_score(
    features: WindowFeatures,
    thresholds: AnomalyThresholds = AnomalyThresholds(),
) -> float:
    """Scor de rezervă, monoton cu depășirea pragurilor.

    Se folosește doar când modelul nu este disponibil. Raportul cel mai mare față de prag
    este normalizat la 1.2, deci pragul însuși dă aproximativ 0.83, iar o depășire de 20%
    saturează scorul. Valorile sunt documentate în model card, nu ascunse în cod.
    """
    ratios: list[float] = []
    if thresholds.vibration_rms > 0:
        ratios.append(features.vibration_rms / thresholds.vibration_rms)
    if thresholds.spindle_load_mean > 0:
        ratios.append(features.spindle_load_mean / thresholds.spindle_load_mean)
    if thresholds.spindle_load_std > 0:
        ratios.append(features.spindle_load_std / thresholds.spindle_load_std)
    if thresholds.temp_c_max > 0:
        ratios.append(features.temp_c_max / thresholds.temp_c_max)
    if thresholds.crest_factor > 0:
        ratios.append(features.vibration_crest / thresholds.crest_factor)
    # Debitul de răcire scade când apare problema, deci raportul se inversează.
    if 0 < features.coolant_flow_lpm < thresholds.coolant_flow_min:
        ratios.append(thresholds.coolant_flow_min / features.coolant_flow_lpm)

    if not ratios:
        return 0.0
    return round(min(1.0, max(ratios) / 1.2), 4)


def feature_vector(record: Mapping[str, Any]) -> list[float]:
    """Vectorul de intrare al modelului, în ordinea din :data:`MODEL_FEATURE_ORDER`."""
    vector: list[float] = []
    for name in MODEL_FEATURE_ORDER:
        value = record.get(name)
        try:
            vector.append(float(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            vector.append(0.0)
    return vector


def endpoint_body(records: Sequence[Mapping[str, Any]]) -> str:
    """Corpul cererii către containerul Random Cut Forest.

    Containerul built-in nu primește listele plate: fiecare punct are forma
    ``{"data": {"features": {"values": [...]}}}``. O listă plată, deși JSON valid, se
    întoarce cu „unable to evaluate payload provided”.
    """
    instances = [
        {"data": {"features": {"values": feature_vector(record)}}}
        for record in records
    ]
    return json.dumps({"instances": instances})


def parse_endpoint_response(body: str | bytes) -> list[dict[str, Any]]:
    """Citește răspunsul endpoint-ului.

    Sunt acceptate trei forme, ca modelul să poată fi schimbat fără să atingă Lambda-ul:

    - ``{"predictions": [0.12, 0.87]}`` — probabilitatea de anomalie, din ``inference.py``;
    - ``{"predictions": [{"anomaly_score": 0.87, "anomaly_detected": true}]}`` — decizie luată
      în container;
    - ``{"scores": [{"score": 1.87}]}`` — răspunsul containerului built-in Random Cut Forest.

    Scorul RCF nu este o probabilitate: pragul lui vine din antrenare (percentila pe ferestrele
    sănătoase), nu din intervalul 0–1.
    """
    try:
        payload = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecordError(f"răspuns de endpoint invalid: {exc}") from exc

    if isinstance(payload, dict):
        predictions = payload.get("predictions", payload.get("scores"))
    else:
        predictions = None
    if not isinstance(predictions, list):
        raise RecordError("răspunsul nu conține lista 'predictions' ori 'scores'")

    parsed: list[dict[str, Any]] = []
    for item in predictions:
        if isinstance(item, dict):
            parsed.append(
                {
                    "anomaly_score": float(item.get("anomaly_score", item.get("score", 0.0))),
                    "anomaly_detected": bool(
                        item.get("anomaly_detected", item.get("is_anomaly", False))
                    ),
                }
            )
        else:
            score = float(item)
            parsed.append({"anomaly_score": score, "anomaly_detected": False})
    return parsed


PredictionProvider = Callable[[Sequence[dict[str, Any]]], list[dict[str, Any]]]


def enrich_window(
    samples: Sequence[Mapping[str, Any]],
    *,
    window_s: int,
    thresholds: AnomalyThresholds = AnomalyThresholds(),
    context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], WindowFeatures]:
    """Agregă o fereastră și completează scorul de anomalie cu pragurile absolute.

    Statisticile se calculează pe eșantioanele de așchiere, ca în corpusul de antrenare
    (``cncdt_ml.mujoco_corpus.build_windows``). Altfel proporția de repaus din fereastră
    acoperă semnalul de uzură, iar modelul învață altă distribuție decât cea din producție.
    """
    features = compute_window_features(samples, only_states=CUTTING_STATES)
    record = enriched_record(features, raw_samples=samples, context=context, window_s=window_s)
    record["anomaly_score"] = threshold_score(features, thresholds)
    record["anomaly_detected"] = thresholds.is_anomalous(features)
    return record, features


def is_model_worthy(result: "WindowResult") -> bool:
    """Fereastra are destule eșantioane de așchiere ca modelul să aibă ce citi."""
    return int(result.features.extras.get("conditioned_samples", 0)) >= MIN_CUTTING_SAMPLES


def apply_predictions(
    record: dict[str, Any],
    features: WindowFeatures,
    prediction: Mapping[str, Any] | None,
    *,
    thresholds: AnomalyThresholds,
    score_threshold: float,
    model_version: str,
) -> dict[str, Any]:
    """Suprapune decizia modelului peste scorul de rezervă.

    Pragul de decizie rămâne configurabil prin variabila de mediu
    ``ANOMALY_SCORE_THRESHOLD``; o înregistrare este marcată ca anomalie dacă modelul o spune
    sau dacă pragurile fizice sunt depășite.
    """
    updated = dict(record)
    if prediction is not None:
        updated["anomaly_score"] = round(float(prediction.get("anomaly_score", 0.0)), 6)
        updated["anomaly_detected"] = bool(prediction.get("anomaly_detected", False))
        updated["model_version"] = str(prediction.get("model_version", model_version))
    else:
        updated["model_version"] = "thresholds"
    if not updated["anomaly_detected"] and float(updated["anomaly_score"]) >= score_threshold:
        updated["anomaly_detected"] = True
    if thresholds.is_anomalous(features):
        updated["anomaly_detected"] = True
    return updated


def group_by_window(
    records: Sequence[DecodedRecord],
    *,
    window_s: int,
) -> dict[tuple[str, str, int], list[DecodedRecord]]:
    """Grupează înregistrările pe (dispozitiv, celulă, fereastră).

    Cheia include dispozitivul și celula: un singur delivery stream poate primi date de la
    mai multe celule, iar agregarea între ele ar produce caracteristici fără sens.
    """
    groups: dict[tuple[str, str, int], list[DecodedRecord]] = {}
    for decoded in records:
        payload = decoded.payload
        try:
            ts_ms = int(payload.get("ts_ms", 0))
        except (TypeError, ValueError):
            continue
        device = str(payload.get("device_id", ""))
        cell = str(payload.get("cell_id", ""))
        key = (device, cell, window_key(ts_ms, window_s))
        groups.setdefault(key, []).append(decoded)
    return groups


def aggregate(
    records: Sequence[DecodedRecord],
    *,
    window_s: int = 60,
    thresholds: AnomalyThresholds = AnomalyThresholds(),
    prediction_provider: PredictionProvider | None = None,
    score_threshold: float = 0.65,
    model_version: str = "unversioned",
    received_at: str | None = None,
) -> tuple[list[WindowResult], list[str]]:
    """Agregă lotul pe ferestre.

    Întoarce (rezultate, id-uri fără marcaj de timp valid). Pentru fiecare fereastră, prima
    înregistrare cronologic primește datele agregate, restul sunt marcate ca aruncate.
    """
    groups = group_by_window(records, window_s=window_s)
    usable_ids = {decoded.record_id for decoded in records}
    timestamped_ids = {
        decoded.record_id
        for group in groups.values()
        for decoded in group
    }
    untimed = sorted(usable_ids - timestamped_ids)

    stamp = received_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results: list[WindowResult] = []

    for key, group in sorted(groups.items()):
        ordered = sorted(group, key=lambda item: int(item.payload.get("ts_ms", 0)))
        samples = [item.payload for item in ordered]
        record, features = enrich_window(
            samples,
            window_s=window_s,
            thresholds=thresholds,
            context={"received_at": stamp, "schema_version": samples[-1].get("schema_version", "1.0.0")},
        )
        results.append(
            WindowResult(
                key=key,
                record_id=ordered[-1].record_id,
                dropped_ids=[item.record_id for item in ordered[:-1]],
                record=record,
                features=features,
                samples=len(samples),
            )
        )

    # Decizia modelului se cere o singură dată per lot, pentru toate ferestrele. Vectorul
    # primește și măsurători care nu intră în contractul Firehose (`axis_feed_mean`), altfel
    # modelul ar vedea un zero pe o coloană pe care a învățat-o.
    eligible = [result for result in results if is_model_worthy(result)]
    predictions: list[dict[str, Any]] = []
    if prediction_provider is not None and eligible:
        try:
            predictions = prediction_provider([model_view(result) for result in eligible])
        except Exception:  # pragma: no cover - depinde de rețea; handler-ul jurnalizează
            predictions = []
    if prediction_provider is not None and results:
        by_key = {id(result): prediction for result, prediction in zip(eligible, predictions)}
        for result in results:
            result.record = apply_predictions(
                result.record,
                result.features,
                by_key.get(id(result)),
                thresholds=thresholds,
                score_threshold=score_threshold,
                model_version=model_version,
            )
    else:
        for result in results:
            result.record = apply_predictions(
                result.record,
                result.features,
                None,
                thresholds=thresholds,
                score_threshold=score_threshold,
                model_version=model_version,
            )

    return results, untimed


def encode_payload(payload: Mapping[str, Any]) -> str:
    """Base64 + JSON, formatul cerut de Firehose pentru câmpul ``data``."""
    return base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")


def assemble_response(
    results: Sequence[WindowResult],
    failures: Iterable[Mapping[str, str]] = (),
    *,
    untimed_ids: Iterable[str] = (),
    error_ids: Iterable[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """Construiește răspunsul Firehose, cu un rezultat pentru fiecare recordId."""
    records: list[dict[str, Any]] = []

    for failure in failures:
        records.append(
            {
                "recordId": failure.get("recordId", ""),
                "result": "ProcessingFailed",
                "data": encode_payload({"error": failure.get("error", "necunoscut")}),
            }
        )

    for result in results:
        conformed = conform(result.record)
        records.append(
            {
                "recordId": result.record_id,
                "result": "Ok",
                "data": encode_payload(conformed),
                "metadata": {"partitionKeys": partition_for(int(conformed.get("ts_ms") or 0))},
            }
        )
        for dropped_id in result.dropped_ids:
            records.append(
                {
                    "recordId": dropped_id,
                    "result": "Dropped",
                    "data": encode_payload({"reason": "agregat în fereastra " + result.record_id}),
                }
            )

    for record_id in untimed_ids:
        records.append(
            {
                "recordId": record_id,
                "result": "ProcessingFailed",
                "data": encode_payload({"error": "lipsește ts_ms numeric"}),
            }
        )

    for failure in error_ids:
        records.append(
            {
                "recordId": failure.get("recordId", ""),
                "result": "ProcessingFailed",
                "data": encode_payload({"error": failure.get("error", "eroare la agregare")}),
            }
        )

    return {"records": records}


def rul_minutes(index: float, rate_per_second: float, limit: float = 1.0) -> float | None:
    """Minutele de operare continuă până la pragul de avarie al unui element.

    ``rate_per_second`` este panta degradării, aceeași valoare pe care o folosește simularea.
    Fără o pantă pozitivă, estimarea nu are sens și se întoarce ``None``.
    """
    if rate_per_second <= 0:
        return None
    remaining = max(0.0, limit - max(0.0, index))
    return remaining / rate_per_second / 60.0


def degradation_metrics(
    record: Mapping[str, Any],
    dimensions: Sequence[Mapping[str, str]],
    stamp: float,
    rates: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Metricile de degradare ale unei ferestre: indicele și RUL, pe element.

    Dimensiunea ``Element`` separă mașina CNC de brațul robotic, ca alarmele și panourile să
    poată urmări fiecare element în parte.
    """
    if not rates:
        return []
    metrics: list[dict[str, Any]] = []
    for element, field in DEGRADATION_ELEMENTS:
        raw = record.get(field)
        if raw is None:
            continue
        index = float(raw)
        element_dimensions = [*dimensions, {"Name": "Element", "Value": element}]
        metrics.append(
            {
                "MetricName": METRIC_DEGRADATION_INDEX,
                "Value": index,
                "Unit": "None",
                "Dimensions": element_dimensions,
                "Timestamp": stamp,
            }
        )
        minutes = rul_minutes(index, float(rates.get(element, 0.0)))
        if minutes is None:
            continue
        metrics.append(
            {
                "MetricName": METRIC_RUL_MINUTES,
                "Value": minutes,
                "Unit": "None",
                "Dimensions": element_dimensions,
                "Timestamp": stamp,
            }
        )
    return metrics


def metric_data(
    results: Sequence[WindowResult],
    namespace: str,
    *,
    timestamp: float | None = None,
    rates: Mapping[str, float] | None = None,
    include_features: bool = False,
    endpoint_failures: int = 0,
) -> list[dict[str, Any]]:
    """Metricile CloudWatch ale invocării, una per fereastră plus cele globale.

    ``include_features`` publică și caracteristicile brute ale ferestrei. Implicit oprit: nu
    le citește niciun widget sau alarmă, iar fiecare metrică custom costă lunar. Se pornește
    doar pentru analiză exploratorie în CloudWatch.

    ``endpoint_failures`` se publică doar când este pozitiv, ca widget-ul de sănătate a
    pipeline-ului să arate o urmă reală, nu un zero permanent.
    """
    stamp = timestamp or time.time()
    metrics: list[dict[str, Any]] = []

    for result in results:
        record = result.record
        dimensions = [
            {"Name": "DeviceId", "Value": str(record.get("device_id", ""))},
            {"Name": "CellId", "Value": str(record.get("cell_id", ""))},
        ]
        metrics.extend(degradation_metrics(record, dimensions, stamp, rates))
        metrics.append(
            {
                "MetricName": METRIC_ANOMALY_SCORE,
                "Value": float(record.get("anomaly_score", 0.0)),
                "Unit": "None",
                "Dimensions": dimensions,
                "Timestamp": stamp,
            }
        )
        metrics.append(
            {
                "MetricName": METRIC_RECORDS,
                "Value": float(result.samples),
                "Unit": "Count",
                "Dimensions": dimensions,
                "Timestamp": stamp,
            }
        )
        if include_features:
            for name in MODEL_FEATURE_ORDER:
                metrics.append(
                    {
                        "MetricName": name,
                        "Value": float(record.get(name) or 0.0),
                        "Unit": "None",
                        "Dimensions": dimensions,
                        "Timestamp": stamp,
                    }
                )

    metrics.append(
        {
            "MetricName": METRIC_WINDOWS,
            "Value": float(len(results)),
            "Unit": "Count",
            "Dimensions": [{"Name": "Stage", "Value": "aggregate"}],
            "Timestamp": stamp,
        }
    )
    if endpoint_failures:
        metrics.append(
            {
                "MetricName": METRIC_ENDPOINT_FAILURES,
                "Value": float(endpoint_failures),
                "Unit": "Count",
                "Timestamp": stamp,
            }
        )
    return metrics


def batch_metrics(metrics: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Împarte metricele în loturi, ca o eroare să nu piardă toată invocarea."""
    return [
        list(metrics[offset : offset + METRIC_BATCH_SIZE])
        for offset in range(0, len(metrics), METRIC_BATCH_SIZE)
    ]


def replay_payload(result: WindowResult, *, reason: str, region: str | None = None) -> dict[str, Any]:
    """Înregistrarea care se scrie în ``replay/manual/`` când endpoint-ul eșuează.

    Scopul este reluarea: datele nu se pierd, iar un model nou poate fi evaluat pe ele.
    """
    return {
        "reason": reason,
        "region": region,
        "record": conform(result.record),
        "features": result.features.as_record(),
        "sample_key": list(result.key),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def contract_columns() -> tuple[str, ...]:
    """Coloanele acceptate de tabelul din registrul de schemă."""
    return ENRICHED_COLUMNS
