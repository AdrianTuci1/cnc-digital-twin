"""Teste pentru modulele pure ale extensiei Isaac Sim.

Extensia nu poate fi pornită fără Isaac Sim, dar degradarea, semnalele sintetice, calculul
caracteristicilor și publicarea în fișier sunt bibliotecă standard. Aici se verifică exact
aceste părți: dacă trec, erorile rămase sunt legate de scenă, nu de logică.
"""

from __future__ import annotations

import hashlib
import json
import py_compile
from pathlib import Path

import pytest

from cncdt.cnc_cell.config import CellConfig
from cncdt.cnc_cell.degradation.model import DefectInjector, DegradationState
from cncdt.cnc_cell.diverter import DiverterGate, GateState
from cncdt.cnc_cell.sim_control import CellController
from cncdt.cnc_cell.sorting import (
    PartRouter,
    Route,
    decision_for_route,
    decision_for_verdict,
)
from cncdt.cnc_cell.telemetry.publisher import TelemetryPublisher
from cncdt.cnc_cell.telemetry.qa_camera import LABEL_FIELDS, QaSnapshotWriter
from cncdt.cnc_cell.telemetry.sensors import SceneSensors, build_sensor_matrix
from cncdt.cnc_cell.telemetry.synthetic import (
    MACHINE_STATES,
    SyntheticCell,
    deterministic_seed,
    phase_progress,
    state_for_phase,
)


# ---------------------------------------------------------------- degradare
def test_tool_wear_advances_and_multiplies() -> None:
    state = DegradationState()
    tool = state.tool
    assert tool.index == 0.0
    tool.rate_per_second = 0.1
    tool.advance(1.0, cutting=True)
    assert tool.index == pytest.approx(0.1)
    assert tool.vibration_multiplier == pytest.approx(1 + 0.1**1.5)
    assert tool.load_multiplier == pytest.approx(1 + 0.7 * 0.1)
    assert tool.severity == "ok"
    tool.reset()
    assert tool.index == 0.0


def test_degradation_is_clamped_at_one() -> None:
    state = DegradationState()
    state.tool.rate_per_second = 5.0
    state.tool.advance(10.0, cutting=True)
    assert state.tool.index == 1.0
    assert state.tool.surface_quality == 0.0


def test_joint_friction_penalises_speed() -> None:
    state = DegradationState()
    joint = state.joint
    joint.rate_per_second = 0.5
    joint.advance(1.0)
    assert joint.index == pytest.approx(0.5)
    assert joint.speed_penalty == pytest.approx(1 - 0.4 * 0.5)
    assert joint.effort_multiplier == pytest.approx(1 + 0.8 * 0.5)


def test_defect_injector_forced_piece() -> None:
    injector = DefectInjector(probability=0.0, seed=1)
    assert injector.should_reject(1.0) == (False, "in_spec")
    injector.force_next()
    assert injector.should_reject(1.0) == (True, "injected")
    assert injector.forced_next is False
    assert injector.should_reject(1.0) == (False, "in_spec")


def test_defect_injector_flags_worn_surface() -> None:
    injector = DefectInjector(probability=0.0, seed=1)
    assert injector.should_reject(0.4) == (True, "tool_wear")


def test_defect_injector_is_deterministic_for_a_seed() -> None:
    a = DefectInjector(probability=0.5, seed=7)
    b = DefectInjector(probability=0.5, seed=7)
    assert [a.should_reject(1.0) for _ in range(20)] == [b.should_reject(1.0) for _ in range(20)]


def test_degradation_setters_clamp() -> None:
    state = DegradationState()
    state.set_tool_wear(0.7)
    assert state.tool.index == pytest.approx(0.7)
    state.set_tool_wear(5.0)
    assert state.tool.index == 1.0
    state.set_tool_wear(-1.0)
    assert state.tool.index == 0.0
    state.set_joint_friction(0.45)
    assert state.joint.index == pytest.approx(0.45)
    state.set_joint_friction(2.0)
    assert state.joint.index == 1.0


