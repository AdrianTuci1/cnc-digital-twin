"""Panoul de control al celulei CNC.

De ce ``omni.ui`` și nu o aplicație web separată: Kit transmite întreaga interfață către
clientul WebRTC. Un panou construit aici apare automat în browser, fără server web, fără
porturi suplimentare și fără sincronizare de stare între două procese.

Panoul nu conține logică de business. Toate acțiunile delegă către
:class:`cncdt.cnc_cell.sim_control.CellController`, care este testabil fără Isaac Sim.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..sim_control import CellController

try:  # omni.ui există doar în interiorul Kit.
    import omni.ui as ui

    HAS_OMNI_UI = True
except ImportError:  # pragma: no cover - se întâmplă doar în afara Isaac Sim.
    ui = None  # type: ignore[assignment]
    HAS_OMNI_UI = False

try:
    import omni.kit.app as kit_app

    HAS_KIT_APP = True
except ImportError:  # pragma: no cover
    kit_app = None  # type: ignore[assignment]
    HAS_KIT_APP = False


WINDOW_TITLE = "CNC Digital Twin — celula CNC"
REFRESH_SECONDS = 0.4
BAR_WIDTH = 18


def _bar(value: float, maximum: float = 1.0) -> str:
    """Bara textuală de progres; evită dependența de modelul intern al widget-urilor."""
    fraction = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
    filled = int(round(fraction * BAR_WIDTH))
    return f"{'█' * filled}{'░' * (BAR_WIDTH - filled)} {fraction * 100:5.1f}%"


class ControlPanel:
    """Fereastra de operator: degradare, ciclu, telemetrie și jurnal de evenimente."""

    def __init__(self, controller: CellController, window_title: str = WINDOW_TITLE) -> None:
        self._controller = controller
        self._title = window_title
        self._window: Any = None
        self._subscription: Any = None
        self._labels: dict[str, Any] = {}
        self._slider: Any = None
        self._tool_wear_slider: Any = None
        self._joint_friction_slider: Any = None
        # Guard împotriva buclei: refresh() scrie în slider, iar sliderul cheamă controlerul.
        self._syncing_sliders = False
        self._command_field: Any = None
        self._last_refresh = 0.0

    @property
    def available(self) -> bool:
        """Adevărat când rulează în Kit, unde ``omni.ui`` este disponibil."""
        return HAS_OMNI_UI

    # ------------------------------------------------------------ ciclu de viață
    def create(self) -> bool:
        """Construiește fereastra. Întoarce ``False`` în afara Isaac Sim."""
        if not HAS_OMNI_UI:
            return False
        if self._window is not None:
            return True

        self._window = ui.Window(self._title, width=400, height=720)
        self._window.frame.set_build_fn(self._build)
        self._window.set_visibility_changed_fn(self._on_visibility)
        self._subscribe()
        return True

    def destroy(self) -> None:
        self._unsubscribe()
        if self._window is not None:
            self._window.visible = False
            self._window = None
        self._labels.clear()
        self._slider = None
        self._tool_wear_slider = None
        self._joint_friction_slider = None
        self._command_field = None

    def _subscribe(self) -> None:
        if not HAS_KIT_APP or self._subscription is not None:
            return
        stream = kit_app.get_app().get_update_event_stream()
        self._subscription = stream.create_subscription_to_pop(
            self._on_update, name="cncdt.cnc_cell.panel"
        )

    def _unsubscribe(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
            self._subscription = None

    def _on_visibility(self, visible: bool) -> None:
        if visible:
            self.refresh()
        else:
            self._unsubscribe()

    def _on_update(self, _event: Any) -> None:
        now = time.monotonic()
        if now - self._last_refresh < REFRESH_SECONDS:
            return
        self._last_refresh = now
        self.refresh()

    # ------------------------------------------------------------ desenare
    def _build(self) -> None:
        controller = self._controller
        with ui.VStack(spacing=6, height=0):
            with ui.CollapsableFrame("Ciclu și degradare", collapsed=False):
                with ui.VStack(spacing=4):
                    self._value_row("Stare", "state")
                    self._value_row("Ciclu", "cycle")
                    self._value_row("Piesă", "part")
                    self._value_row("Uzură sculă", "tool_wear")
                    self._value_row("Frecare articulații", "joint_friction")
                    self._value_row("Calitate suprafață", "surface")
                    with ui.HStack(spacing=4, height=26):
                        ui.Button("Uzură +0.1", clicked_fn=lambda: controller.inject_tool_wear(0.1), height=26)
                        ui.Button("Uzură +0.25", clicked_fn=lambda: controller.inject_tool_wear(0.25), height=26)
                        ui.Button("Frecare +0.25", clicked_fn=lambda: controller.inject_joint_friction(0.25), height=26)
                    with ui.HStack(spacing=4, height=26):
                        ui.Button("Sculă nouă", clicked_fn=controller.replace_tool, height=26)
                        ui.Button("Repară articulații", clicked_fn=controller.repair_joints, height=26)
                        ui.Button("Repară tot", clicked_fn=controller.repair_all, height=26)
                    with ui.HStack(spacing=4, height=26):
                        ui.Button("Defect forțat", clicked_fn=controller.force_defect, height=26)
                    ui.Label("Uzură sculă (set direct)", height=18)
                    self._tool_wear_slider = ui.FloatSlider(min=0.0, max=1.0, step=0.01)
                    self._tool_wear_slider.model.set_value(controller.degradation.tool.index)
                    self._tool_wear_slider.model.add_value_changed_fn(self._on_tool_wear_slider)
                    ui.Label("Frecare articulații (set direct)", height=18)
                    self._joint_friction_slider = ui.FloatSlider(min=0.0, max=1.0, step=0.01)
                    self._joint_friction_slider.model.set_value(controller.degradation.joint.index)
                    self._joint_friction_slider.model.add_value_changed_fn(
                        self._on_joint_friction_slider
                    )

            with ui.CollapsableFrame("Producție și inspecție", collapsed=False):
                with ui.VStack(spacing=4):
                    self._value_row("Piese inspectate", "parts")
                    self._value_row("Acceptate", "accepted")
                    self._value_row("Respinse (cloud)", "rejected")
                    self._value_row("Rată respingere", "reject_rate")
                    self._value_row("Ultimul verdict", "verdict")
                    self._value_row("Ultima rută", "route")
                    self._value_row("Sortate bune / rebut", "routes")
                    self._value_row("Poartă (piesa oprită)", "gate_waiting")
                    self._value_row("Sortare la lamă", "gate_routes")
                    self._value_row("Piston (acționări)", "piston")
                    self._value_row("Prinderi", "grasps")
                    with ui.HStack(spacing=4, height=26):
                        ui.Button("Prinde piesa", clicked_fn=controller.grasp_part, height=26)
                        ui.Button("Lasă piesa", clicked_fn=controller.release_part, height=26)
                    with ui.HStack(spacing=4, height=26):
                        ui.Button("Captură QA acum", clicked_fn=controller.capture_snapshot_now, height=26)
                        ui.Button("Eliberează poarta", clicked_fn=controller.release_gate, height=26)
                        ui.Button("Leagă senzorii", clicked_fn=self._bind_scene, height=26)

            with ui.CollapsableFrame("Control", collapsed=False):
                with ui.VStack(spacing=4):
                    with ui.HStack(spacing=4, height=28):
                        ui.Button("Pornește", clicked_fn=controller.start, height=28)
                        ui.Button("Suspendă", clicked_fn=lambda: controller.pause(True), height=28)
                        ui.Button("Reia", clicked_fn=lambda: controller.pause(False), height=28)
                        ui.Button("Oprește", clicked_fn=controller.stop, height=28)
                    with ui.HStack(spacing=4, height=26):
                        ui.Button("Ciclu nou", clicked_fn=controller.start_cycle, height=26)
                        ui.Button("Oprește ciclul", clicked_fn=self._stop_cycle, height=26)
                        ui.Button("Reset degradare", clicked_fn=self._reset, height=26)
                    ui.Label("Probabilitate defect aleator", height=18)
                    self._slider = ui.FloatSlider(min=0.0, max=1.0, step=0.01)
                    self._slider.model.set_value(controller.config.random_defect_probability)
                    self._slider.model.add_value_changed_fn(self._on_probability)

            with ui.CollapsableFrame("Telemetrie", collapsed=False):
                with ui.VStack(spacing=4):
                    self._value_row("Publicator", "publisher")
                    self._value_row("Imagini QA", "snapshots")
                    self._value_row("Ultimul eșantion", "sample")
                    with ui.HStack(spacing=4, height=26):
                        ui.Button("Trimite comandă JSON", clicked_fn=self._send_command, height=26)
                    self._command_field = ui.StringField(height=24)
                    self._command_field.model.set_value('{"command": "inject_tool_wear", "amount": 0.2}')

            with ui.CollapsableFrame("Jurnal", collapsed=False):
                self._labels["events"] = ui.Label(
                    "—", word_wrap=True, height=150, alignment=ui.Alignment.LEFT_TOP
                )

        self.refresh()

    def _value_row(self, title: str, key: str) -> None:
        with ui.HStack(spacing=6, height=20):
            ui.Label(title, width=130, height=20)
            self._labels[key] = ui.Label("—", height=20)

    # ------------------------------------------------------------ acțiuni
    def _bind_scene(self) -> None:
        self._controller.bind_scene()

    def _stop_cycle(self) -> None:
        self._controller.on_control_message({"command": "stop_cycle"})

    def _reset(self) -> None:
        self._controller.on_control_message({"command": "reset"})

    def _on_probability(self, model: Any) -> None:
        try:
            self._controller.set_defect_probability(float(model.get_value_as_float()))
        except Exception:  # pragma: no cover - model intern omni.ui
            pass

    def _on_tool_wear_slider(self, model: Any) -> None:
        if self._syncing_sliders:
            return
        try:
            self._controller.set_tool_wear(float(model.get_value_as_float()))
        except Exception:  # pragma: no cover - model intern omni.ui
            pass

    def _on_joint_friction_slider(self, model: Any) -> None:
        if self._syncing_sliders:
            return
        try:
            self._controller.set_joint_friction(float(model.get_value_as_float()))
        except Exception:  # pragma: no cover - model intern omni.ui
            pass

    def _send_command(self) -> None:
        if self._command_field is None:
            return
        raw = self._command_field.model.get_value_as_string()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._controller.note(f"comandă JSON invalidă: {exc}")
            return
        if not isinstance(payload, dict):
            self._controller.note("comanda trebuie să fie un obiect JSON")
            return
        self._controller.on_control_message(payload)

    # ------------------------------------------------------------ reîmprospătare
    def refresh(self) -> None:
        """Copiază starea controlerului în etichete. Nu conține logică de business."""
        if not self._labels and not self._slider:
            return
        controller = self._controller
        stats = controller.stats
        state = controller.degradation_state()

        self._set("state", controller.state_line())
        self._set("cycle", str(stats.cycles))
        self._set("part", controller.degradation.part_id)
        self._set(
            "tool_wear",
            f"{state['tool_wear_index']:.3f}  {_bar(state['tool_wear_index'])}",
        )
        self._set(
            "joint_friction",
            f"{state['joint_friction_index']:.3f}  {_bar(state['joint_friction_index'])}",
        )
        self._set(
            "surface",
            f"{state['surface_quality']:.3f}  {_bar(state['surface_quality'])}",
        )
        self._sync_slider(self._tool_wear_slider, state["tool_wear_index"])
        self._sync_slider(self._joint_friction_slider, state["joint_friction_index"])

        self._set("parts", str(stats.parts_inspected))
        self._set("accepted", str(stats.accepted_cloud))
        self._set("rejected", str(stats.rejected_cloud))
        self._set("reject_rate", f"{stats.reject_rate * 100:.1f}%")
        self._set("verdict", f"{stats.last_verdict} / {stats.last_verdict_reason}")
        router = controller.router
        self._set("route", f"{router.last_route} (piesa {router.last_part_id})")
        self._set("routes", f"{router.good} / {router.scrap}")
        gate = controller.gate
        self._set("gate_waiting", f"{gate.waiting_for} ({gate.state.value})")
        self._set("gate_routes", gate.status_line)
        self._set("piston", str(stats.piston_actuations))
        self._set("grasps", str(stats.grasps))

        self._set("publisher", controller.telemetry_status())
        self._set("snapshots", controller.snapshot_status())
        self._set("sample", self._sample_line())
        self._set("events", "\n".join(reversed(controller.recent_events(12))) or "—")

    def _sample_line(self) -> str:
        sample = self._controller.last_sample()
        if not sample:
            return "—"
        return (
            f"{sample.get('machine_state', '?')} | "
            f"sarcină {sample.get('spindle_load', 0):.2f} | "
            f"vibrații {sample.get('vibration', 0):.2f} | "
            f"temp {sample.get('temp_c', 0):.1f}°C | "
            f"debit {sample.get('coolant_flow_lpm', 0):.1f} l/min"
        )

    def _set(self, key: str, text: str) -> None:
        label = self._labels.get(key)
        if label is not None:
            label.text = text

    def _sync_slider(self, slider: Any, value: float) -> None:
        """Aduce sliderul la valoarea din controler, fără a re-declanșa callback-ul.

        Slider-ele de uzură sunt și intrare, și afișaj: valorile cresc singure în timpul
        ciclului. Guard-ul oprește bucla refresh → set_value → callback → set_tool_wear.
        """
        if slider is None:
            return
        try:
            current = float(slider.model.get_value_as_float())
        except Exception:  # pragma: no cover - model intern omni.ui
            return
        if abs(current - value) < 0.005:
            return
        self._syncing_sliders = True
        try:
            slider.model.set_value(value)
        finally:
            self._syncing_sliders = False
