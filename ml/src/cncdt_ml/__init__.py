"""Cod comun pentru calculul caracteristicilor, offline și pe hot path.

Acest pachet este sursa de adevăr pentru două lucruri:

1. **contractul de date** — :mod:`cncdt_ml.schema` fixează coloanele tabelului de telemetrie
   din registrul de schemă și filtrează câmpurile suplimentare înainte de scriere;
2. **caracteristicile pe fereastră** — :mod:`cncdt_ml.features` calculează aceleași valori
   ca extensia Isaac Sim, verificat prin testul de paritate.

Codul este scris cu biblioteca standard. Lambda-ul îl importă direct (directorul
``ml/src/cncdt_ml`` intră în arhiva funcției ca strat comun), iar antrenarea îl folosește
pentru a construi setul de date cu aceleași definiții ca producția.
"""

from .features import (
    SENSOR_KEYS,
    AnomalyThresholds,
    WindowFeatures,
    compute_window_features,
    crest_factor,
    mean,
    percentile,
    rms,
    stdev,
)
from .labels import health_label, rul_proxy, severity_from_wear
from .offline import DEFAULT_SPEC, OfflineFeatureSpec, build_offline_features
from .schema import (
    ENRICHED_COLUMNS,
    PARTITION_KEYS,
    SCHEMA_VERSION,
    conform,
    partition_for,
)
from .windows import group_windows, window_bounds, window_key

__all__ = [
    "DEFAULT_SPEC",
    "ENRICHED_COLUMNS",
    "PARTITION_KEYS",
    "SCHEMA_VERSION",
    "SENSOR_KEYS",
    "AnomalyThresholds",
    "OfflineFeatureSpec",
    "WindowFeatures",
    "build_offline_features",
    "compute_window_features",
    "conform",
    "crest_factor",
    "group_windows",
    "health_label",
    "mean",
    "partition_for",
    "percentile",
    "rms",
    "rul_proxy",
    "severity_from_wear",
    "stdev",
    "window_bounds",
    "window_key",
]
