#!/usr/bin/env python3
"""Scena MuJoCo a celulei CNC: verificare, randare offscreen, viewer.

Patru moduri, toate pe aceeași scenă compusă de `cncdt_mj.scene`:

* `--check` (implicit) — compilează scena, pune poziția nominală, rulează câțiva pași și
  raportează cifrele, distanțele până la ținte, cadrul camerelor de pe flanșe, yaw-ul citit din
  scenă și contactele. Iese cu cod 1 dacă vreo verificare cade.
* `--render` — scrie un PNG offscreen, implicit `renders/cell.png`; cu `--camera arm2_wrist_camera`
  scrie cadrul camerei de pe flanșă, la rezoluția ei (`renders/arm2_wrist_camera.png`).
* `--demo` — ciclul celulei, cu prindere fizică: benzile aduc materialul la liniile de preluare,
  fiecare braț își urmează ruta din `cncdt_mj/moves.py`, falcile se închid pe piesă, o ridică și o
  lasă mai departe, iar la fiecare oprire se tipăresc eroarea, unghiul și contactele. Se termină cu
  „Ciclul: verde" ori cu „Ciclul: CĂZUT (n)". Cu `--shots` scrie un PNG la fiecare oprire și la
  fiecare prindere.
* `--view` — deschide viewer-ul interactiv, cu benzile pornite: forța de antrenare se rescrie la
  fiecare pas (`cell.apply_belt_forces`), iar ceasul se ține în ritm real, ca transportul să se
  vadă. `--no-belts` îngheață transportul, `--speed` schimbă ritmul de rulare, iar `--demo` rulează
  ciclul în fereastră, în locul benzilor goale. Împreună cu viewer-ul se deschide panoul de uzură
  și reparații (`panel.py`), cu butoanele din schema de control; `--no-panel` îl oprește. Pe macOS
  viewer-ul merge doar sub `mjpython`, deci scriptul spune asta explicit și refuză să pornească sub
  `python`.

Se rulează din venviul geamănului (`simulation/mujoco/.venv`), care este singurul loc unde
`mujoco` există (ADR 0012).
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

MUJOCO_DIR = Path(__file__).resolve().parent
if str(MUJOCO_DIR) not in sys.path:
    sys.path.insert(0, str(MUJOCO_DIR))

import mujoco  # noqa: E402  (importul cere calea de mai sus)

from cncdt_mj import (arms, belt, camera, cell, checks, control, grasp_cycle,  # noqa: E402
                      layout, part, povs, scene, wear)

#: Unde se scriu cadrele; directorul e comis, ca dovada vizuală să fie în repo.
RENDERS_DIR = MUJOCO_DIR / "renders"

#: Prefixele corpurilor brațelor, pentru regula de contact (`checks.arm_foreign_contacts`).
ARM_PREFIXES = tuple(f"{prefix}_" for prefix, _ in scene.ARM_MOUNTS)

#: La cât timp simulat se tipărește o linie de transport în viewer.
REPORT_PERIOD_S = 1.0

#: Cât durează fereastra de așchiere în `--demo`: destul ca uzura să avanseze și să se vadă în
#: viewer, fără să țină demonstrația douăsprezece secunde pe ciclu.
DEMO_MACHINING_S = 0.5

# Pragurile verificării (`MIN_WRIST_PART_PX`, `YAW_TOLERANCE_DEG`, `MAX_SETTLE_DRIFT_M`) stau în
# `cncdt_mj/checks.py`: sunt reguli pure, iar testele din `.venv`-ul proiectului le împart cu
# scriptul de față.


def build(args: argparse.Namespace) -> tuple[mujoco.MjModel, mujoco.MjData, dict, list[str]]:
    """Compune scena, cu avertismentele MuJoCo strânse pe drum."""
    with scene.warn_capture() as warnings:
        model, metadata = scene.build_model(
            seed=args.seed,
            defect=args.defect,
            family=args.family,
            blank_shape=args.blank_shape,
            blank_xy=requested_xy(args.blank_x, args.blank_y, layout.STATE_BLANK),
            blank_yaw_deg=args.blank_yaw_deg,
            part_yaw_deg=args.part_yaw_deg,
            part_xy=requested_xy(args.part_x, args.part_y, layout.STATE_MACHINED),
        )
    return model, mujoco.MjData(model), metadata, list(warnings)


def requested_xy(x: float | None, y: float | None, state: str) -> tuple[float, float] | None:
    """Poziția cerută prin `--blank-x`/`--part-x` și perechea lor, cu planul ca rezervă.

    Cota care nu se dă rămâne cea nominală din plan: altfel `--blank-x 0.6` singur ar fi ignorat
    tăcut, fiindcă scena are nevoie de amândouă coordonatele.
    """
    if x is None and y is None:
        return None
    place = next(item for item in layout.part_placements() if item.state == state)
    return (place.x if x is None else x, place.y if y is None else y)


def prepare(model: mujoco.MjModel, data: mujoco.MjData, metadata: dict,
            steps: int) -> dict:
    """Pune poziția nominală, rulează pașii, apoi citește starea din scenă."""
    scene.apply_nominal(model, data, metadata["placements"])
    before = {body: data.xpos[_body_id(model, body)].copy() for body in metadata["placements"]}
    scene.settle(model, data, steps)
    drift = {body: float(np.linalg.norm(data.xpos[_body_id(model, body)] - position))
             for body, position in before.items()}
    return {
        "drift_m": drift,
        "yaw_deg": {body: scene.part_yaw_deg(model, data, body)
                    for body in metadata["placements"]},
    }


def _body_id(model: mujoco.MjModel, body: str) -> int:
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body)
    if identifier < 0:
        raise ValueError(f"corp inexistent în scenă: {body}")
    return identifier


def camera_arguments(args: argparse.Namespace) -> tuple[str | None, int, int]:
    """Camera și rezoluția: o cameră de pe flanșă are rezoluția punctului ei de vedere."""
    width, height = povs.pov("cell").resolution
    for item in povs.POVS:
        if item.camera is not None and item.camera == args.camera:
            width, height = item.resolution
            break
    return args.camera, args.width or width, args.height or height


def check(args: argparse.Namespace) -> int:
    """Modul implicit: tot ce se poate verifica fără să te uiți la ecran."""
    steps = args.steps or scene.SETTLE_STEPS
    model, data, metadata, warnings = build(args)
    state = prepare(model, data, metadata, steps)
    counters = scene.metrics(model, data)
    reach = scene.reach_report(model, data)
    contacts = scene.contact_pairs(model, data)
    failures: list[str] = []

    print("Scena celulei CNC: verificare")
    print(f"  model de braț            : {metadata['panda_model']} (Apache-2.0), atașat de două ori")
    print(f"  sămânța piesei           : {metadata['seed']}")
    print(f"  avertismente MuJoCo      : {len(warnings)}")
    for message in warnings:
        print(f"      {message}")
    print(f"  nq / nv / nu             : {counters['nq']} / {counters['nv']} / {counters['nu']}")
    print(f"  nbody / ngeom            : {counters['nbody']} / {counters['ngeom']}")
    print(f"  contacte după {steps} pași : {counters['ncon']}")

    print(f"\nBrațe: distanța în spațiu până la fiecare țintă (rază {layout.ARM_REACH_M:.3f} m)")
    for row in reach:
        mark = "ok" if row["ok"] else "DEPĂȘITĂ"
        print(f"  {row['arm']} → {row['label']:<34} {row['distance_m']:.3f} m  {mark}")
        if not row["ok"]:
            failures.append(f"ținta „{row['label']}\" ({row['arm']}) este în afara razei brațului")

    print("\nCamere de pe flanșe: față de punctul de strângere, la 46° pe verticală")
    for arm in arms.ARMS:
        subject = arms.tool_point(model, data, arm)
        view = scene.camera_view(model, data, f"{arm}_wrist_camera", subject)
        diameter_mm = 2 * part.gear_radius_m(metadata["part"]["family"]) * 1000
        pixels = diameter_mm * float(view["px_per_mm"])
        clearance = scene.lens_clearance(model, data, f"{arm}_wrist_camera", arm)
        surround = scene.lens_surround_clearance(model, data, f"{arm}_wrist_camera", arm)
        print(f"  {arm}: punctul de strângere la {view['depth_m'] * 1000:.0f} mm, "
              f"abatere {view['angle_x_deg']:+.1f}° / {view['angle_y_deg']:+.1f}° față de ax, "
              f"piesa de {diameter_mm:.0f} mm iese la {pixels:.0f} px")
        print(f"  {'':<{len(arm)}}  spațiu liber în cîmp / în jurul obiectivului: "
              f"{clearance * 1000:.0f} / {surround * 1000:.0f} mm")
        if not view["in_frame"]:
            failures.append(f"camera de pe {arm} nu are scula în cadru")
        elif pixels < checks.MIN_WRIST_PART_PX:
            failures.append(f"camera de pe {arm} vede prea departe piesa ținută "
                            f"({pixels:.0f} px < {checks.MIN_WRIST_PART_PX:.0f} px)")
        if clearance < checks.MIN_LENS_CLEARANCE_M:
            failures.append(f"obiectivul camerei de pe {arm} stă în materialul brațului "
                            f"({clearance * 1000:.0f} mm < "
                            f"{checks.MIN_LENS_CLEARANCE_M * 1000:.0f} mm)")
        if surround < checks.MIN_LENS_SURROUND_M:
            failures.append(f"camera de pe {arm} stă în cochilia mîinii și vede prin braț "
                            f"({surround * 1000:.0f} mm < "
                            f"{checks.MIN_LENS_SURROUND_M * 1000:.0f} mm)")

    print("\nMaterial: două stări, în celulă")
    for body, place in metadata["placements"].items():
        label = f"{body} ({place['state']})"
        print(f"  {label:<22} la ({place['pos'][0]:.3f}, {place['pos'][1]:.3f}, "
              f"{place['pos'][2]:.3f}), {place['note']}")
        print(f"  {'':<22} yaw cerut {place['yaw_deg']:.1f}°, "
              f"yaw citit din scenă {state['yaw_deg'][body]:.1f}°, "
              f"mișcare la așezare {state['drift_m'][body] * 1000:.1f} mm")
        yaw_error = part.yaw_delta_deg(state["yaw_deg"][body], place["yaw_deg"])
        if abs(yaw_error) > checks.YAW_TOLERANCE_DEG:
            failures.append(f"yaw-ul lui {body} nu se citește înapoi din scenă")
        if state["drift_m"][body] > checks.MAX_SETTLE_DRIFT_M:
            failures.append(f"{body} nu s-a așezat la locul lui (s-a mișcat "
                            f"{state['drift_m'][body] * 1000:.1f} mm)")
    print(f"  eticheta piesei mașinate: {metadata['part']}")
    print(f"  blocul brut              : {metadata['blank']}")

    print("\nPrindere: cleme de tip weld, inactive cît timp piesa stă pe suprafață")
    named = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_EQUALITY, index)
             for index in range(model.neq)]
    ours = [index for index, name in enumerate(named)
            if name and name.startswith(("grasp_", "fixture_"))]
    grips = [name for name in named if name and name.startswith("grasp_")]
    fixtures = [name for name in named if name and name.startswith("fixture_")]
    # Egalitățile din modelele atașate (cuplarea falcilor Panda) sînt active din fabrică și nu
    # fac parte din prinderea noastră: se numără doar cele de mai sus.
    active = sum(1 for index in ours if bool(data.eq_active[index]))
    print(f"  {len(grips)} cleme braț↔piesă, {len(fixtures)} cleme piesă↔lume, "
          f"{active} active acum")
    if len(grips) != len(ARM_PREFIXES) * len(metadata["placements"]):
        failures.append(f"lipsesc cleme de prindere: {len(grips)} pentru "
                        f"{len(ARM_PREFIXES)} brațe × {len(metadata['placements'])} piese")
    if active:
        failures.append(f"{active} cleme sînt active la poziția nominală: piesa ar fi legată "
                        f"fără să fi fost prinsă")

    print("\nContacte: " + (", ".join(contacts) if contacts else "niciunul"))
    foreign = checks.arm_foreign_contacts(contacts, ARM_PREFIXES)
    if foreign:
        failures.append("un braț atinge un element al celulei la poziția nominală: "
                        + ", ".join(foreign))

    if failures:
        print(f"\nVerificare: CĂZUTĂ ({len(failures)})")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nVerificare: verde")
    return 0


def transport_line(model: mujoco.MjModel, data: mujoco.MjData,
                   parts: tuple[str, ...]) -> str:
    """O linie cu ce se întâmplă acum: ce piesă e pe ce bandă, cu ce viteză și cât mai are până
    la linia de oprire.

    Viewer-ul își are peisajul propriu, dar transportul se citește mai bine din cifre: linia se
    tipărește în terminal la fiecare `REPORT_PERIOD_S` secunde de timp simulat. Distanța rămasă
    este pe direcția de mers a benzii, deci pozitiv înseamnă că piesa mai are de mers.
    """
    carrying = cell.carried_parts(model, data, parts)
    positions = cell.part_positions(model, data, parts)
    cells = []
    for body in parts:
        x, y, _ = positions[body]
        speed = math.hypot(*cell.part_velocity(model, data, body))
        key = carrying.get(body)
        if key is None:
            cells.append(f"{body}: ({x:.3f}, {y:.3f}) v={speed:.3f} m/s, în afara benzii")
            continue
        item = belt.belt(key)
        remaining = (item.stop_x() - x) * item.direction[0]
        cells.append(f"{body}: ({x:.3f}, {y:.3f}) v={speed:.3f} m/s pe {key}, "
                     f"mai are {remaining:+.3f} m până la linia de oprire")
    return f"t={data.time:6.2f} s | " + " | ".join(cells)


def demo(args: argparse.Namespace, hook: arms.StepHook | None = None,
         shots: Path | None = None) -> int:
    """Compune scena, așază totul la poziția nominală și rulează o dată ciclul brațelor."""
    steps = args.steps or scene.SETTLE_STEPS
    model, data, metadata, warnings = build(args)
    if warnings:
        print(f"Avertismente la încărcare: {len(warnings)}")
        for message in warnings:
            print(f"  {message}")
    scene.apply_nominal(model, data, metadata["placements"])
    scene.settle(model, data, steps)
    return run_demo(model, data, args, str(metadata["part"]["family"]), hook=hook, shots=shots)


def run_demo(model: mujoco.MjModel, data: mujoco.MjData, args: argparse.Namespace,
             family: str | None = None, hook: arms.StepHook | None = None,
             shots: Path | None = None) -> int:
    """Ciclul de lucru cu prindere fizică: banda 1 → brațul 1 → CNC → banda 2 → raft.

    Este singura parte a verificării care se vede. La fiecare punct de lucru se tipăresc eroarea
    rămasă, cea mai mare eroare de pe drum, câți pași a durat și contactele; la fiecare prindere,
    dacă falcile au atins piesa, cât au închis și dacă au legat-o de mînă. O verigă a brațului care
    atinge celula este eșec, iar la sfîrșit se verifică locul fiecărei piese. ``family`` alege
    piesa, blocul brut și compartimentul din raft, deci toate cele cinci familii se pot proba.
    """
    family = family or part.family().name
    parts = cell.part_bodies(model)
    tool = {arm: arms.tool_length(model, data, arm) for arm in arms.ARMS}
    frames = camera.Frames(model) if shots is not None else None
    failures: list[str] = []

    print(f"Ciclul celulei: banda 1 → brațul 1 → CNC → banda 2 → raft; familia {family}")
    for arm in arms.ARMS:
        print(f"  scula {arm}                : {tool[arm] * 1000:.1f} mm de la flanșă la punctul "
              f"de strângere")
    print()

    def reporter(row: dict[str, Any]) -> None:
        """O linie pentru fiecare oprire a sculei și pentru fiecare prindere."""
        if "grasp" in row:
            item = row["grasp"]
            outcome = "reușită" if item["success"] else "RATATĂ"
            print(f"  {item['arm']:<5} {'':<11} {'':<28} falcile {item['action']:<5} "
                  f"{outcome}: contact {'da' if item['contact'] else 'nu'}, "
                  f"deschidere {item['finger_gap_m'] * 1000:.1f} mm, {item['steps']} pași")
            if frames is not None and shots is not None:
                frames.write(data, shots / f"{item['arm']}-{item['point']}-{item['action']}.png")
            return
        print(f"  {row['arm']:<5} {row['point']:<11} {row['label']:<28} {row['stage']:<10} "
              f"({row['target'][0]:.3f}, {row['target'][1]:.3f}, {row['target'][2]:.3f})  "
              f"eroare {row['position_m'] * 1000:6.1f} mm / "
              f"{row['angle_deg']:4.1f}°  drum "
              f"{row['path_residual_m'] * 1000:6.1f} mm  "
              f"pași {int(row['steps']):5d}")
        if row["finger_contacts"]:
            print(f"  {'':<11} {'':<28} falcile ating: {', '.join(row['finger_contacts'])}")
        if frames is not None and shots is not None:
            frames.write(data, shots / f"{row['arm']}-{row['point']}-{row['stage']}.png")

    result = grasp_cycle.run_cycle(
        model, data, family, seed=args.seed, settle=args.demo_settle,
        machining_s=DEMO_MACHINING_S, hook=hook, reporter=reporter)
    if frames is not None:
        frames.close()
    print(f"\nTimp simulat: {result.duration_s:.2f} s")
    failures.extend(result.failures)

    print("\nPiesele după ciclu:")
    positions = cell.part_positions(model, data, parts)
    for body in parts:
        x, y, z = positions[body]
        home = belt.belt_at(x, y)
        place = f"pe {home}" if home else "în afara benzilor"
        print(f"  {body:<12} la ({x:.3f}, {y:.3f}, {z:.3f})  {place}")

    good = sum(1 for row in result.grasps if row["success"])
    print(f"\nPrinderi: {good}/{len(result.grasps)} reușite")
    if failures:
        print(f"\nCiclul: CĂZUT ({len(failures)})")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Ciclul: verde")
    return 0


@functools.lru_cache(maxsize=1)
def panel_python() -> str | None:
    """Interpretul care are Tkinter: venv-ul geamănului nu îl are pe macOS.

    Panoul este un proces separat, deci nu trebuie să ruleze sub `mjpython`. Se încearcă întâi
    interpretul curent, apoi `python3` din PATH și, pe macOS, Python-ul de sistem, care vine cu
    Tkinter. Dacă niciunul nu îl are, viewer-ul continuă fără fereastră și spune de ce.
    """
    candidates = (sys.executable, shutil.which("python3"), "/usr/bin/python3")
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            probe = subprocess.run(
                [candidate, "-c", "import tkinter"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


class ViewerPanel:
    """Procesul Tkinter al panoului, cu o coadă de comenzi și un fir de citire.

    Comenzile ies din panou pe stdout, starea intră pe stdin. Viewer-ul nu blochează bucla de
    simulare: firul de citire doar pune mesajele într-o coadă, iar `poll` le ridică la pasul
    următor.
    """

    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.commands: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader: threading.Thread | None = None

    def start(self) -> bool:
        python = panel_python()
        if python is None:
            print("Panoul de uzură nu a pornit: niciun Python cu Tkinter în PATH "
                  "(pe macOS încearcă /usr/bin/python3).", flush=True)
            return False
        script = MUJOCO_DIR / "panel.py"
        try:
            self.process = subprocess.Popen(
                [python, str(script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            print(f"Panoul de uzură nu a pornit: {exc}", flush=True)
            self.process = None
            return False
        self._reader = threading.Thread(target=self._read, name="cncdt-panel", daemon=True)
        self._reader.start()
        print(f"Panoul de uzură: {python} {script}", flush=True)
        return True

    def _read(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = control.parse_message(line)
            except ValueError:
                continue
            self.commands.put(message)

    def poll(self) -> list[dict[str, Any]]:
        """Comenzile primite de la panou de la ultima chemare."""
        messages: list[dict[str, Any]] = []
        while True:
            try:
                messages.append(self.commands.get_nowait())
            except queue.Empty:
                return messages

    def send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            return
        try:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            self.close()

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass


def view(args: argparse.Namespace) -> int:
    """Viewer-ul interactiv, cu benzile pornite; pe macOS are nevoie de `mjpython`."""
    if sys.platform == "darwin" and "MJPYTHON_BIN" not in os.environ:
        print("Pe macOS viewer-ul MuJoCo merge doar sub `mjpython`, fiindcă fereastra trebuie\n"
              "creată pe firul principal. Semnul că rulăm sub el este `MJPYTHON_BIN`, nu numele\n"
              "din `sys.executable`: acolo scrie tot interpretul din venv.\n"
              f"  {Path(sys.executable).parent / 'mjpython'} view.py --view\n"
              "Fără afișaj, folosește `--check` și `--render`.", file=sys.stderr)
        return 2
    model, data, metadata, build_warnings = build(args)
    with scene.warn_capture() as captured:
        scene.apply_nominal(model, data, metadata["placements"])
    messages = list(build_warnings) + list(captured)
    print(f"Avertismente la încărcare: {len(messages)}")
    for message in messages:
        print(f"  {message}")
    parts = cell.part_bodies(model)
    if args.belts:
        print(f"Benzile sunt pornite la {belt.BELT_SURFACE_SPEED_MPS:.3f} m/s; își scot forța la "
              f"{layout.belt_stop_x('belt_in'):.3f} m (banda 1) și "
              f"{layout.belt_stop_x('belt_out'):.3f} m (banda 2).")
    else:
        print("Benzile sunt oprite; `--belts` le pornește.")
    print(f"Ritm de rulare {args.speed:g}x față de timpul real." if args.speed > 0.0
          else "Rulare cât de repede merge (fără ritm real).")
    steps = args.steps or 0
    started = time.monotonic()
    next_report = 0.0
    session = control.ControlSession(wear.WearModel(seed=args.seed))
    panel = ViewerPanel() if args.panel else None
    if panel is not None and not panel.start():
        panel = None
    last_wear_time = float(data.time)
    next_status = 0.0

    def tick(work: wear.Work) -> None:
        """Aplică comenzile panoului, avansează uzura și trimite starea înapoi."""
        nonlocal last_wear_time, next_status
        if panel is not None:
            for message in panel.poll():
                try:
                    detail = session.apply(message)
                except (KeyError, ValueError, TypeError) as exc:
                    detail = f"refuzat: {exc}"
                print(f"Comandă panoul: {detail}", flush=True)
        dt = max(0.0, float(data.time) - last_wear_time)
        last_wear_time = float(data.time)
        if dt > 0.0:
            session.advance(dt, work)
        if panel is not None:
            now = time.monotonic()
            if now >= next_status:
                panel.send(session.status())
                next_status = now + 0.2

    try:
        with mujoco.viewer.launch_passive(model, data) as viewer:
            camera.configure_camera(viewer.cam)
            if args.demo:
                def hook(model: mujoco.MjModel, data: mujoco.MjData) -> None:
                    """Ține viewer-ul la curent cu starea și ritmul de rulare."""
                    if not viewer.is_running():
                        raise SystemExit(0)
                    tick(wear.Work(belts=wear.BELT_COMPONENTS, arms=wear.ARM_COMPONENTS))
                    if args.speed > 0.0:
                        delay = started + data.time / args.speed - time.monotonic()
                        if delay > 0.0:
                            time.sleep(delay)
                    viewer.sync()

                return run_demo(model, data, args, str(metadata["part"]["family"]), hook=hook)
            while viewer.is_running():
                if args.belts:
                    cell.apply_belt_forces(
                        model, data, speed_scale=session.model.belt_speed_scales())
                mujoco.mj_step(model, data)
                tick(wear.Work(belts=wear.BELT_COMPONENTS) if args.belts else wear.Work())
                if args.speed > 0.0:
                    delay = started + data.time / args.speed - time.monotonic()
                    if delay > 0.0:
                        time.sleep(delay)
                viewer.sync()
                if args.belts and data.time >= next_report:
                    print(transport_line(model, data, parts), flush=True)
                    next_report = data.time + REPORT_PERIOD_S
                steps -= 1
                if args.steps and steps <= 0:
                    break
        return 0
    finally:
        if panel is not None:
            panel.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="compilează scena, rulează pași, raportează cifrele (implicit)")
    mode.add_argument("--render", nargs="?", const="", metavar="PNG",
                      help="scrie un PNG offscreen: implicit renders/cell.png, "
                           "ori renders/<camera>.png cu --camera")
    mode.add_argument("--view", action="store_true",
                      help="deschide viewer-ul (pe macOS: mjpython view.py --view)")
    parser.add_argument("--demo", action="store_true",
                       help="rulează ciclul celulei, cu prindere fizică: benzile, brațele, mașina "
                            "și raftul, cu erorile și contactele la fiecare oprire; cu `--view`, "
                            "ciclul se vede în viewer")
    parser.add_argument("--shots", nargs="?", const="", metavar="DIR",
                       help="cu `--demo`, scrie un PNG la fiecare oprire și la fiecare prindere "
                            "(implicit renders/demo/)")
    parser.add_argument("--demo-settle", type=int, default=300,
                       help="câți pași se așteaptă la fiecare punct de lucru din ciclu")
    parser.add_argument("--camera", default=None,
                       help="camera de randat: implicit camera liberă, altfel una de pe flanșă "
                            "(arm1_wrist_camera, arm2_wrist_camera)")
    parser.add_argument("--width", type=int, default=None, help="lățimea cadrului, în pixeli")
    parser.add_argument("--height", type=int, default=None, help="înălțimea cadrului, în pixeli")
    parser.add_argument("--steps", type=int, default=None,
                       help="pași înainte de randare, ori înainte de închiderea viewer-ului")
    parser.add_argument("--seed", type=int, default=part.DEFAULT_SEED,
                       help="sămânța piesei și a așezării ei")
    parser.add_argument("--defect", default=None, help="clasa de defect a piesei mașinate")
    parser.add_argument("--family", default=None, choices=part.family_names(),
                        help="familia de piese: alege piesa mașinată, blocul brut, "
                             "compartimentul din raft și cota de lucru "
                             f"(implicit {part.family().name})")
    parser.add_argument("--blank-shape", choices=part.BLANK_SHAPES, default=part.BLANK_SHAPES[0],
                       help="forma blocului brut (implicit disc, ca semifabricatul de strunjit)")
    parser.add_argument("--blank-x", type=float, default=None,
                       help="poziția X a blocului pe banda 1 (implicit: aleatoare, din sămânță)")
    parser.add_argument("--blank-y", type=float, default=None,
                       help="poziția Y a blocului pe banda 1 (implicit: aleatoare, din sămânță)")
    parser.add_argument("--blank-yaw-deg", type=float, default=None,
                       help="yaw-ul blocului brut (implicit: aleator, din sămânță)")
    parser.add_argument("--part-yaw-deg", type=float, default=None,
                       help="yaw-ul piesei mașinate pe banda 2 (implicit: aleator, din sămânță)")
    parser.add_argument("--part-x", type=float, default=None,
                       help="poziția X a piesei mașinate pe masa de descărcare a mașinii, "
                            "verificată față de amprenta mesei (implicit: lîngă universal)")
    parser.add_argument("--part-y", type=float, default=None,
                       help="poziția Y a piesei mașinate pe masa de descărcare (implicit: lîngă "
                            "universal)")
    parser.add_argument("--base-z", type=float, default=None,
                       help="cota vârfului piedestalului brațelor, pentru măsurători: "
                            f"implicit cea din document, {layout.documented_height('arm_base'):.2f} "
                            "m. La 0,55 m brațul 2 nu poate trece cu antebrațul peste banda 2 ca "
                            "să ridice piesa (`arm2_link3↔belt_out`, 106 mm eroare)")
    parser.add_argument("--belts", action=argparse.BooleanOptionalAction, default=True,
                       help="în viewer, benzile sunt pornite (implicit); `--no-belts` le oprește, "
                            "ca scena să stea pe loc")
    parser.add_argument("--panel", action=argparse.BooleanOptionalAction, default=True,
                       help="în viewer, deschide panoul de uzură și reparații (implicit pornit); "
                            "`--no-panel` îl lasă închis")
    parser.add_argument("--speed", type=float, default=1.0,
                       help="ritmul de rulare în viewer față de timpul real (implicit 1; "
                            "0 rulează cât de repede merge)")
    args = parser.parse_args(argv)

    if args.base_z is not None:
        # Cota documentată este cea din `docs/11-geaman-mujoco.md` §2; coborârea se face doar
        # pentru măsurători, ca scena comisă să rămână cea verificată.
        print(f"Măsurătoare: vârful piedestalului brațelor la {args.base_z:.2f} m, în locul cotei "
              f"documentate, {layout.documented_height('arm_base'):.2f} m.")
        layout.ARM_BASE_Z = args.base_z

    if args.view:
        return view(args)
    if args.demo:
        shots = None if args.shots is None else (
            Path(args.shots) if args.shots else RENDERS_DIR / "demo")
        return demo(args, shots=shots)
    if args.render is not None:
        camera_name, width, height = camera_arguments(args)
        path = Path(args.render) if args.render else RENDERS_DIR / (
            f"{camera_name}.png" if camera_name else "cell.png")
        model, data, metadata, warnings = build(args)
        prepare(model, data, metadata, args.steps or scene.SETTLE_STEPS)
        # Cadrul de verificare al unei camere de pe flanșă arată ca în dataset: fără umbre
        # aruncate și cu fețele din spate ale mîinii, ca brațul să nu pară transparent.
        item = next((pov for pov in povs.POVS if pov.camera == camera_name), None)
        camera.render(model, data, path, camera_name, width, height,
                      shadows=item.shadows if item else True,
                      backfaces=item.backfaces if item else False)
        print(f"scris {path} ({width}x{height}, camera "
              f"{camera_name or 'liberă'}, {len(warnings)} avertismente)")
        return 0
    return check(args)


if __name__ == "__main__":
    raise SystemExit(main())
