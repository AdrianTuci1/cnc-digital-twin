"""Caracteristici agregate pe fereastră.

Definițiile sunt identice cu cele din extensia Isaac Sim
(``simulation/extensions/cncdt.cnc_cell/cncdt/cnc_cell/telemetry/features.py``). Paritatea
este verificată de ``tests/unit/test_feature_parity.py``: dacă cele două implementări
diverg, antrenarea și producția ar vedea lumi diferite.

Doar biblioteca standard, ca modulul să ruleze și în Lambda (Python 3.12) și în containerul
de procesare, fără numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Collection, Iterable, Mapping, Sequence

from .schema import ENRICHED_COLUMNS, SCHEMA_VERSION

# Cheile de senzor publicate de simulare, în ordinea din contract.
SENSOR_KEYS: tuple[str, ...] = (
    "spindle_load",
    "vibration",
    "temp_c",
    "coolant_flow_lpm",
    "joint_effort",
    "axis_feed",
)


def _values(samples: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    """Valorile numerice finite pentru o cheie, ignorând valorile lipsă sau invalide."""
    out: list[float] = []
    for sample in samples:
        value = sample.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            out.append(number)
    return out


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    """Deviația standard de eșantion (n-1)."""
    count = len(values)
    if count < 2:
        return 0.0
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (count - 1)
    return math.sqrt(variance)


def rms(values: Sequence[float]) -> float:
    """Valoarea efectivă; pentru vibrații este metrica standard de uzură."""
    if not values:
        return 0.0
    return math.sqrt(sum(value * value for value in values) / len(values))


def percentile(values: Sequence[float], fraction: float) -> float:
    """Percentila prin interpolare liniară, echivalentul lui ``numpy.percentile``."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, fraction)) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def crest_factor(values: Sequence[float]) -> float:
    """Raportul vârf/RMS; crește la apariția șocurilor mecanice."""
    effective = rms(values)
    if effective <= 0:
        return 0.0
    return max(abs(value) for value in values) / effective


@dataclass
class WindowFeatures:
    """Caracteristicile unei ferestre de telemetrie."""

    sample_count: int = 0
    spindle_load_mean: float = 0.0
    spindle_load_p95: float = 0.0
    spindle_load_std: float = 0.0
    spindle_load_max: float = 0.0
    vibration_rms: float = 0.0
    vibration_peak: float = 0.0
    vibration_std: float = 0.0
    vibration_crest: float = 0.0
    temp_c_mean: float = 0.0
    temp_c_max: float = 0.0
    coolant_flow_lpm: float = 0.0
    joint_effort_mean: float = 0.0
    joint_effort_max: float = 0.0
    axis_feed_mean: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        """Toate caracteristicile calculate, inclusiv cele care nu intră în contract."""
        record: dict[str, Any] = {
            "sample_count": self.sample_count,
            "spindle_load_mean": round(self.spindle_load_mean, 6),
            "spindle_load_p95": round(self.spindle_load_p95, 6),
            "spindle_load_std": round(self.spindle_load_std, 6),
            "spindle_load_max": round(self.spindle_load_max, 6),
            "vibration_rms": round(self.vibration_rms, 6),
            "vibration_peak": round(self.vibration_peak, 6),
            "vibration_std": round(self.vibration_std, 6),
            "vibration_crest": round(self.vibration_crest, 6),
            "temp_c_mean": round(self.temp_c_mean, 6),
            "temp_c_max": round(self.temp_c_max, 6),
            "coolant_flow_lpm": round(self.coolant_flow_lpm, 6),
            "joint_effort_mean": round(self.joint_effort_mean, 6),
            "joint_effort_max": round(self.joint_effort_max, 6),
            "axis_feed_mean": round(self.axis_feed_mean, 6),
        }
        record.update(self.extras)
        return record


