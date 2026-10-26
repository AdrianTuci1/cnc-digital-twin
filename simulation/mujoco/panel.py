#!/usr/bin/env python3
"""Panoul de uzură, reparații și jurnal al geamănului MuJoCo.

Fereastra are butoanele cerute de operator — uzură, frecare, sculă nouă, repară articulațiile,
repară tot și reset — plus indicii citiți din motorul de uzură și un jurnal scurt al demonstrației:
conexiunea IoT, eșantioanele trimise, verdictul endpoint-ului, starea ciclului și comanda din
panou. Comunică prin linii JSON cu `view.py`: comenzile ies pe stdout, starea intră pe stdin.
Astfel panoul rămîne un proces separat, iar viewer-ul MuJoCo nu depinde de Tkinter.

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

#: Cît de des se citesc mesajele venite de la viewer, în milisecunde.
POLL_MS = 100

#: Cîte linii de jurnal încap în fereastră, fără derulare.
JOURNAL_ROWS = 12


class Panel:
    """Fereastra de control: butoanele trimit comenzi, etichetele arată starea."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.tool_value = tk.StringVar(value="0.000")
        self.arm1_value = tk.StringVar(value="0.000")
        self.arm2_value = tk.StringVar(value="0.000")
        self.surface_value = tk.StringVar(value="1.000")
        self.failures_value = tk.StringVar(value="—")
        self.action_value = tk.StringVar(value="—")
        self.pipeline_value = tk.StringVar(value="IoT Core: —")
        self.cycle_value = tk.StringVar(value="Cycle: —")
        self.tool_bar: ttk.Progressbar
        self.arm1_bar: ttk.Progressbar
        self.arm2_bar: ttk.Progressbar
        self.log: tk.Text
        self._log_text = ""
        self._build()
        self._start_reader()
        self.root.after(POLL_MS, self._poll)

    def _build(self) -> None:
        self.root.title("CNCDT — wear, repairs and journal")
        self.root.resizable(False, False)
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

        frame = ttk.Frame(self.root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="Pipeline", font=("TkDefaultFont", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, textvariable=self.pipeline_value, wraplength=380,
                  justify="left").grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, textvariable=self.cycle_value, wraplength=380,
                  justify="left").grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Separator(frame).grid(row=3, column=0, columnspan=2, sticky="ew", pady=8)

        ttk.Label(frame, text="CNC tool wear", font=("TkDefaultFont", 10, "bold")).grid(
            row=4, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.tool_value, width=8, anchor="e").grid(
            row=4, column=1, sticky="e")
        self.tool_bar = ttk.Progressbar(frame, length=340, maximum=1.0)
        self.tool_bar.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        # Fiecare braț are rândul lui: uzura se injectează și se repară separat, iar indicii
        # ajung în CloudWatch pe elemente distincte (ADR 0026).
        for index, (label, value, bar_name) in enumerate((
            ("Arm 1 joint friction", self.arm1_value, "arm1_bar"),
            ("Arm 2 joint friction", self.arm2_value, "arm2_bar"),
        )):
            # Trei rînduri per braț: eticheta și valoarea, bara, apoi butoanele.
            base = 6 + index * 3
            ttk.Label(frame, text=label, font=("TkDefaultFont", 10, "bold")).grid(
                row=base, column=0, sticky="w")
            ttk.Label(frame, textvariable=value, width=8, anchor="e").grid(
                row=base, column=1, sticky="e")
            bar = ttk.Progressbar(frame, length=340, maximum=1.0)
            bar.grid(row=base + 1, column=0, columnspan=2, sticky="ew", pady=(0, 4))
            setattr(self, bar_name, bar)
            arm_buttons = ttk.Frame(frame)
            arm_buttons.grid(row=base + 2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
            for column in range(2):
                arm_buttons.columnconfigure(column, weight=1)
            arm = f"arm{index + 1}"
            for column, (text, command, amount) in enumerate((
                ("Inject +0.25", "inject_wear", 0.25),
                ("Repair arm", "repair", None),
            )):
                button = ttk.Button(
                    arm_buttons,
                    text=text,
                    command=lambda c=command, a=amount, n=arm: self._send(c, a, n),
                )
                button.grid(row=0, column=column, sticky="ew", padx=2, pady=2)

        ttk.Label(frame, text="Surface quality").grid(row=12, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.surface_value, width=8, anchor="e").grid(
            row=12, column=1, sticky="e")

        buttons = ttk.Frame(frame)
        buttons.grid(row=13, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        for column in range(2):
            buttons.columnconfigure(column, weight=1)
        rows = (
            (("CNC wear +0.1", "inject_tool_wear", 0.1),
             ("CNC wear +0.25", "inject_tool_wear", 0.25)),
            (("New CNC tool", "replace_tool", None),
             ("Repair all", "repair_all", None)),
            (("Reset wear", "reset_wear", None),),
        )
        for row, items in enumerate(rows):
            for column, (label, command, amount) in enumerate(items):
                button = ttk.Button(
                    buttons,
                    text=label,
                    command=lambda c=command, a=amount: self._send(c, a),
                )
                button.grid(row=row, column=column, sticky="ew", padx=2, pady=2)

        ttk.Separator(frame).grid(row=14, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(frame, text="Active failures").grid(row=15, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.failures_value, width=18, anchor="e").grid(
            row=15, column=1, sticky="e")
        ttk.Label(frame, text="Last action").grid(row=16, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.action_value, width=24, anchor="w",
                  wraplength=240).grid(row=16, column=1, sticky="w")

        ttk.Separator(frame).grid(row=17, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(frame, text="Journal", font=("TkDefaultFont", 10, "bold")).grid(
            row=18, column=0, columnspan=2, sticky="w")
        self.log = tk.Text(frame, height=JOURNAL_ROWS, width=62, wrap="word",
                           state="disabled", font=("TkFixedFont", 9),
                           background="#101418", foreground="#e8e8e8",
                           insertbackground="#e8e8e8", relief="flat")
        self.log.grid(row=19, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        self.root.protocol("WM_DELETE_WINDOW", self._close)

        # Fereastra pornește în spatele terminalului pe macOS: o așezăm în stînga viewer-ului,
        # o aducem în față cît viewer-ul își creează fereastra, apoi o lăsăm să stea acolo.
        # Fără `focus_force`, ca o tastă apăsată în viewer să nu apese un buton de aici.
        self.root.after(50, self._place_window)
        for delay in (200, 1200, 2500):
            self.root.after(delay, self._raise_window)
        self.root.after(5000, self._release_topmost)

    def _place_window(self) -> None:
        """Așază panoul în stînga, cu dimensiunea cerută de conținut."""
        try:
            self.root.update_idletasks()
            width = max(368, self.root.winfo_reqwidth())
            height = max(660, self.root.winfo_reqheight())
            self.root.geometry(f"{width}x{height}+80+80")
        except tk.TclError:
            pass

    def _raise_window(self) -> None:
        """Aduce panoul în față, peste viewer, fără să-i fure focusul."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
        except tk.TclError:
            pass

    def _release_topmost(self) -> None:
        """După pornire, panoul nu mai stă peste viewer: se poate da click pe scenă."""
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

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
        self.tool_value.set(f"{tool:.3f}")
        self.tool_bar["value"] = tool
        for value, bar, key in (
            (self.arm1_value, self.arm1_bar, "arm1_friction_index"),
            (self.arm2_value, self.arm2_bar, "arm2_friction_index"),
        ):
            index = _number(message.get(key))
            value.set(f"{index:.3f}")
            bar["value"] = index
        self.surface_value.set(f"{_number(message.get('surface_quality'), 1.0):.3f}")
        failures = message.get("active_failures") or []
        self.failures_value.set(", ".join(str(item) for item in failures) or "—")
        self.action_value.set(str(message.get("last_action", "—")))

        pipeline = message.get("pipeline")
        if pipeline:
            self.pipeline_value.set(str(pipeline))
        cycle = message.get("cycle")
        if cycle:
            machine = message.get("machine")
            suffix = f" · machine {machine}" if machine else ""
            self.cycle_value.set(f"Cycle: {cycle}{suffix}")
        self._show_journal(message.get("journal"))

    def _show_journal(self, lines: Any) -> None:
        """Rescrie căsuța doar cînd s-a schimbat ceva: altfel cursorul sare la fiecare 100 ms."""
        if not isinstance(lines, list):
            return
        text = "\n".join(str(line) for line in lines)
        if text == self._log_text:
            return
        self._log_text = text
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        if text:
            self.log.insert("1.0", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _send(self, command: str, amount: float | None,
              component: str | None = None) -> None:
        payload: dict[str, Any] = {"command": command}
        if component is not None:
            payload["component"] = component
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
    """Un cîmp numeric din starea primită; valorile lipsă rămîn la implicitul dat."""
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
