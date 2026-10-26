"""Contractul de date al telemetriei.

Tabelul din registrul de schemă (Glue) are exact coloanele din :data:`ENRICHED_COLUMNS`.
Firehose convertește JSON în Parquet pe baza acestei scheme, iar conversia **eșuează dacă
înregistrarea are câmpuri în plus**. De aceea Lambda-ul de feature engineering trece fiecare
înregistrare prin :func:`conform` înainte de a o scrie.

Registrul este doar o schemă: nu există job-uri, crawlere sau transformări Glue în proiect.
Feature engineering-ul offline se face cu Data Wrangler (vezi ``ml/feature_engineering/``).
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = "1.1.0"

# Ordinea contează: este ordinea coloanelor din tabelul din catalog.
ENRICHED_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "device_id",
    "cell_id",
    "topic",
    "ts_ms",
    "received_at",
    "window_s",
    "sample_count",
    "machine_state",
    "cycle_id",
    "part_id",
    "spindle_load_mean",
    "spindle_load_p95",
    "spindle_load_std",
    "vibration_rms",
    "vibration_peak",
    "vibration_std",
    "tool_wear_index",
    "coolant_flow_lpm",
    "temp_c_mean",
    "joint_friction_index",
    "joint_friction_index_arm1",
    "joint_friction_index_arm2",
    "anomaly_score",
    "anomaly_detected",
    "model_version",
    "is_synthetic_defect",
)

# Cheile de partiționare sunt extrase de Firehose din răspunsul Lambda, nu din coloane.
PARTITION_KEYS: tuple[str, ...] = ("dt_year", "dt_month", "dt_day", "dt_hour")

# Câmpuri pe care simularea le publică și care nu au loc în tabel.
_DROPPED_BY_DESIGN: frozenset[str] = frozenset(
    {
        "spindle_load_max",
        "vibration_crest",
        "temp_c_max",
        "joint_effort_mean",
        "joint_effort_max",
        "axis_feed_mean",
        "joint_effort",
        "axis_feed",
        "spindle_load",
        "vibration",
        "temp_c",
        "surface_quality",
        "coolant_fault",
        "sample_seq",
        "mqtt_topic",
        "iot_timestamp",
    }
)


def partition_for(ts_ms: int, *, utc: bool = True) -> dict[str, str]:
    """Cheile de partiționare pentru un moment dat, ca șiruri (cerință Firehose)."""
    stamp = time.gmtime(ts_ms / 1000.0) if utc else time.localtime(ts_ms / 1000.0)
    return {
        "dt_year": f"{stamp.tm_year:04d}",
        "dt_month": f"{stamp.tm_mon:02d}",
        "dt_day": f"{stamp.tm_mday:02d}",
        "dt_hour": f"{stamp.tm_hour:02d}",
    }


def conform(record: Mapping[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Păstrează doar coloanele din contract, în ordinea din tabel.

    ``strict`` ridică eroare la câmpuri necunoscute, util în teste. În producție se
    filtrează silent, pentru ca un câmp nou publicat de simulare să nu blocheze livrarea.
    """
    conformed: dict[str, Any] = {}
    for column in ENRICHED_COLUMNS:
        conformed[column] = record.get(column)

    if strict:
        extra = sorted(set(record) - set(ENRICHED_COLUMNS) - set(PARTITION_KEYS))
        if extra:
            raise ValueError(f"câmpuri în afara contractului: {', '.join(extra)}")
    return conformed


def missing_columns(record: Mapping[str, Any]) -> list[str]:
    """Coloanele obligatorii lipsă dintr-o înregistrare."""
    return [column for column in ENRICHED_COLUMNS if column not in record]


def partition_keys_are_strings(partition: Mapping[str, Any]) -> bool:
    """Firehose cere ca valorile cheilor de partiționare să fie șiruri."""
    return all(isinstance(partition.get(key), str) for key in PARTITION_KEYS)


def dropped_fields(record: Mapping[str, Any]) -> list[str]:
    """Câmpurile cunoscute care sunt eliminate la conformare, pentru diagnostic."""
    return sorted(set(record) & _DROPPED_BY_DESIGN)


def conform_many(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [conform(record) for record in records]