def test_cycle_and_part_identifiers() -> None:
    state = DegradationState()
    assert state.begin_cycle() == "part-000001"
    assert state.cycle_id == "cyc-000001"
    assert state.part_id == "part-000001"
    state.finish_part()
    assert state.begin_cycle() == "part-000002"
    assert state.cycle_id == "cyc-000002"
    assert state.part_id == "part-000002"


def test_snapshot_has_telemetry_keys() -> None:
    state = DegradationState()
    snapshot = state.snapshot()
    assert {
        "tool_wear_index",
        "tool_wear_severity",
        "joint_friction_index",
        "surface_quality",
        "cycle_index",
        "part_index",
        "rejected_total",
        "accepted_total",
        "reject_rate",
        "random_defect_probability",
    } <= set(snapshot)


# ---------------------------------------------------------------- semnale sintetice
def test_deterministic_seed_uses_sha256_not_python_hash() -> None:
    expected = int.from_bytes(hashlib.sha256(b"cnc-cell-01|cycle").digest()[:4], "big")
    assert deterministic_seed("cnc-cell-01", "cycle") == expected
    assert deterministic_seed("a", "b") == deterministic_seed("a", "b")
    assert deterministic_seed("a", "b") != deterministic_seed("b", "a")


def test_phase_progress_and_state_mapping() -> None:
    assert phase_progress(0.0, 45.0) == 0.0
    assert phase_progress(22.5, 45.0) == pytest.approx(0.5)
    assert phase_progress(1.0, 0.0) == 0.0
    assert state_for_phase(0.0) == "IDLE"
    assert state_for_phase(0.10) == "LOADING"
    assert state_for_phase(0.50) == "CUTTING"
    assert state_for_phase(0.80) == "UNLOADING"
    assert state_for_phase(0.90) == "INSPECTING"
    assert state_for_phase(0.99) == "IDLE"


def test_synthetic_sample_has_all_sensor_keys() -> None:
    cell = SyntheticCell(degradation=DegradationState())
    cell.set_state("CUTTING")
    sample = cell.sample(0.1)
    expected = {
        "sample_seq",
        "machine_state",
        "spindle_load",
        "vibration",
        "temp_c",
        "coolant_flow_lpm",
        "joint_effort",
        "axis_feed",
        "tool_wear_index",
        "joint_friction_index",
        "surface_quality",
        "coolant_fault",
    }
    assert expected <= set(sample)
    assert sample["machine_state"] in MACHINE_STATES


def test_synthetic_window_length() -> None:
    cell = SyntheticCell(degradation=DegradationState())
    cell.set_state("CUTTING")
    assert len(cell.sample_window(25, 0.1)) == 25


def test_wear_raises_vibration_on_average() -> None:
    def average_vibration(wear: float) -> float:
        degradation = DegradationState()
        degradation.tool.index = wear
        cell = SyntheticCell(degradation=degradation)
        cell.set_state("CUTTING")
        samples = cell.sample_window(400, 0.1)
        return sum(sample["vibration"] for sample in samples) / len(samples)

    assert average_vibration(0.9) > average_vibration(0.0)


# ---------------------------------------------------------------- senzori
def test_scene_sensors_fall_back_to_synthetic() -> None:
    sensors = SceneSensors(degradation=DegradationState())
    assert sensors.bind() == {}
    assert sensors.using_synthetic is True
    sample = sensors.read(0.1)
    assert sample["cell_id"] == "cell-a"
    assert "spindle_load" in sample


def test_build_sensor_matrix_zero_fills_missing_sensors() -> None:
    values, missing = build_sensor_matrix({"spindle_load": 0.7}, ("spindle_load", "vibration"))
    assert values == {"spindle_load": 0.7, "vibration": 0.0}
    assert missing == ["vibration"]


