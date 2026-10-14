"""Etichete de sănătate pentru antrenare.

Etichetele sunt derivate din semnalele pe care simularea le publică oricum
(``tool_wear_index``, ``joint_friction_index``, ``anomaly_detected``, ``is_synthetic_defect``,
temperatură, debit de lichid de răcire). Sunt reguli explicite, nu adevăr absolut: un model
antrenat pe ele învață să reproducă regulile acestea, ceea ce este suficient pentru un demo
cap-coadă și pentru validarea pipeline-ului. Când există date reale de la un client,
etichetele se înlocuiesc cu cele din fișele de mentenanță.

Pragurile sunt centralizate aici, ca să apară într-un singur loc în model card.
"""

from __future__ import annotations

from typing import Any, Mapping

# Praguri de severitate pentru uzura de sculă.
WEAR_WATCH = 0.45
WEAR_REPLACE = 0.75

# Praguri pentru frecarea articulațiilor.
FRICTION_WATCH = 0.50
FRICTION_REPAIR = 0.70

# Praguri pentru răcire.
COOLANT_MIN_LPM = 3.0
TEMP_WARN_C = 70.0
TEMP_CRITICAL_C = 78.0

HEALTH_LABELS: tuple[str, ...] = (
    "healthy",
    "tool_wear",
    "joint_friction",
    "thermal",
    "coolant",
    "defect",
)


def _number(record: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    value = record.get(key)
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def severity_from_wear(index: float) -> str:
    """Traduce indicele de uzură într-o decizie de mentenanță."""
    if index >= WEAR_REPLACE:
        return "replace"
    if index >= WEAR_WATCH:
        return "watch"
    return "ok"


def health_label(record: Mapping[str, Any]) -> str:
    """Eticheta dominantă pentru o fereastră.

    Ordinea de verificare este intenționată: un defect explicit de inspecție are prioritate,
    apoi cauzele mecanice, apoi cele termice.
    """
    if bool(record.get("is_synthetic_defect", False)):
        return "defect"
    if _number(record, "tool_wear_index") >= WEAR_REPLACE:
        return "tool_wear"
    if _number(record, "joint_friction_index") >= FRICTION_REPAIR:
        return "joint_friction"
    if _number(record, "temp_c_mean") >= TEMP_CRITICAL_C:
        return "thermal"
    if 0.0 < _number(record, "coolant_flow_lpm", COOLANT_MIN_LPM) < COOLANT_MIN_LPM:
        return "coolant"
    if bool(record.get("anomaly_detected", False)):
        return "tool_wear" if _number(record, "tool_wear_index") >= WEAR_WATCH else "joint_friction"
    return "healthy"


def rul_proxy(record: Mapping[str, Any], wear_rate_per_hour: float = 0.05) -> float:
    """Estimare de viață rămasă, în ore, din rata de uzură.

    Este un proxy, nu o predicție: presupune uzură liniară și o rată constantă. Se folosește
    doar ca etichetă auxiliară de regresie, cu limita documentată în model card.
    """
    if wear_rate_per_hour <= 0:
        return 0.0
    remaining = max(0.0, 1.0 - _number(record, "tool_wear_index"))
    return round(remaining / wear_rate_per_hour, 3)
