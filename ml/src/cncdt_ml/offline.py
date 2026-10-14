"""Caracteristicile offline, cele care se construiesc peste fereastra de 60 de secondi.

Hot path-ul (Lambda, la fiecare microbatch de 60 s) calculează doar agregările de bază.
Antrenarea are nevoie de mai mult context: aceleași mărimi privite pe mai multe ferestre,
diferențe față de trecut, rapoarte între senzori și etichete.

Acest modul este **echivalentul determinist** al fluxului Data Wrangler din
``ml/feature_engineering/flows/``. Fluxul se construiește vizual, în Studio, pentru explorare
și pentru grafice; modulul de aici rulează în pipeline și în CI, cu aceleași definiții.
Numele coloanelor produse aici sunt cele pe care fluxul trebuie să le emită, iar
``ml/feature_engineering/README.md`` ține tabelul de corespondență.

Doar biblioteca standard: rulează în Lambda, în containerul de procesare și în teste, fără
pandas sau numpy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .features import mean as _mean
from .features import stdev as _std
from .labels import health_label, rul_proxy, severity_from_wear
from .schema import ENRICHED_COLUMNS

# Coloanele de bază peste care se calculează ferestrele glisante și decalajele.
ROLLING_BASE: tuple[str, ...] = (
    "spindle_load_mean",
    "spindle_load_std",
    "vibration_rms",
    "temp_c_mean",
    "coolant_flow_lpm",
    "tool_wear_index",
    "joint_friction_index",
)


@dataclass(frozen=True)
class OfflineFeatureSpec:
    """Rețeta de caracteristici. Versiunea intră în numele prefixului din S3."""

    version: str = "pm-features-v1"
    rolling_windows: tuple[int, ...] = (3, 5, 15)
    lag_steps: tuple[int, ...] = (1, 2)
    group_key: str = "device_id"
    order_key: str = "ts_ms"
    base_columns: tuple[str, ...] = ROLLING_BASE

    def rolling_columns(self) -> list[str]:
        names: list[str] = []
        for size in self.rolling_windows:
            for column in self.base_columns:
                names.extend(
                    [
                        f"{column}_roll{size}_mean",
                        f"{column}_roll{size}_std",
                        f"{column}_roll{size}_delta",
                    ]
                )
        return names

    def lag_columns(self) -> list[str]:
        return [f"{column}_lag{lag}" for lag in self.lag_steps for column in self.base_columns]


DEFAULT_SPEC = OfflineFeatureSpec()

# Praguri absolute, exprimate ca marje față de punctul de referință.
THERMAL_LIMIT_C = 78.0
COOLANT_MIN_LPM = 3.0


def _number(record: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    value = record.get(key)
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def derived_ratios(record: Mapping[str, Any]) -> dict[str, float]:
    """Rapoartele și marjele care nu se văd într-o singură coloană.

    Sunt exact caracteristicile pe care le construiește operatorul „Formula" din Data
    Wrangler, în această ordine.
    """
    load = _number(record, "spindle_load_mean")
    vibration = _number(record, "vibration_rms")
    friction = _number(record, "joint_friction_index")
    temp = _number(record, "temp_c_mean")
    coolant = _number(record, "coolant_flow_lpm", COOLANT_MIN_LPM)
    peak = _number(record, "vibration_peak")
    effective = _number(record, "vibration_rms")

    return {
        # 0.05 la numitor evită împărțirea la zero când senzorul lipsește.
        "load_per_friction": round(load / (friction + 0.05), 6),
        "vibration_per_load": round(vibration / (load + 0.05), 6),
        "crest_ratio": round(peak / effective, 6) if effective > 0 else 0.0,
        "thermal_margin_c": round(THERMAL_LIMIT_C - temp, 6),
        "coolant_margin_lpm": round(coolant - COOLANT_MIN_LPM, 6),
        "wear_x_load": round(_number(record, "tool_wear_index") * load, 6),
        "friction_x_temp": round(friction * temp, 6),
    }


def _rolling(values: Sequence[float], size: int) -> tuple[list[float], list[float]]:
    """Medii și deviații standard pe fereastră glisantă, cu minim 2 puncte."""
    means: list[float] = []
    deviations: list[float] = []
    for index in range(len(values)):
        chunk = list(values[max(0, index - size + 1) : index + 1])
        means.append(round(_mean(chunk), 6))
        deviations.append(round(_std(chunk), 6))
    return means, deviations


def add_rolling_features(
    rows: Sequence[Mapping[str, Any]],
    spec: OfflineFeatureSpec = DEFAULT_SPEC,
) -> list[dict[str, Any]]:
    """Adaugă mediile, deviațiile și diferențele pe ferestre glisante.

    Gruparea se face pe ``spec.group_key``, ordonarea pe ``spec.order_key``. Rândurile
    păstrează ordinea de intrare în rezultat.
    """
    enriched = [dict(row) for row in rows]
    groups: dict[str, list[int]] = {}
    for position, row in enumerate(enriched):
        group = str(row.get(spec.group_key, ""))
        groups.setdefault(group, []).append(position)

    for positions in groups.values():
        ordered = sorted(positions, key=lambda pos: _number(enriched[pos], spec.order_key))
        for column in spec.base_columns:
            values = [_number(enriched[pos], column) for pos in ordered]
            for size in spec.rolling_windows:
                means, deviations = _rolling(values, size)
                for offset, pos in enumerate(ordered):
                    enriched[pos][f"{column}_roll{size}_mean"] = means[offset]
                    enriched[pos][f"{column}_roll{size}_std"] = deviations[offset]
                    enriched[pos][f"{column}_roll{size}_delta"] = round(
                        values[offset] - means[offset], 6
                    )
    return enriched


def add_lag_features(
    rows: Sequence[Mapping[str, Any]],
    spec: OfflineFeatureSpec = DEFAULT_SPEC,
) -> list[dict[str, Any]]:
    """Adaugă valorile anterioare ale coloanelor de bază, pe grup și în ordine temporală."""
    enriched = [dict(row) for row in rows]
    groups: dict[str, list[int]] = {}
    for position, row in enumerate(enriched):
        groups.setdefault(str(row.get(spec.group_key, "")), []).append(position)

    for positions in groups.values():
        ordered = sorted(positions, key=lambda pos: _number(enriched[pos], spec.order_key))
        for column in spec.base_columns:
            values = [_number(enriched[pos], column) for pos in ordered]
            for lag in spec.lag_steps:
                for offset, pos in enumerate(ordered):
                    previous = values[offset - lag] if offset >= lag else values[0]
                    enriched[pos][f"{column}_lag{lag}"] = round(previous, 6)
    return enriched


def add_labels(
    rows: Sequence[Mapping[str, Any]],
    wear_rate_per_hour: float = 0.05,
) -> list[dict[str, Any]]:
    """Adaugă eticheta de sănătate, severitatea și proxy-ul de viață rămasă."""
    enriched: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["health_label"] = health_label(record)
        record["wear_severity"] = severity_from_wear(_number(record, "tool_wear_index"))
        record["is_anomaly"] = bool(record.get("anomaly_detected", False)) or (
            record["health_label"] != "healthy"
        )
        record["rul_hours_proxy"] = rul_proxy(record, wear_rate_per_hour)
        enriched.append(record)
    return enriched


def build_offline_features(
    rows: Sequence[Mapping[str, Any]],
    spec: OfflineFeatureSpec = DEFAULT_SPEC,
    *,
    wear_rate_per_hour: float = 0.05,
) -> list[dict[str, Any]]:
    """Rețeta completă, în ordinea în care o execută și fluxul Data Wrangler.

    1. rapoarte derivate, rând cu rând;
    2. ferestre glisante pe coloanele de bază;
    3. decalaje temporale;
    4. etichete.

    Rezultatul conține coloanele din contract plus caracteristicile derivate. Coloanele
    necunoscute din intrare sunt păstrate: antrenarea are voie să vadă mai mult decât
    contractul, dar scrierea în data lake trece prin :func:`cncdt_ml.schema.conform`.
    """
    enriched: list[dict[str, Any]] = [dict(row) for row in rows]
    for row in enriched:
        row.update(derived_ratios(row))
    enriched = add_rolling_features(enriched, spec)
    enriched = add_lag_features(enriched, spec)
    enriched = add_labels(enriched, wear_rate_per_hour)
    return enriched


def dropped_contract_columns(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Coloanele din contract care lipsesc din primul rând, pentru validarea intrării."""
    if not rows:
        return list(ENRICHED_COLUMNS)
    return [column for column in ENRICHED_COLUMNS if column not in rows[0]]


def feature_columns(spec: OfflineFeatureSpec = DEFAULT_SPEC) -> list[str]:
    """Lista completă a coloanelor rezultate, în ordine, fără cele brute."""
    return sorted(derived_ratios({}).keys()) + spec.rolling_columns() + spec.lag_columns() + [
        "health_label",
        "wear_severity",
        "is_anomaly",
        "rul_hours_proxy",
    ]