def compute_window_features(
    samples: Iterable[Mapping[str, Any]],
    *,
    only_states: Collection[str] | None = None,
) -> WindowFeatures:
    """Caracteristicile unei ferestre de eșantioane brute.

    Cu ``only_states``, statisticile se calculează doar pe eșantioanele din stările date, iar
    ``sample_count`` rămâne numărul total din fereastră. Motivul: într-o fereastră de 60 s
    intră și repaus, și așchiere, iar proporția de așchiere variază de la o fereastră la alta.
    Media peste toate eșantioanele amestecă cele două regimuri, iar variația de încărcare a
    celulei acoperă semnalul de uzură; pe ferestrele de așchiere, sarcina axului și debitul
    depind direct de sănătatea sculei și a pompei.
    """
    rows = list(samples)
    features = WindowFeatures(sample_count=len(rows))
    if not rows:
        return features
    if only_states is not None:
        allowed = {str(state).upper() for state in only_states}
        rows = [row for row in rows if str(row.get("machine_state", "")).upper() in allowed]
        features.extras["conditioned_samples"] = len(rows)
        if not rows:
            return features

    load = _values(rows, "spindle_load")
    vibration = _values(rows, "vibration")
    temp = _values(rows, "temp_c")
    coolant = _values(rows, "coolant_flow_lpm")
    effort = _values(rows, "joint_effort")
    feed = _values(rows, "axis_feed")

    features.spindle_load_mean = mean(load)
    features.spindle_load_p95 = percentile(load, 0.95)
    features.spindle_load_std = stdev(load)
    features.spindle_load_max = max(load) if load else 0.0

    features.vibration_rms = rms(vibration)
    features.vibration_peak = max(abs(value) for value in vibration) if vibration else 0.0
    features.vibration_std = stdev(vibration)
    features.vibration_crest = crest_factor(vibration)

    features.temp_c_mean = mean(temp)
    features.temp_c_max = max(temp) if temp else 0.0

    features.coolant_flow_lpm = mean(coolant)
    features.joint_effort_mean = mean(effort)
    features.joint_effort_max = max(effort) if effort else 0.0
    features.axis_feed_mean = mean(feed)

    return features


def enriched_record(
    features: WindowFeatures,
    *,
    raw_samples: Sequence[Mapping[str, Any]] = (),
    context: Mapping[str, Any] | None = None,
    window_s: int = 60,
) -> dict[str, Any]:
    """Înregistrarea din contract, gata de scris de Firehose.

    Câmpurile care nu apar în :data:`cncdt_ml.schema.ENRICHED_COLUMNS` sunt eliminate.
    Valorile de context (identitate, stare, indici de degradare) se iau din ``raw_samples``
    dacă nu sunt date explicit: simularea le publică pe fiecare eșantion.
    """
    context = dict(context or {})
    latest = raw_samples[-1] if raw_samples else {}

    def pick(name: str, default: Any = None) -> Any:
        if name in context:
            return context[name]
        if name in latest:
            return latest[name]
        return default

    record: dict[str, Any] = {
        "schema_version": pick("schema_version", SCHEMA_VERSION),
        "device_id": pick("device_id", ""),
        "cell_id": pick("cell_id", ""),
        "topic": pick("topic", ""),
        "ts_ms": pick("ts_ms", 0),
        "received_at": pick("received_at", ""),
        "window_s": int(window_s),
        "sample_count": features.sample_count,
        "machine_state": pick("machine_state", ""),
        "cycle_id": pick("cycle_id", ""),
        "part_id": pick("part_id", ""),
        "spindle_load_mean": round(features.spindle_load_mean, 6),
        "spindle_load_p95": round(features.spindle_load_p95, 6),
        "spindle_load_std": round(features.spindle_load_std, 6),
        "vibration_rms": round(features.vibration_rms, 6),
        "vibration_peak": round(features.vibration_peak, 6),
        "vibration_std": round(features.vibration_std, 6),
        "tool_wear_index": pick("tool_wear_index", 0.0),
        "coolant_flow_lpm": round(features.coolant_flow_lpm, 6),
        "temp_c_mean": round(features.temp_c_mean, 6),
        "joint_friction_index": pick("joint_friction_index", 0.0),
        "anomaly_score": pick("anomaly_score", 0.0),
        "anomaly_detected": bool(pick("anomaly_detected", False)),
        "model_version": pick("model_version", "unversioned"),
        "is_synthetic_defect": bool(pick("is_synthetic_defect", False)),
    }
    return {column: record[column] for column in ENRICHED_COLUMNS}


@dataclass
class AnomalyThresholds:
    """Praguri absolute, folosite când modelul de anomalii nu este disponibil."""

    vibration_rms: float = 4.0
    spindle_load_mean: float = 0.85
    spindle_load_std: float = 0.18
    temp_c_max: float = 78.0
    coolant_flow_min: float = 3.0
    crest_factor: float = 4.5

    def violations(self, features: WindowFeatures) -> list[str]:
        broken: list[str] = []
        if features.vibration_rms > self.vibration_rms:
            broken.append("vibration_rms")
        if features.spindle_load_mean > self.spindle_load_mean:
            broken.append("spindle_load_mean")
        if features.spindle_load_std > self.spindle_load_std:
            broken.append("spindle_load_std")
        if features.temp_c_max > self.temp_c_max:
            broken.append("temp_c_max")
        if 0 < features.coolant_flow_lpm < self.coolant_flow_min:
            broken.append("coolant_flow_lpm")
        if features.vibration_crest > self.crest_factor:
            broken.append("vibration_crest")
        return broken

    def is_anomalous(self, features: WindowFeatures) -> bool:
        return bool(self.violations(features))
