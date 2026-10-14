"""Punctul de intrare al extensiei Kit.

Kit instanțiază :class:`CncCellExtension` la activare (vezi ``config/extension.toml``).
Extensia leagă trei lucruri:

1. :class:`~cncdt.cnc_cell.config.CellConfig` — configurația din variabile de mediu;
2. :class:`~cncdt.cnc_cell.sim_control.CellController` — ciclul, degradarea, telemetria;
3. :class:`~cncdt.cnc_cell.ui.panel.ControlPanel` — interfața de operator din ``omni.ui``.

Legarea de scenă (citirea senzorilor din prim-uri) se face amânat, câteva cadre după pornire,
pentru că scena USD poate să nu fie încărcată în momentul activării extensiei.
"""

from __future__ import annotations

from typing import Any

from .config import CellConfig
from .sim_control import CellController
from .telemetry.publisher import log
from .telemetry.sensors import CANDIDATE_PATHS, get_stage, resolve_prim_path
from .ui.panel import ControlPanel

try:
    import omni.ext

    _IExt = omni.ext.IExt
    HAS_KIT = True
except ImportError:  # pragma: no cover - doar în afara Isaac Sim.
    _IExt = object  # type: ignore[assignment,misc]
    HAS_KIT = False

try:
    import omni.kit.app as kit_app

    HAS_KIT_APP = True
except ImportError:  # pragma: no cover
    kit_app = None  # type: ignore[assignment]
    HAS_KIT_APP = False


# Numărul de cadre după care se încearcă legarea de scenă.
BIND_AFTER_FRAMES = 30
BIND_RETRY_FRAMES = 120
PISTON_PUSH_OFFSET = 0.12


class CncCellExtension(_IExt):  # type: ignore[misc,valid-type]
    """Extensia „celula CNC": control, degradare și telemetrie într-un singur proces."""

    def __init__(self) -> None:
        self._config: CellConfig | None = None
        self._controller: CellController | None = None
        self._panel: ControlPanel | None = None
        self._frames = 0
        self._next_bind_frame = BIND_AFTER_FRAMES
        self._bound = False
        self._subscription: Any = None
        self._piston_path: str | None = None
        self._piston_home: Any = None
        self._gripper_path: str | None = None
        self._part_path: str | None = None

    # ------------------------------------------------------------ ciclu de viață Kit
    def on_startup(self, ext_id: str) -> None:
        self._config = CellConfig()
        self._controller = CellController(config=self._config)
        self._controller.piston_callback = self.actuate_piston
        self._controller.grasp_callback = self.actuate_gripper
        self._controller.note(f"extensie pornită ({ext_id})")

        self._panel = ControlPanel(self._controller)
        if not self._panel.create():
            log("omni.ui indisponibil; panoul de control nu a fost creat", level="warn")

        if HAS_KIT_APP:
            stream = kit_app.get_app().get_update_event_stream()
            self._subscription = stream.create_subscription_to_pop(
                self._on_frame, name="cncdt.cnc_cell.bind"
            )

        if self._config.autostart_cycle:
            self._controller.start(autostart_cycle=True)

    def on_shutdown(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
            self._subscription = None
        if self._panel is not None:
            self._panel.destroy()
            self._panel = None
        if self._controller is not None:
            self._controller.stop()
            self._controller = None

    # ------------------------------------------------------------ legarea de scenă
    def _on_frame(self, _event: Any) -> None:
        self._frames += 1
        if self._bound or self._controller is None:
            return
        if self._frames < self._next_bind_frame:
            return

        # Prima încercare după BIND_AFTER_FRAMES, apoi reluări rare: scena poate fi
        # deschisă mai târziu, iar un eșec nu trebuie să blocheze extensia.
        self._next_bind_frame = self._frames + BIND_RETRY_FRAMES
        paths = self._controller.bind_scene()
        self._bind_piston()
        self._bind_gripper()
        if paths or get_stage() is None:
            self._bound = True
            if self._subscription is not None:
                self._subscription.unsubscribe()
                self._subscription = None

    def _bind_piston(self) -> None:
        """Găsește pistonul de rejecție și reține transformarea de repaus."""
        if self._piston_path is not None:
            return
        stage = get_stage()
        if stage is None:
            return
        path = resolve_prim_path(stage, CANDIDATE_PATHS.get("reject_piston", ()))
        if path is None:
            return
        self._piston_path = path
        prim = stage.GetPrimAtPath(path)
        attribute = prim.GetAttribute("xformOp:translate") if prim and prim.IsValid() else None
        self._piston_home = attribute.Get() if attribute and attribute.IsValid() else None
        if self._controller is not None:
            self._controller.note(f"piston legat: {path}")

    def actuate_piston(self, reject: bool) -> None:
        """Împinge sau retrage pistonul de rejecție.

        Fără o scenă legată, acțiunea rămâne raportată în jurnal. Aceasta este o limitare
        asumată: extensia nu inventează geometrie.
        """
        if self._controller is None:
            return
        if self._piston_path is None or self._piston_home is None:
            self._controller.note("piston nelegat; rejecția este doar raportată")
            return

        stage = get_stage()
        if stage is None:
            return
        prim = stage.GetPrimAtPath(self._piston_path)
        attribute = prim.GetAttribute("xformOp:translate") if prim and prim.IsValid() else None
        if attribute is None or not attribute.IsValid():
            return

        home = self._piston_home
        try:
            if reject:
                attribute.Set((home[0], home[1], home[2] + PISTON_PUSH_OFFSET))
            else:
                attribute.Set(home)
        except Exception as exc:
            self._controller.note(f"piston: setarea transformării a eșuat ({exc})")

    # ------------------------------------------------------------ griperul
    def _bind_gripper(self) -> None:
        """Găsește efectorul final și piesa, ca prinderea să aibă ținte."""
        if self._gripper_path is not None and self._part_path is not None:
            return
        stage = get_stage()
        if stage is None:
            return
        if self._gripper_path is None:
            self._gripper_path = resolve_prim_path(stage, CANDIDATE_PATHS.get("robot_gripper", ()))
        if self._part_path is None:
            self._part_path = resolve_prim_path(stage, CANDIDATE_PATHS.get("part", ()))
        if self._gripper_path and self._controller is not None:
            self._controller.note(f"griper legat: {self._gripper_path}")
        elif self._controller is not None:
            self._controller.note("griper negăsit în scenă; prinderea este doar raportată")

    def actuate_gripper(self, closed: bool) -> None:
        """Închide ori deschide griperul.

        Închiderea creează o articulație fixă între griper și piesă, la poziția relativă de
        atunci (``cncdt/cnc_cell/grasping.py``); deschiderea o șterge. Asset-ul griperului nu
        are falci articulate comandabile din script, deci aceasta este prinderea.
        """
        if self._controller is None:
            return
        stage = get_stage()
        if stage is None:
            return
        self._bind_gripper()
        if not self._gripper_path or not self._part_path:
            self._controller.note("griper ori piesă negăsite; prinderea rămâne doar raportată")
            return

        from .grasping import create_grasp_joint, remove_grasp_joint

        try:
            if closed:
                path = create_grasp_joint(stage, self._gripper_path, self._part_path)
                self._controller.note(f"piesă prinsă: articulație fixă la {path}")
            elif remove_grasp_joint(stage):
                self._controller.note("piesă eliberată: articulația de prindere a fost ștearsă")
            else:
                self._controller.note("griper deschis, dar nu exista nicio prindere activă")
        except Exception as exc:
            self._controller.note(f"prinderea a eșuat: {exc}")
