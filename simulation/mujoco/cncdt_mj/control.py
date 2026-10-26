"""Comenzile de uzură și reparație, împărțite de panou, viewer și cloud.

Modulul este **pur**: ține un :class:`~cncdt_mj.wear.WearModel` și aplică mesaje JSON cu
aceleași nume de comandă ca schema `cnc.control.v1.json` acolo unde ea le are deja
(`inject_tool_wear`, `inject_joint_friction`, `replace_tool`, `repair_joints`). Comenzile
locale geamănului (`reset_wear`) rămân în afara schemei, ca să nu ceară bump de versiune.

Panoul Tkinter (`simulation/mujoco/panel.py`) trimite liniile JSON, iar `view.py` le citește și
le aplică prin :class:`ControlSession`. Aceeași clasă poate fi chemată dintr-un REPL ori dintr-un
test, fără fereastră și fără MuJoCo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import wear


#: Butoanele panoului: eticheta din fereastră, comanda, cantitatea și componenta (unde există).
#: Brațele se injectează și se repară separat, cu comenzile locale `inject_wear` și `repair`.
BUTTONS: tuple[tuple[str, str, float | None, str | None], ...] = (
    ("CNC wear +0.1", "inject_tool_wear", 0.1, None),
    ("CNC wear +0.25", "inject_tool_wear", 0.25, None),
    ("Arm 1 inject +0.25", "inject_wear", 0.25, "arm1"),
    ("Arm 2 inject +0.25", "inject_wear", 0.25, "arm2"),
    ("Arm 1 repair", "repair", None, "arm1"),
    ("Arm 2 repair", "repair", None, "arm2"),
    ("New CNC tool", "replace_tool", None, None),
    ("Repair all", "repair_all", None, None),
    ("Reset wear", "reset_wear", None, None),
)


def parse_message(line: str) -> dict[str, Any]:
    """Citește o linie JSON de la panou; orice altceva este refuzat cu un mesaj clar."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"linie JSON invalidă: {exc}") from None
    if not isinstance(payload, dict):
        raise ValueError("mesajul panoului trebuie să fie un obiect JSON")
    return payload


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"valoare numerică invalidă: {value!r}") from None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def apply_message(model: wear.WearModel, message: Mapping[str, Any]) -> str:
    """Aplică o comandă din panou și întoarce descrierea ei, pentru jurnal.

    ``repair_all`` este compus din cele două reparații ale schemei, ca panoul să aibă un singur
    buton care readuce scula și articulațiile la starea de început. ``reset_wear`` rămâne comandă
    locală și readuce toate cele șapte componente, plus rata de uzură.
    """
    command = str(message.get("command", "")).strip()
    if not command:
        raise ValueError("comanda lipsește")

    if command == "repair_all":
        model.apply_action("replace_tool")
        model.apply_action("repair_joints")
        return "tool and joints repaired"

    action = model.apply_action(
        command,
        name=_optional_text(message.get("component")),
        amount=_optional_float(message.get("amount")),
        value=_optional_float(message.get("value")),
        duration_s=_optional_float(message.get("duration_s")),
    )
    return action.detail or action.command


def status_message(model: wear.WearModel, last_action: str = "—") -> dict[str, Any]:
    """Starea pe care panoul o afișează, cu indicii din contract și sănătatea brațelor.

    Fiecare braț are indicele lui: panoul arată două bare, iar metricile din CloudWatch au
    elementele `arm1` și `arm2` separate (ADR 0026).
    """
    joint_health = min(model.health_of(name) for name in wear.ARM_COMPONENTS)
    return {
        "tool_wear_index": model.tool_wear_index(),
        "joint_friction_index": model.joint_friction_index(),
        "arm1_friction_index": model.wear_index("arm1"),
        "arm2_friction_index": model.wear_index("arm2"),
        "arm1_health": round(model.health_of("arm1"), 6),
        "arm2_health": round(model.health_of("arm2"), 6),
        "surface_quality": model.surface_quality(),
        "tool_health": model.health_of("cnc_tool"),
        "joint_health": round(joint_health, 6),
        "belt_speed_scale": model.belt_speed_scale(),
        "active_failures": sorted(failure.kind for failure in model.active.values()),
        "last_action": last_action,
    }


@dataclass
class ControlSession:
    """Motorul de uzură plus ultima acțiune de operator, pentru viewer și panou."""

    model: wear.WearModel = field(default_factory=wear.WearModel)
    last_action: str = "—"

    def apply(self, message: Mapping[str, Any]) -> str:
        """Aplică mesajul și ține minte ce s-a întâmplat, ca panoul să aibă un rând de istoric."""
        self.last_action = apply_message(self.model, message)
        return self.last_action

    def status(self) -> dict[str, Any]:
        """Starea curentă, gata de trimis ca o linie JSON."""
        return status_message(self.model, self.last_action)

    def advance(self, seconds: float, work: wear.Work) -> list[wear.Failure]:
        """Trece timpul de lucru prin motorul de uzură, ca în sesiunea de colectare."""
        return self.model.advance(seconds, work)
