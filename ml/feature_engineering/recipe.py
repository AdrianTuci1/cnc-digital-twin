"""Rețeta de caracteristici, așa cum se construiește în Data Wrangler.

Data Wrangler este mediul de autorat: se lucrează vizual, se profilează coloanele, se văd
histogramele și avertismentele de scurgere de informație (target leakage). Rezultatul este un
fișier ``.flow``, care se comite în ``flows/`` și se rulează ca job de procesare.

Acest modul generează lista de pași și **lista exactă de coloane de ieșire**. Fișierul
``flows/predictive_maintenance.recipe.json`` este generat din el și verificat în teste, deci
nu poate rămâne în urma codului: dacă `cncdt_ml.offline` se schimbă, testul cade și rețeta
trebuie regenerată.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from cncdt_ml.offline import DEFAULT_SPEC, OfflineFeatureSpec, derived_ratios
from cncdt_ml.schema import ENRICHED_COLUMNS

SOURCE_URI_TEMPLATE = "s3://{bucket}/raw/telemetry/"
DESTINATION_URI_TEMPLATE = "s3://{bucket}/features/{model}/{version}/"

# Coloanele de context care se păstrează ca atare în setul de antrenare.
CONTEXT_COLUMNS: tuple[str, ...] = (
    "device_id",
    "cell_id",
    "ts_ms",
    "cycle_id",
    "part_id",
    "machine_state",
)

LABEL_COLUMNS: tuple[str, ...] = ("health_label", "wear_severity", "is_anomaly", "rul_hours_proxy")

# Coloane care nu ajută modelul și se elimină în primul pas de curățare.
TRAINING_DROP_COLUMNS: tuple[str, ...] = ("schema_version", "topic", "received_at")


@dataclass(frozen=True)
class RecipeStep:
    """Un pas din flux, descris în termenii operatorilor din Data Wrangler."""

    step: int
    operator: str
    purpose: str
    output_columns: tuple[str, ...]
    notes: str = ""
    requires_custom_transform: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step": self.step,
            "operator": self.operator,
            "purpose": self.purpose,
            "output_columns": list(self.output_columns),
        }
        if self.notes:
            payload["notes"] = self.notes
        if self.requires_custom_transform:
            payload["requires_custom_transform"] = True
        return payload


def steps(spec: OfflineFeatureSpec = DEFAULT_SPEC) -> list[RecipeStep]:
    """Pașii fluxului, în ordinea în care se construiesc în Studio."""
    ratio_columns = tuple(sorted(derived_ratios({})))
    rolling_columns = tuple(spec.rolling_columns())
    lag_columns = tuple(spec.lag_columns())

    return [
        RecipeStep(
            step=1,
            operator="Import from S3 (Parquet)",
            purpose="Citește telemetria îmbogățită scrisă de Firehose.",
            output_columns=ENRICHED_COLUMNS,
            notes=f"Sursa: {SOURCE_URI_TEMPLATE}. Partițiile dt_* se citesc automat.",
        ),
        RecipeStep(
            step=2,
            operator="Drop columns",
            purpose="Elimină coloanele care nu intră în model.",
            output_columns=tuple(
                column for column in ENRICHED_COLUMNS if column not in TRAINING_DROP_COLUMNS
            ),
            notes="Se elimină: " + ", ".join(TRAINING_DROP_COLUMNS) + ".",
        ),
        RecipeStep(
            step=3,
            operator="Parse / Cast types",
            purpose="Transformă ts_ms în timestamp și elimină rândurile fără marcaj de timp.",
            output_columns=CONTEXT_COLUMNS,
            notes="Fără ts_ms valid, ordinea temporală nu poate fi stabilită.",
        ),
        RecipeStep(
            step=4,
            operator="Sort",
            purpose="Ordonează crescător după device_id și ts_ms.",
            output_columns=CONTEXT_COLUMNS,
        ),
        RecipeStep(
            step=5,
            operator="Formula",
            purpose="Rapoarte și marje între senzori, rând cu rând.",
            output_columns=ratio_columns,
            notes="Formulele sunt în cncdt_ml.offline.derived_ratios.",
        ),
        RecipeStep(
            step=6,
            operator="Custom transform (pandas) sau Processing job",
            purpose="Medii, deviații și diferențe pe ferestre glisante, plus decalaje temporale.",
            output_columns=rolling_columns + lag_columns,
            notes=(
                "Fereastra glisantă cere ordine temporală și stare între rânduri. "
                "Referința este cncdt_ml.offline.add_rolling_features / add_lag_features. "
                "Recomandat: pasul rulează ca al doilea job de procesare al pipeline-ului, "
                "cu build_features.py, ca să fie identic în CI și în producție."
            ),
            requires_custom_transform=True,
        ),
        RecipeStep(
            step=7,
            operator="Encode categorical",
            purpose="Codifică machine_state și device_id pentru modelele care cer valori numerice.",
            output_columns=("machine_state", "device_id"),
            notes="One-hot pentru machine_state, ordinal pentru device_id.",
        ),
        RecipeStep(
            step=8,
            operator="Formula (etichete)",
            purpose="Derivă eticheta de sănătate, severitatea și proxy-ul de viață rămasă.",
            output_columns=LABEL_COLUMNS,
            notes="Regulile sunt în cncdt_ml.labels, cu pragurile la un loc.",
        ),
        RecipeStep(
            step=9,
            operator="Split data",
            purpose="Separă antrenarea de validare, cu păstrarea ordinii temporale.",
            output_columns=CONTEXT_COLUMNS + ratio_columns + rolling_columns + lag_columns + LABEL_COLUMNS,
            notes="80/20 pe ultimele ferestre, fără amestecare aleatoare.",
        ),
        RecipeStep(
            step=10,
            operator="Export to S3",
            purpose="Scrie setul de antrenare în data lake.",
            output_columns=(),
            notes=f"Destinația: {DESTINATION_URI_TEMPLATE.format(bucket='<datalake-bucket>', model='predictive_maintenance', version='<versiune>')}, format Parquet.",
        ),
    ]


def declared_columns(spec: OfflineFeatureSpec = DEFAULT_SPEC) -> list[str]:
    """Coloanele pe care fluxul trebuie să le emită, în ordine stabilă, fără duplicate."""
    seen: list[str] = []
    for step in steps(spec):
        for column in step.output_columns:
            if column not in seen:
                seen.append(column)
    return seen


def build_recipe(
    spec: OfflineFeatureSpec = DEFAULT_SPEC,
    *,
    model: str = "predictive_maintenance",
) -> dict[str, Any]:
    """Descrierea completă a fluxului, serializabilă."""
    return {
        "recipe_version": spec.version,
        "model": model,
        "source": SOURCE_URI_TEMPLATE,
        "destination": DESTINATION_URI_TEMPLATE.format(
            bucket="<datalake-bucket>", model=model, version=spec.version
        ),
        "output_format": "parquet",
        "rolling_windows": list(spec.rolling_windows),
        "lag_steps": list(spec.lag_steps),
        "group_key": spec.group_key,
        "order_key": spec.order_key,
        "base_columns": list(spec.base_columns),
        "declared_columns": declared_columns(spec),
        "steps": [step.as_dict() for step in steps(spec)],
    }


def render_recipe_json(spec: OfflineFeatureSpec = DEFAULT_SPEC, *, model: str = "predictive_maintenance") -> str:
    """JSON stabil, cu chei sortate, ca diff-urile să fie lizibile."""
    return json.dumps(build_recipe(spec, model=model), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def verify_columns(spec: OfflineFeatureSpec = DEFAULT_SPEC) -> list[str]:
    """Coloanele calculate de cod care lipsesc din rețetă. Listă goală înseamnă acord."""
    computed = set(derived_ratios({})) | set(spec.rolling_columns()) | set(spec.lag_columns()) | set(LABEL_COLUMNS)
    return sorted(computed - set(declared_columns(spec)))
