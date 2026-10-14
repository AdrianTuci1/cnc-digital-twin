"""Paritatea dintre extensia Isaac Sim și codul folosit de Lambda/antrenare.

Cele două implementări ale caracteristicilor pe fereastră trebuie să dea valori identice.
Altfel, modelul antrenat pe datele din simulare ar primi altă distribuție în producție, iar
scorul de anomalie ar fi greșit fără ca vreun test să observe.
"""

from __future__ import annotations

import pytest

from cncdt.cnc_cell.telemetry import features as sim_features
from cncdt.cnc_cell.telemetry.synthetic import SyntheticCell

from cncdt_ml import features as shared_features


def test_sensor_keys_match() -> None:
    assert sim_features.SENSOR_KEYS == shared_features.SENSOR_KEYS


def test_threshold_defaults_match() -> None:
    sim_thresholds = sim_features.AnomalyThresholds()
    shared_thresholds = shared_features.AnomalyThresholds()
    assert sim_thresholds.__dict__ == shared_thresholds.__dict__


def _samples(count: int = 600) -> list[dict]:
    from cncdt.cnc_cell.degradation.model import DegradationState

    degradation = DegradationState()
    cell = SyntheticCell(degradation=degradation)
    cell.set_state("CUTTING")
    return cell.sample_window(count, 0.1)


def test_helpers_match_on_generated_samples() -> None:
    samples = _samples()
    for key in shared_features.SENSOR_KEYS:
        values = shared_features._values(samples, key)
        assert sim_features._values(samples, key) == values
        assert sim_features.mean(values) == shared_features.mean(values)
        assert sim_features.stdev(values) == shared_features.stdev(values)
        assert sim_features.rms(values) == shared_features.rms(values)
        assert sim_features.crest_factor(values) == shared_features.crest_factor(values)
        for fraction in (0.05, 0.5, 0.95, 0.99):
            assert sim_features.percentile(values, fraction) == shared_features.percentile(
                values, fraction
            )


def test_window_features_match_on_generated_samples() -> None:
    samples = _samples()
    sim_record = sim_features.compute_window_features(samples).as_record()
    shared_record = shared_features.compute_window_features(samples).as_record()
    # Cheile suplimentare din `extras` nu fac parte din calcul.
    sim_common = {key: value for key, value in sim_record.items() if key in shared_record}
    assert sim_common == shared_record


def test_shared_record_matches_sim_record_field_by_field() -> None:
    from cncdt_ml.schema import ENRICHED_COLUMNS

    samples = _samples(300)
    sim = sim_features.compute_window_features(samples)
    shared = shared_features.compute_window_features(samples)
    shared_record = shared_features.enriched_record(shared, raw_samples=samples, window_s=60)
    assert tuple(shared_record) == ENRICHED_COLUMNS
    for column in (
        "spindle_load_mean",
        "spindle_load_p95",
        "spindle_load_std",
        "vibration_rms",
        "vibration_peak",
        "vibration_std",
        "temp_c_mean",
        "coolant_flow_lpm",
    ):
        assert shared_record[column] == pytest.approx(round(sim.as_record()[column], 6))
