#!/usr/bin/env python3
"""Panoul de uzură și reparații al geamănului MuJoCo.

Fereastra are butoanele cerute de operator — uzură, frecare, sculă nouă, repară articulațiile,
repară tot și reset — plus indicii citiți din motorul de uzură. Comunică prin linii JSON cu
`view.py`: comenzile ies pe stdout, starea intră pe stdin. Astfel panoul rămâne un proces
separat, iar viewer-ul MuJoCo nu depinde de Tkinter.

Se pornește automat din `view.py --view`; pentru o probă fără viewer:

    /usr/bin/python3 simulation/mujoco/panel.py --self-test

Pe macOS, Python-ul din venv-ul geamănului nu are Tkinter, deci `view.py` caută un interpret
cu Tkinter (de obicei `/usr/bin/python3`) și pornește panoul cu el.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

#: Cât de des se citesc mesajele venite de la viewer, în milisecunde.
POLL_MS = 100


class Panel:
    """Fereastra de control: butoanele trimit comenzi, etichetele arată starea."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.tool_value = tk.StringVar(value="0.000")
        self.joint_value = tk.StringVar(value="0.000")
        self.surface_value = tk.StringVar(value="1.000")
        self.failures_value = tk.StringVar(value="—")
        self.action_value = tk.StringVar(value="—")
        self.tool_bar: ttk.Progressbar
        self.joint_bar: ttk.Progressbar
        self._build()
        self._start_reader()
        self.root.after(POLL_MS, self._poll)

    def _build(self) -> None:
        self.root.title("CNCDT — uzură și reparații")
        self.root.resizable(False, False)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        frame = ttk.Frame(self.root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="Uzură sculă", font=("TkDefaultFont", 10, "bold")).grid(
            row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.tool_value, width=8, anchor="e").grid(
            row=0, column=1, sticky="e")
        self.tool_bar = ttk.Progressbar(frame, length=260, maximum=1.0)
        self.tool_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(frame, text="Frecare articulații", font=("TkDefaultFont", 10, "bold")).grid(
            row=2, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.joint_value, width=8, anchor="e").grid(
            row=2, column=1, sticky="e")
        self.joint_bar = ttk.Progressbar(frame, length=260, maximum=1.0)
        self.joint_bar.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(frame, text="Calitate suprafață").grid(row=4, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.surface_value, width=8, anchor="e").grid(
            row=4, column=1, sticky="e")

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        for column in range(2):
            buttons.columnconfigure(column, weight=1)
        rows = (
            (("Uzură +0.1", "inject_tool_wear", 0.1),
             ("Uzură +0.25", "inject_tool_wear", 0.25)),
            (("Frecare +0.25", "inject_joint_friction", 0.25),
             ("Sculă nouă", "replace_tool", None)),
            (("Repară articulații", "repair_joints", None),
             ("Repară tot", "repair_all", None)),
            (("Reset degradare", "reset_wear", None),),
        )
        for row, items in enumerate(rows):
            for column, (label, command, amount) in enumerate(items):
                button = ttk.Button(
                    buttons,
                    text=label,
                    command=lambda c=command, a=amount: self._send(c, a),
                )
                button.grid(row=row, column=column, sticky="ew", padx=2, pady=2)

        ttk.Separator(frame).grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(frame, text="Eșecuri active").grid(row=7, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.failures_value, width=18, anchor="e").grid(
            row=7, column=1, sticky="e")
        ttk.Label(frame, text="Ultima acțiune").grid(row=8, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.action_value, width=24, anchor="w",
                  wraplength=240).grid(row=8, column=1, sticky="w")

        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _start_reader(self) -> None:
        """Citește starea de la viewer fără să blocheze bucla Tkinter."""
        thread = threading.Thread(target=self._read_stdin, name="cncdt-panel-stdin", daemon=True)
        thread.start()

    def _read_stdin(self) -> None:
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    self.messages.put(message)
        finally:
            self.messages.put(None)

    def _poll(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if message is None:
                    self._close()
                    return
                self._show(message)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _show(self, message: dict[str, Any]) -> None:
        tool = _number(message.get("tool_wear_index"))
        joint = _number(message.get("joint_friction_index"))
        self.tool_value.set(f"{tool:.3f}")
        self.joint_value.set(f"{joint:.3f}")
        self.tool_bar["value"] = tool
        self.joint_bar["value"] = joint
        self.surface_value.set(f"{_number(message.get('surface_quality'), 1.0):.3f}")
        failures = message.get("active_failures") or []
        self.failures_value.set(", ".join(str(item) for item in failures) or "—")
        self.action_value.set(str(message.get("last_action", "—")))

    def _send(self, command: str, amount: float | None) -> None:
        payload: dict[str, Any] = {"command": command}
        if amount is not None:
            payload["amount"] = amount
        try:
            print(json.dumps(payload), flush=True)
        except (BrokenPipeError, OSError):
            self._close()

    def _close(self) -> None:
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def _number(value: Any, default: float = 0.0) -> float:
    """Un câmp numeric din starea primită; valorile lipsă rămân la implicitul dat."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--self-test", action="store_true",
                        help="deschide fereastra, o închide după o clipă și iese cu 0")
    args = parser.parse_args(argv)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"panoul nu poate porni fără afișaj: {exc}", file=sys.stderr)
        return 2

    Panel(root)
    if args.self_test:
        root.after(600, root.destroy)
        root.mainloop()
        print("panou: verde")
        return 0

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