# ---------------------------------------------------------------- publicare
def test_publisher_writes_json_lines(tmp_path, monkeypatch) -> None:
    target = tmp_path / "telemetry.jsonl"
    monkeypatch.setenv("CNCDT_TELEMETRY_LOG", str(target))
    config = CellConfig()
    publisher = TelemetryPublisher(config=config)
    publisher.start()
    publisher.publish({"ts_ms": 1, "spindle_load": 0.5})
    publisher.publish({"ts_ms": 2, "spindle_load": 0.6})
    publisher.stop(flush=True)

    lines = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert lines[0]["spindle_load"] == 0.5


# ---------------------------------------------------------------- controler
@pytest.fixture()
def controller(tmp_path, monkeypatch):
    monkeypatch.setenv("CNCDT_TELEMETRY_LOG", str(tmp_path / "telemetry.jsonl"))
    monkeypatch.setenv("CNCDT_QA_OUTPUT_DIR", str(tmp_path / "qa"))
    monkeypatch.setenv("CNCDT_AUTOSTART_CYCLE", "false")
    return CellController(config=CellConfig())


def test_controller_publishes_and_reports(controller, tmp_path) -> None:
    controller.start(autostart_cycle=True)
    try:
        assert controller.running
        assert controller.degradation.cycle_id == "cyc-000001"
    finally:
        controller.stop()

    lines = (tmp_path / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines, "controlerul trebuie să publice cel puțin un eșantion"
    assert "spindle_load" in json.loads(lines[0])


def test_controller_operator_actions(controller) -> None:
    controller.inject_tool_wear(0.3)
    assert controller.degradation.tool.index == pytest.approx(0.3)
    controller.replace_tool()
    assert controller.degradation.tool.index == 0.0
    controller.inject_joint_friction(0.4)
    controller.repair_joints()
    assert controller.degradation.joint.index == 0.0
    controller.force_defect()
    assert controller.degradation.defects.forced_next is True
    controller.set_defect_probability(0.25)
    assert controller.degradation.defects.probability == pytest.approx(0.25)


def test_controller_sets_wear_progressively(controller) -> None:
    controller.set_tool_wear(0.65)
    assert controller.degradation.tool.index == pytest.approx(0.65)
    controller.set_joint_friction(0.35)
    assert controller.degradation.joint.index == pytest.approx(0.35)

    # „Repară tot" resetează ambele procese și defectul forțat.
    controller.force_defect()
    controller.repair_all()
    assert controller.degradation.tool.index == 0.0
    assert controller.degradation.joint.index == 0.0
    assert controller.degradation.defects.forced_next is False


def test_controller_accepts_set_wear_commands(controller) -> None:
    controller.on_control_message({"command": "set_tool_wear", "value": 0.6})
    assert controller.degradation.tool.index == pytest.approx(0.6)
    controller.on_control_message({"command": "set_joint_friction", "value": 0.3})
    assert controller.degradation.joint.index == pytest.approx(0.3)
    controller.on_control_message({"command": "repair_all"})
    assert controller.degradation.tool.index == 0.0
    assert controller.degradation.joint.index == 0.0


def test_controller_applies_cloud_verdict(controller) -> None:
    actuations: list[bool] = []
    controller.piston_callback = actuations.append

    # O piesă bună retrage opritorul: altfel ar rămâne ridicat de la rebutul precedent.
    controller.on_control_message({"verdict": "PASS", "part_id": "part-000001"})
    assert controller.stats.accepted_cloud == 1
    assert actuations == [False]
    assert controller.router.last_route == "GOOD"

    controller.on_control_message({"verdict": "REJECT", "part_id": "part-000002", "confidence": 0.9})
    assert controller.stats.rejected_cloud == 1
    assert actuations == [False, True]
    assert controller.stats.piston_actuations == 2
    assert controller.router.last_route == "SCRAP"
    assert controller.router.last_part_id == "part-000002"
    assert controller.router.scrap == 1
    assert controller.router.good == 1


def test_controller_warns_on_unknown_verdict(controller) -> None:
    controller.piston_callback = lambda _reject: None
    controller.on_control_message({"verdict": "MAYBE", "part_id": "part-000003"})
    assert controller.router.last_route == "GOOD"
    assert any("necunoscut" in event for event in controller.recent_events(5))


def test_controller_routes_the_local_prediction_when_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CNCDT_TELEMETRY_LOG", str(tmp_path / "telemetry.jsonl"))
    monkeypatch.setenv("CNCDT_QA_OUTPUT_DIR", str(tmp_path / "qa"))
    monkeypatch.setenv("CNCDT_SORT_ON_LOCAL", "true")
    local = CellController(config=CellConfig())
    local.piston_callback = lambda _reject: None

    local.start_cycle()
    local._finish_part()
    rejected = local.router.last_verdict == "REJECT"
    assert local.router.last_route == ("SCRAP" if rejected else "GOOD")
    assert local.router.history[-1]["source"] == "simulation"


def test_controller_grasps_and_releases(controller) -> None:
    calls: list[bool] = []
    controller.grasp_callback = calls.append

    controller.grasp_part()
    controller.release_part()
    assert calls == [True, False]
    assert controller.stats.grasps == 2

    controller.on_control_message({"command": "grasp_part"})
    assert calls == [True, False, True]


def test_controller_reports_an_unbound_gripper(controller) -> None:
    controller.grasp_part()
    assert controller.stats.grasps == 0
    assert any("griper nelegat" in event for event in controller.recent_events(5))


def test_sorting_route_and_decision() -> None:
    decisions = [decision_for_verdict("part-1", "REJECT", reason="por", source="cloud")]
    assert decisions[0].is_scrap
    assert decisions[0].as_dict() == {
        "part_id": "part-1",
        "verdict": "REJECT",
        "route": "SCRAP",
        "reason": "por",
        "source": "cloud",
    }


def test_sorting_router_without_scene_only_reports() -> None:
    notes: list[str] = []
    router = PartRouter(note=notes.append)
    assert router.route("part-1", "REJECT") is Route.SCRAP
    assert router.status_line() == "bune 0 / rebut 1 (ultima rută: SCRAP)"
    assert router.scrap_share == pytest.approx(1.0)
    assert notes and "part-1" in notes[0]


def test_controller_captures_snapshot(controller, tmp_path) -> None:
    controller.capture_snapshot_now()
    written = list((tmp_path / "qa").rglob("*.json"))
    assert written, "sidecar-ul de metadate trebuie scris"
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["part_id"] == controller.degradation.part_id
    assert "schema_version" in payload


def test_snapshot_records_inspection_camera(tmp_path) -> None:
    writer = QaSnapshotWriter(output_dir=tmp_path / "qa")
    record = writer.capture(
        cycle_id="cyc-000001",
        part_id="part-000001",
        metadata={"device_id": "cnc-cell-01"},
        capture_image=False,
        camera_path="/World/QA/Camera",
    )
    assert record["camera"] == "/World/QA/Camera"

    default = writer.capture(
        cycle_id="cyc-000001",
        part_id="part-000002",
        metadata={},
        capture_image=False,
    )
    assert default["camera"] == "active_viewport"
    assert default["image_captured"] is False


def test_snapshot_writer_refuses_a_label_field(tmp_path) -> None:
    """Sidecar-ul QA pornește inferența, deci un câmp cu răspunsul este refuzat, nu scris.

    Fișierul ajunge în S3 și declanșează Lambda-ul de inspecție: dacă ar duce indiciul de uzură
    ori flagul de defect cerut, modelul ar citi răspunsul în loc să îl deducă din imagine.
    """
    writer = QaSnapshotWriter(output_dir=tmp_path / "qa")
    with pytest.raises(ValueError) as error:
        writer.capture(
            cycle_id="cyc-000001",
            part_id="part-000001",
            metadata={"tool_wear_index": 0.5, "is_synthetic_defect": True},
            capture_image=False,
        )
    assert "eticheta" in str(error.value)
    assert not list((tmp_path / "qa").rglob("*.json")), "cadrul nu se scrie deloc"


def test_snapshot_writer_keeps_the_context_of_the_cell(tmp_path) -> None:
    """Contextul legitim de celulă rămâne în sidecar: dispozitivul, starea, topicul."""
    writer = QaSnapshotWriter(output_dir=tmp_path / "qa")
    record = writer.capture(
        cycle_id="cyc-000001",
        part_id="part-000001",
        metadata={
            "device_id": "cnc-cell-01",
            "cell_id": "cell-01",
            "machine_state": "INSPECTING",
            "telemetry_topic": "cncdt/dev/cnc/cell-01/telemetry",
        },
        capture_image=False,
    )
    assert record["schema_version"] == "1.2.0"
    assert record["device_id"] == "cnc-cell-01"
    assert not set(record) & set(LABEL_FIELDS)


def test_controller_pause_and_resume(controller) -> None:
    controller.pause(True)
    assert controller.paused is True
    assert "suspendat" in controller.state_line()
    controller.pause(False)
    assert controller.paused is False


def test_controller_rejects_unknown_command(controller) -> None:
    controller.on_control_message({"command": "comanda_inexistenta"})
    assert any("necunoscută" in event for event in controller.recent_events(5))


# ---------------------------------------------------------------- poarta de sortare
class FakeClock:
    """Ceas controlat din test, ca așteptarea să fie deterministă."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_gate(**overrides):
    """Poartă legată de liste, ca testul să vadă exact ce s-a aplicat în scenă."""
    clock = overrides.pop("clock", None) or FakeClock()
    applied = []
    holds = []
    notes = []
    gate = DiverterGate(
        route=applied.append,
        hold=holds.append,
        note=notes.append,
        clock=clock,
        **overrides,
    )
    return gate, clock, applied, holds, notes


def test_diverter_gate_holds_the_part_and_the_cloud_verdict_decides() -> None:
    gate, clock, applied, holds, _ = make_gate()

    ticket = gate.arrived("part-000003", "cyc-000001", frame_key="raw/qa-snapshots/x.png")
    assert ticket is not None
    assert holds == [True], "opritorul urcă la sosirea piesei"
    assert gate.state is GateState.WAITING
    assert gate.waiting_for == "part-000003"

    clock.advance(2.5)
    decision = gate.verdict("part-000003", "REJECT", reason="cloud")

    assert decision is not None
    assert decision.route is Route.SCRAP
    assert applied == [decision], "lama primește exact decizia verdictului"
    assert gate.state is GateState.IDLE
    assert gate.cloud_routes == 1
    assert gate.history[-1]["waited_s"] == pytest.approx(2.5)
    assert gate.history[-1]["source"] == "cloud"


def test_diverter_gate_refuses_a_second_part_while_occupied() -> None:
    gate, _, applied, holds, _ = make_gate()

    assert gate.arrived("part-000001", "cyc-000001") is not None
    assert gate.arrived("part-000002", "cyc-000002") is None

    assert gate.waits == 1
    assert holds == [True], "a doua piesă nu ridică din nou opritorul"
    assert applied == []


def test_diverter_gate_ignores_a_late_verdict_for_another_part() -> None:
    gate, _, applied, _, notes = make_gate()
    gate.arrived("part-000001", "cyc-000001")

    assert gate.verdict("part-000009", "REJECT") is None
    assert applied == [], "un verdict întârziat nu mișcă lama sub altă piesă"
    assert gate.stale_verdicts == 1
    assert gate.waiting_for == "part-000001"
    assert any("întârziat" in note for note in notes)


def test_diverter_gate_closes_the_window_with_the_local_prediction() -> None:
    gate, _, applied, _, _ = make_gate(use_local_prediction=True)
    gate.arrived("part-000004", "cyc-000001")

    decision = gate.supply_local("part-000004", rejected=False, reason="in_spec")

    assert decision is not None
    assert decision.route is Route.GOOD
    assert decision.source == "simulation"
    assert decision.reason == "in_spec"
    assert applied == [decision]
    assert gate.local_routes == 1
    assert gate.state is GateState.IDLE


def test_diverter_gate_uses_the_fail_safe_route_when_the_prediction_is_off() -> None:
    gate, _, _applied, _, notes = make_gate(use_local_prediction=False, fail_safe=Route.SCRAP)
    gate.arrived("part-000005", "cyc-000001")

    decision = gate.supply_local("part-000005", rejected=False)

    assert decision is not None
    assert decision.route is Route.SCRAP
    assert decision.source == "fail-safe"
    assert decision.reason == "timeout"
    assert gate.fail_safe_routes == 1
    assert any("rezervă" in note for note in notes)


def test_diverter_gate_fail_safe_can_prefer_continuity() -> None:
    gate, _, applied, _, _ = make_gate(use_local_prediction=False, fail_safe=Route.GOOD)
    gate.arrived("part-000006", "cyc-000001")

    decision = gate.supply_local("part-000006", rejected=True)

    assert decision is not None
    assert decision.route is Route.GOOD
    assert applied == [decision]


def test_diverter_gate_applies_a_verdict_without_a_ticket() -> None:
    gate, _, applied, holds, _ = make_gate()

    decision = gate.verdict("part-000007", "PASS")

    assert decision is not None
    assert decision.route is Route.GOOD
    assert applied == [decision]
    assert holds == [], "fără piesă în poartă nu se ridică opritorul"
    assert gate.cloud_routes == 1


def test_diverter_gate_reset_releases_without_a_route() -> None:
    gate, _, applied, _, notes = make_gate()
    gate.arrived("part-000008", "cyc-000001")

    gate.reset()

    assert applied == []
    assert gate.state is GateState.IDLE
    assert any("fără decizie" in note for note in notes)


def test_decision_for_route_keeps_the_reason_and_source() -> None:
    decision = decision_for_route("part-1", Route.SCRAP, reason="timeout", source="fail-safe")
    assert decision.as_dict() == {
        "part_id": "part-1",
        "verdict": "REJECT",
        "route": "SCRAP",
        "reason": "timeout",
        "source": "fail-safe",
    }


def test_controller_holds_the_part_at_the_blade_until_the_verdict(controller) -> None:
    controller.config.diverter_enabled = True
    actuations: list[bool] = []
    controller.piston_callback = actuations.append

    record = controller._capture_snapshot()

    assert record["camera_role"] == "sorting"
    assert controller.gate.state is GateState.WAITING
    assert controller.gate.waiting_for == controller.degradation.part_id
    assert actuations == [True], "piesa este oprită la lamă la intrarea în inspecție"

    controller.on_control_message(
        {"verdict": "REJECT", "part_id": controller.degradation.part_id, "confidence": 0.9}
    )

    assert controller.gate.state is GateState.IDLE
    assert controller.router.last_route == "SCRAP"
    assert controller._routed_for_part == controller.degradation.part_id

    # Sfârșitul ciclului nu mai sortează a doua oară aceeași piesă.
    controller._finish_part()
    assert controller.router.history[-1]["part_id"] == controller.degradation.part_id
    assert len(controller.router.history) == 1


def test_controller_gate_routes_with_the_fail_safe_when_no_verdict_arrives(controller) -> None:
    controller.config.diverter_enabled = True
    controller.config.sort_on_local_verdict = False
    controller.piston_callback = lambda _reject: None

    controller.start_cycle()
    controller._capture_snapshot()
    controller._finish_part()

    assert controller.gate.fail_safe_routes == 1
    assert controller.router.history[-1]["source"] == "fail-safe"
    assert controller.router.history[-1]["route"] == "SCRAP"


def test_controller_gate_uses_the_local_prediction_when_enabled(controller) -> None:
    controller.config.diverter_enabled = True
    controller.config.sort_on_local_verdict = True
    controller.gate.use_local_prediction = True
    controller.piston_callback = lambda _reject: None

    controller.start_cycle()
    controller._capture_snapshot()
    controller._finish_part()

    assert controller.gate.local_routes == 1
    assert controller.router.history[-1]["source"] == "simulation"


def test_controller_manual_capture_leaves_the_gate_alone(controller) -> None:
    controller.piston_callback = lambda _reject: None
    record = controller.capture_snapshot_now()

    assert record["camera_role"] == "sorting"
    assert controller.gate.state is GateState.IDLE
    assert controller.gate.waits == 0


def test_controller_keeps_the_inspection_role_when_the_gate_is_off(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CNCDT_TELEMETRY_LOG", str(tmp_path / "telemetry.jsonl"))
    monkeypatch.setenv("CNCDT_QA_OUTPUT_DIR", str(tmp_path / "qa"))
    monkeypatch.setenv("CNCDT_DIVERTER_ENABLED", "false")
    plain = CellController(config=CellConfig())
    plain.piston_callback = lambda _reject: None

    record = plain._capture_snapshot()

    assert record["camera_role"] == "inspection"
    assert plain.gate.state is GateState.IDLE
    assert plain.gate.waits == 0


def test_controller_releases_a_stuck_gate(controller) -> None:
    controller.piston_callback = lambda _reject: None
    controller._capture_snapshot()
    assert controller.gate.state is GateState.WAITING

    controller.release_gate()

    assert controller.gate.state is GateState.IDLE
    assert controller.gate.waiting_for == "-"


def test_sorting_camera_is_bound_from_the_scene_contract(controller, monkeypatch) -> None:
    # `bound_paths` întoarce o copie, deci legarea se face pe dicționarul intern, exact cum
    # ar face-o `SceneSensors.bind()` după încărcarea scenei.
    monkeypatch.setattr(
        controller.sensors,
        "_paths",
        {"diverter_camera": "/World/QA/DiverterCamera", "inspection_camera": "/World/QA/Camera"},
    )

    record = controller._capture_snapshot()

    assert record["camera"] == "/World/QA/DiverterCamera"
    assert record["camera_role"] == "sorting"


def test_snapshot_sidecar_satisfies_the_contract(tmp_path) -> None:
    """Cadrul scris pe disc respectă câmpurile obligatorii din schema JSON."""
    schema_path = Path(__file__).resolve().parents[2] / "telemetry" / "schemas" / "cnc.qa.snapshot.v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    writer = QaSnapshotWriter(output_dir=tmp_path / "qa")
    record = writer.capture(
        cycle_id="cyc-000001",
        part_id="part-000001",
        metadata={"device_id": "cnc-cell-01"},
        capture_image=False,
        camera_path="/World/QA/DiverterCamera",
        camera_role="sorting",
    )

    missing = [field for field in schema["required"] if field not in record]
    assert missing == []
    assert record["schema_version"] == schema["properties"]["schema_version"]["const"]
    assert record["camera_role"] in schema["properties"]["camera_role"]["enum"]


def test_sim_scripts_compile(tmp_path) -> None:
    """Scripturile pentru Script Editor nu se pot importa în teste, dar trebuie să compileze."""
    scripts = Path(__file__).resolve().parents[2] / "simulation" / "scripts"
    targets = sorted(scripts.glob("*.py"))
    assert targets, "nu am găsit scripturile de simulare"
    for target in targets:
        py_compile.compile(str(target), doraise=True, cfile=str(tmp_path / f"{target.stem}.pyc"))
