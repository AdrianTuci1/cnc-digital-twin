"""Controlul ciclului de producție și legătura cu pipeline-ul de date.

:class:`CellController` este nodul central al extensiei. Ține starea de degradare, citește
senzorii, publică telemetria, captează cadrele de inspecție și reacționează la comenzile
venite din cloud.

Bucla rulează într-un fir de execuție separat, la 10 Hz implicit. Aceasta este frecvența la
care sunt generate eșantioanele; agregarea pe fereastră se face în Lambda-ul de feature
engineering, care primește lotul de la Firehose la fiecare 60 de secunde.

Fluxul unei piese:

1. ciclul avansează prin stările LOADING → CUTTING → UNLOADING → INSPECTING;
2. la intrarea în INSPECTING, piesa este oprită la lamă și se capturează cadrul cu camera
   de la zona lamei/piston; cadrul pleacă spre endpoint-ul de viziune prin S3;
3. verdictul care decide ruta vine din cloud, pe topicul ``qa-verdict``; dacă nu sosește
   până la sfârșitul ciclului, poarta folosește previziunea locală ori ruta de rezervă
   (vezi :mod:`cncdt.cnc_cell.diverter`);
4. ruta (piese bune ori rebut) este aplicată de :class:`~cncdt.cnc_cell.sorting.PartRouter`,
   care ridică ori retrage opritorul și selectează traseul de role al joncțiunii, dacă
   există; evenimentul este jurnalizat și numărat.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import CellConfig
from .degradation.model import DefectInjector, DegradationState
from .diverter import DiverterGate
from .sorting import PartRouter, Route, decision_for_verdict, is_known_verdict
from .telemetry.publisher import TelemetryPublisher, log
from .telemetry.qa_camera import QaSnapshotWriter
from .telemetry.sensors import SceneSensors
from .telemetry.synthetic import SyntheticCell, phase_progress, state_for_phase

# Durata unui ciclu complet, în secunde. Se poate scurta pentru demonstrații.
CYCLE_SECONDS = 45.0
SAMPLE_INTERVAL_SECONDS = 0.1
# Seed fix pentru injectorul de defecte: aceeași sesiune de demonstrație produce
# aceleași piese, ceea ce face depanarea reproductibilă.
DEMO_SEED = 20260101


@dataclass
class CycleStats:
    """Contoarele afișate în panoul de control."""

    cycles: int = 0
    parts_inspected: int = 0
    rejected_cloud: int = 0
    accepted_cloud: int = 0
    verdicts_received: int = 0
    commands_received: int = 0
    piston_actuations: int = 0
    grasps: int = 0
    last_verdict: str = "-"
    last_verdict_reason: str = "-"
    last_verdict_source: str = "-"

    @property
    def reject_rate(self) -> float:
        total = self.rejected_cloud + self.accepted_cloud
        return self.rejected_cloud / total if total else 0.0


@dataclass
class CellController:
    """Orchestrează ciclul de producție și legătura cu AWS."""

    config: CellConfig
    degradation: DegradationState = field(default_factory=DegradationState)
    stats: CycleStats = field(default_factory=CycleStats)

    # Apelat cu (rejected) când opritorul de rejecție trebuie ridicat sau retras. Legat de
    # scena USD.
    piston_callback: Callable[[bool], None] | None = None
    # Apelat cu (closed) când griperul trebuie închis (piesa este prinsă) ori deschis.
    grasp_callback: Callable[[bool], None] | None = None

    # Decide ruta fiecărei piese și o aplică în scenă. Se creează în ``__post_init__``,
    # pentru că are nevoie de metoda legată ``actuate_piston``.
    router: PartRouter = field(init=False)

    # Poarta de la zona lamei/piston: oprește piesa, așteaptă verdictul, aplică ruta.
    gate: DiverterGate = field(init=False)

    sensors: SceneSensors = field(init=False)
    publisher: TelemetryPublisher = field(init=False)
    snapshots: QaSnapshotWriter = field(init=False)
    synthetic: SyntheticCell = field(init=False)

    _thread: threading.Thread | None = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _events: list[str] = field(default_factory=list, init=False)
    _events_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _cycle_started_at: float = field(default=0.0, init=False)
    _snapshot_taken_for_part: str = field(default="", init=False)
    _cloud_verdict_for_part: str = field(default="", init=False)
    # Ultima piesă căreia i-a fost aplicată o rută. Fără acest guard, o piesă sortată de
    # verdictul din cloud ar fi sortată a doua oară de previziunea locală, la finalul
    # ciclului.
    _routed_for_part: str = field(default="", init=False)
    _last_sample: dict[str, Any] = field(default_factory=dict, init=False)
    _paused: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.router = PartRouter(gate=self.actuate_piston, note=self.note)
        self.gate = DiverterGate(
            route=self.router.apply,
            hold=self.actuate_piston,
            note=self.note,
            use_local_prediction=self.config.sort_on_local_verdict,
            fail_safe=Route(self.config.diverter_fail_safe_route),
        )
        self.degradation.tool.rate_per_second = self.config.tool_wear_rate
        self.degradation.joint.rate_per_second = self.config.joint_friction_rate
        # Seed fix: aceeași sesiune produce aceleași semnale, ceea ce face depanarea reproductibilă.
        self.degradation.defects = DefectInjector(
            probability=self.config.random_defect_probability,
            seed=DEMO_SEED,
        )

        self.synthetic = SyntheticCell(degradation=self.degradation)
        self.sensors = SceneSensors(
            degradation=self.degradation,
            synthetic=self.synthetic,
            cell_id=self.config.cell_id,
            device_id=self.config.device_id,
        )
        self.publisher = TelemetryPublisher(
            config=self.config,
            control_callback=self.on_control_message,
        )
        self.snapshots = QaSnapshotWriter(
            output_dir=Path(self.config.qa_output_dir),
            bucket=self.config.s3_bucket,
            prefix=self.config.s3_prefix,
            aws_region=self.config.aws_region,
        )

    # ------------------------------------------------------------ jurnal intern
    def note(self, message: str) -> None:
        stamped = f"{time.strftime('%H:%M:%S')} {message}"
        with self._events_lock:
            self._events.append(stamped)
            if len(self._events) > 200:
                del self._events[:-200]
        log(message)

    def recent_events(self, count: int = 10) -> list[str]:
        with self._events_lock:
            return list(self._events[-count:])

    # ------------------------------------------------------------ ciclu de viață
    def bind_scene(self) -> dict[str, str]:
        """Leagă senzorii de scenă după ce aceasta este încărcată."""
        paths = self.sensors.bind()
        if paths:
            self.note(f"senzori legați: {', '.join(sorted(paths))}")
        return paths

    def start(self, autostart_cycle: bool | None = None) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self.publisher.start()
        if autostart_cycle is None:
            autostart_cycle = self.config.autostart_cycle
        if autostart_cycle:
            self.start_cycle()
        self._thread = threading.Thread(target=self._run, name="cncdt-controller", daemon=True)
        self._thread.start()
        self.note("controler pornit")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self.publisher.stop()
        self.note("controler oprit")

    @property
    def running(self) -> bool:
        return self._thread is not None

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self, paused: bool = True) -> None:
        self._paused = paused
        self.note("ciclu suspendat" if paused else "ciclu reluat")

    def start_cycle(self) -> None:
        """Începe un ciclu nou."""
        self._cycle_started_at = time.monotonic()
        cycle_id = self.degradation.begin_cycle()
        self.stats.cycles = self.degradation.cycle_index
        self._snapshot_taken_for_part = ""
        self._cloud_verdict_for_part = ""
        self._routed_for_part = ""
        self.gate.reset()
        self.synthetic.set_state("LOADING")
        self.note(f"ciclu nou {cycle_id} pentru piesa {self.degradation.part_id}")

    # ------------------------------------------------------------ bucla principală
    def _run(self) -> None:
        last = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            dt = min(0.5, now - last)
            last = now

            if self._paused:
                time.sleep(SAMPLE_INTERVAL_SECONDS)
                continue

            try:
                self._tick(dt, now)
            except Exception as exc:  # pragma: no cover - bucla nu trebuie să moară
                self.note(f"eroare în buclă: {exc}")
                time.sleep(0.5)

            time.sleep(SAMPLE_INTERVAL_SECONDS)

    def _tick(self, dt: float, now: float) -> None:
        if self._cycle_started_at <= 0.0:
            # Fără ciclu pornit explicit, înaintăm lent prin stări, ca demonstrația să arate viu.
            progress = phase_progress(now, CYCLE_SECONDS * 4)
            self.synthetic.set_state(state_for_phase(progress))
        else:
            elapsed = now - self._cycle_started_at
            progress = phase_progress(elapsed, CYCLE_SECONDS)
            self.synthetic.set_state(state_for_phase(progress))

            # La intrarea în INSPECTING se capturează cadrul piesei.
            if (
                self.synthetic.state == "INSPECTING"
                and self._snapshot_taken_for_part != self.degradation.part_id
            ):
                self._capture_snapshot()

            # Ciclul s-a încheiat: se închide piesa și se pornește următoarea.
            if elapsed >= CYCLE_SECONDS:
                self._finish_part()
                self.start_cycle()

        sample = self.sensors.read(dt)
        sample.update(
            {
                "ts_ms": int(time.time() * 1000),
                "schema_version": "1.0.0",
                "cycle_id": self.degradation.cycle_id,
                "part_id": self.degradation.part_id,
                "topic": self.config.telemetry_topic,
            }
        )
        self._last_sample = sample
        self.publisher.publish(sample)

    def _finish_part(self) -> None:
        """Închide piesa curentă și pregătește rejecția, dacă este cazul."""
        rejected, reason = self.degradation.finish_part()
        self.stats.parts_inspected = self.degradation.part_index
        state = self.degradation.snapshot()
        self.note(
            f"piesa {self.degradation.part_id}: verdict local "
            f"{'REJECT' if rejected else 'PASS'} ({reason}), "
            f"uzură {state['tool_wear_index']}, frecare {state['joint_friction_index']}"
        )
        if rejected:
            # Verdictul autoritar vine din cloud. Aici doar raportăm previziunea locală,
            # publicată deja în telemetrie prin câmpurile de degradare.
            self.stats.last_verdict = "REJECT (local)"
            self.stats.last_verdict_reason = reason
            self.stats.last_verdict_source = "simulation"

        # Cu poarta pornită, piesa este încă oprită la lamă: fereastra se închide aici, cu
        # previziunea locală ori cu ruta de rezervă. Altfel, ruta rămâne pe seama
        # previziunii locale, ca înainte.
        if self.config.diverter_enabled and self.gate.supply_local(
            self.degradation.part_id, rejected, reason
        ) is not None:
            return
        self._route_local_prediction(rejected, reason)

    def _route_local_prediction(self, rejected: bool, reason: str) -> None:
        """Aplică ruta după previziunea locală, când cloudul nu a răspuns încă.

        Implicit oprit (``CNCDT_SORT_ON_LOCAL=false``): verdictul care mișcă opritorul vine
        din cloud, pe topicul ``qa-verdict``. Cu opțiunea pornită, demonstrația sortează și
        fără cloud, iar sursa deciziei rămâne scrisă în jurnal (``simulation``).
        """
        if self._cloud_verdict_for_part or self._routed_for_part == self.degradation.part_id:
            return
        if not self.config.sort_on_local_verdict:
            return
        self.router.route(
            self.degradation.part_id,
            "REJECT" if rejected else "PASS",
            reason=reason,
            source="simulation",
        )
        self._routed_for_part = self.degradation.part_id

    def _capture_snapshot(self, hold_at_gate: bool = True) -> dict[str, Any]:
        """Capturează cadrul piesei curente și, la cerere, oprește piesa la lamă.

        Cu poarta pornită, cadrul este luat de camera de la zona lamei/piston, pentru că
        verdictul lui decide ruta. Camera de inspecție de la ieșirea din mașină rămâne
        rezerva, când scena nu are încă a doua cameră.
        """
        self._snapshot_taken_for_part = self.degradation.part_id
        metadata = {
            "device_id": self.config.device_id,
            "cell_id": self.config.cell_id,
            "machine_state": self.synthetic.state,
            "telemetry_topic": self.config.telemetry_topic,
        }
        camera_key = "diverter_camera" if self.config.diverter_enabled else "inspection_camera"
        camera_path = self.sensors.bound_paths.get(camera_key) or self.sensors.bound_paths.get(
            "inspection_camera"
        )
        record = self.snapshots.capture(
            cycle_id=self.degradation.cycle_id,
            part_id=self.degradation.part_id,
            metadata=metadata,
            camera_path=camera_path,
            camera_role="sorting" if self.config.diverter_enabled else "inspection",
        )
        image_state = "cu imagine" if record["image_captured"] else "doar metadate"
        self.note(
            f"cadru QA pentru {self.degradation.part_id} ({image_state}, "
            f"rol {record['camera_role']})"
        )

        if hold_at_gate and self.config.diverter_enabled:
            self.gate.arrived(
                part_id=self.degradation.part_id,
                cycle_id=self.degradation.cycle_id,
                frame_key=str(record.get("image_key", "")),
                image_captured=bool(record["image_captured"]),
                camera=str(record["camera"]),
            )
        return record

    # ------------------------------------------------------------ comenzi din cloud
    def on_control_message(self, payload: dict[str, Any]) -> None:
        """Tratează mesajele primite pe topicurile de control.

        Formate acceptate::

            {"command": "inject_tool_wear", "amount": 0.2}
            {"command": "inject_joint_friction", "amount": 0.2}
            {"command": "set_tool_wear", "value": 0.6}
            {"command": "set_joint_friction", "value": 0.4}
            {"command": "force_defect"}
            {"command": "replace_tool"} | {"command": "repair_joints"} | {"command": "repair_all"}
            {"command": "pause"} | {"command": "resume"}
            {"command": "start_cycle"} | {"command": "stop_cycle"}
            {"command": "grasp_part"} | {"command": "release_part"}
            {"command": "set_defect_probability", "value": 0.3}
            {"verdict": "REJECT", "part_id": "part-000012", "confidence": 0.93}

        Verdictul nu oprește doar ciclul: el decide ruta piesei, prin
        :class:`~cncdt.cnc_cell.sorting.PartRouter`.
        """
        self.stats.commands_received += 1

        verdict = payload.get("verdict")
        if verdict is not None:
            self._handle_verdict(payload)
            return

        command = str(payload.get("command", "")).lower()
        if not command:
            self.note(f"mesaj de control fără comandă: {payload}")
            return

        if command == "inject_tool_wear":
            amount = float(payload.get("amount", 0.25))
            self.inject_tool_wear(amount)
        elif command == "inject_joint_friction":
            amount = float(payload.get("amount", 0.25))
            self.inject_joint_friction(amount)
        elif command == "set_tool_wear":
            self.set_tool_wear(float(payload.get("value", 0.0)))
        elif command == "set_joint_friction":
            self.set_joint_friction(float(payload.get("value", 0.0)))
        elif command == "force_defect":
            self.force_defect()
        elif command == "replace_tool":
            self.replace_tool()
        elif command == "repair_joints":
            self.repair_joints()
        elif command == "repair_all":
            self.repair_all()
        elif command == "set_defect_probability":
            self.set_defect_probability(float(payload.get("value", 0.05)))
        elif command == "pause":
            self.pause(True)
        elif command == "resume":
            self.pause(False)
        elif command == "start_cycle":
            self.start_cycle()
        elif command == "stop_cycle":
            self._cycle_started_at = 0.0
            self.gate.reset()
            self.synthetic.set_state("IDLE")
            self.note("ciclu oprit")
        elif command == "grasp_part":
            self.grasp_part()
        elif command == "release_part":
            self.release_part()
        elif command == "reset":
            self.degradation.reset_all()
            self.note("degradare resetată")
        else:
            self.note(f"comandă necunoscută: {command}")

    def _handle_verdict(self, payload: dict[str, Any]) -> None:
        """Aplică verdictul de inspecție venit din cloud."""
        verdict = str(payload.get("verdict", "")).upper()
        part_id = str(payload.get("part_id", self.degradation.part_id))
        confidence = payload.get("confidence")
        reason = str(payload.get("reason", "cloud"))

        self.stats.verdicts_received += 1
        self.stats.last_verdict = verdict
        self.stats.last_verdict_reason = reason
        self.stats.last_verdict_source = "cloud"
        self._cloud_verdict_for_part = verdict

        confidence_text = f", încredere {confidence}" if confidence is not None else ""
        self.note(f"verdict cloud pentru {part_id}: {verdict} ({reason}{confidence_text})")
        if not is_known_verdict(verdict):
            self.note(
                f"verdict necunoscut „{verdict}”; piesa {part_id} urmează ruta pieselor bune"
            )

        decision = decision_for_verdict(part_id, verdict, reason=reason, source="cloud")
        if decision.is_scrap:
            self.stats.rejected_cloud += 1
        else:
            self.stats.accepted_cloud += 1

        if self.config.diverter_enabled:
            # Poarta ține piesa oprită la lamă: verdictul închide așteptarea. Un verdict
            # întârziat, pentru o piesă care a plecat deja, nu mișcă lama.
            if self.gate.verdict(part_id, verdict, reason=reason, source="cloud") is None:
                self.note(f"verdictul pentru {part_id} a sosit după ce piesa a plecat")
                return
        else:
            self.router.apply(decision)
        self._routed_for_part = part_id

    def actuate_piston(self, reject: bool) -> None:
        """Ridică ori retrage opritorul de rejecție, dacă este legat de scenă."""
        if self.piston_callback is None:
            self.note("piston nelegat de scenă; rejecția rămâne doar raportată")
            return
        try:
            self.piston_callback(reject)
            self.stats.piston_actuations += 1
            self.note(f"piston acționat: {'rejecție' if reject else 'retragere'}")
        except Exception as exc:
            self.note(f"acționarea pistonului a eșuat: {exc}")

    def grasp_part(self) -> None:
        """Închide griperul: piesa este prinsă de efectorul final."""
        self._set_gripper(True)

    def release_part(self) -> None:
        """Deschide griperul: piesa este lăsată liberă."""
        self._set_gripper(False)

    def _set_gripper(self, closed: bool) -> None:
        if self.grasp_callback is None:
            self.note("griper nelegat de scenă; prinderea rămâne doar raportată")
            return
        try:
            self.grasp_callback(closed)
            self.stats.grasps += 1
            self.note("griper închis: piesa este prinsă" if closed else "griper deschis: piesa este liberă")
        except Exception as exc:
            self.note(f"acționarea griperului a eșuat: {exc}")

    # ------------------------------------------------------------ acțiuni de operator
    def inject_tool_wear(self, amount: float = 0.25) -> None:
        self.degradation.inject_tool_wear(amount)
        applied = self.sensors.apply_degradation_to_scene()
        self.note(
            f"uzură de sculă injectată (+{amount}). Uzură curentă: "
            f"{self.degradation.tool.index:.3f}. Aplicat în scenă: {applied or 'nimic'}"
        )

    def inject_joint_friction(self, amount: float = 0.25) -> None:
        self.degradation.inject_joint_friction(amount)
        applied = self.sensors.apply_degradation_to_scene()
        self.note(
            f"frecare de articulație injectată (+{amount}). Frecare curentă: "
            f"{self.degradation.joint.index:.3f}. Aplicat în scenă: {applied or 'nimic'}"
        )

    def set_tool_wear(self, value: float) -> None:
        """Setează direct uzura sculei; folosită de slider-ele din panou și de comenzi."""
        self.degradation.set_tool_wear(value)
        applied = self.sensors.apply_degradation_to_scene()
        self.note(
            f"uzură de sculă setată la {self.degradation.tool.index:.3f}. "
            f"Aplicat în scenă: {applied or 'nimic'}"
        )

    def set_joint_friction(self, value: float) -> None:
        """Setează direct frecarea articulațiilor."""
        self.degradation.set_joint_friction(value)
        applied = self.sensors.apply_degradation_to_scene()
        self.note(
            f"frecare de articulație setată la {self.degradation.joint.index:.3f}. "
            f"Aplicat în scenă: {applied or 'nimic'}"
        )

    def force_defect(self) -> None:
        self.degradation.defects.force_next()
        self.note("următoarea piesă este marcată ca defectă")

    def replace_tool(self) -> None:
        self.degradation.replace_tool()
        self.sensors.apply_degradation_to_scene()
        self.note("sculă înlocuită, uzura a fost resetată")

    def repair_joints(self) -> None:
        self.degradation.repair_joints()
        self.sensors.apply_degradation_to_scene()
        self.note("articulații reparate, frecarea a fost resetată")

    def repair_all(self) -> None:
        """Resetează ambele procese de degradare și defectul forțat."""
        self.degradation.reset_all()
        self.sensors.apply_degradation_to_scene()
        self.note("reparație completă: sculă nouă și articulații reparate")

    def set_defect_probability(self, probability: float) -> None:
        self.degradation.defects.set_probability(probability)
        self.note(f"probabilitatea de defect aleator: {probability:.2f}")

    def capture_snapshot_now(self) -> dict[str, Any]:
        """Captură manuală, utilă pentru verificarea camerelor.

        Nu oprește piesa la lamă: este o verificare de cameră, nu o inspecție de piesă.
        """
        record = self._capture_snapshot(hold_at_gate=False)
        if self.config.diverter_enabled:
            self.note("captură manuală: poarta rămâne neatinsă")
        return record

    def release_gate(self) -> None:
        """Eliberează o piesă rămasă în poartă, fără să aplice o rută.

        Buton de operator: dacă o piesă a rămas oprită la lamă fără verdict, poarta se
        golește și ciclul poate continua.
        """
        self.gate.reset()

    # ------------------------------------------------------------ raportare
    def telemetry_status(self) -> str:
        return self.publisher.status_line()

    def snapshot_status(self) -> str:
        return self.snapshots.status_line()

    def state_line(self) -> str:
        state = self.synthetic.state
        if self._paused:
            return f"{state} (suspendat)"
        return state

    def last_sample(self) -> dict[str, Any]:
        return dict(self._last_sample)

    def degradation_state(self) -> dict[str, Any]:
        return self.degradation.snapshot()
