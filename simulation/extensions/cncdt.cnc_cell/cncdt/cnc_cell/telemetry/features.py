"""Calculul caracteristicilor pe fereastră.

Aceste funcții sunt pure și folosesc doar biblioteca standard, ca să poată rula atât în
Isaac Sim, cât și în testele locale. Lambda-ul de feature engineering aplică aceleași
definiții pe lotul primit de la Firehose, iar valorile trebuie să coincidă.

Convenție: eșantioanele sunt dicționare cu chei numerice, una per senzor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# Cheile de senzor publicate de simulare.
SENSOR_KEYS = (
    "spindle_load",
    "vibration",
    "temp_c",
    "coolant_flow_lpm",
    "joint_effort",
    "axis_feed",
)


def _values(samples: Sequence[dict[str, Any]], key: str) -> list[float]:
    """Extrage valorile numerice pentru o cheie, ignorând valorile lipsă sau invalide."""
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
    """Deviația standard de eșantion (n-1), ca în statistica uzuală."""
    count = len(values)
    if count < 2:
        return 0.0
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (count - 1)
    return math.sqrt(variance)


def rms(values: Sequence[float]) -> float:
    """Valoarea efectivă. Pentru vibrații este metrica standard de uzură."""
    if not values:
        return 0.0
    return math.sqrt(sum(value * value for value in values) / len(values))


def percentile(values: Sequence[float], fraction: float) -> float:
    """Percentila prin interpolare liniară, echivalentul lui numpy.percentile."""
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
    """Raportul vârf/RMS. Crește la apariția șocurilor mecanice."""
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
        """Dicționarul care se publică și care corespunde coloanelor din catalog."""
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


def compute_window_features(samples: Iterable[dict[str, Any]]) -> WindowFeatures:
    """Calculează caracteristicile unei ferestre de eșantioane.

    Corespunde exact funcției cu același rol din Lambda-ul de feature engineering.
    """
    rows = list(samples)
    features = WindowFeatures(sample_count=len(rows))
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


@dataclass
class AnomalyThresholds:
    """Praguri absolute, folosite când nu există încă un model antrenat.

    Sunt valorile de rezervă: un scor de anomalie din SageMaker le înlocuiește atunci când
    endpoint-ul este disponibil.
    """

    vibration_rms: float = 4.0
    spindle_load_mean: float = 0.85
    spindle_load_std: float = 0.18
    temp_c_max: float = 78.0
    coolant_flow_min: float = 3.0
    crest_factor: float = 4.5

    def violations(self, features: WindowFeatures) -> list[str]:
        """Lista pragurilor încălcate, folosită ca explicație în telemetrie."""
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
