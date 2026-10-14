#!/usr/bin/env python3
"""Sesiunea de colectare: cicluri întregi, cu telemetrie și cadre, scrise ca dataset.

O sesiune este o suită de cicluri pe aceeași celulă, fiecare cu piesa lui (altă sămânță), și scoate
exact ce cere antrenarea:

* `samples.jsonl` — eșantioane brute la ~10 Hz, cu câmpurile din `cnc.telemetry.v1.json`;
* `snapshots.jsonl` — metadatele cadrelor de inspecție, cu rolul lor (`sorting` / `inspection`),
  exact ce s-ar publica pe MQTT și în bucketul de date;
* `frames.jsonl` — indexul cadrelor: fiecare PNG al fiecărui punct de vedere, cu eticheta piesei
  (familie, defect, verdict) — pe el se antrenează clasificarea;
* `frames/` — PNG-urile, pe cicluri;
* `readings.jsonl` — mărimile care nu au coloană în contract (efort pe braț, forță de strângere,
  poziția și viteza piesei, contacte), ca diagnostic al sesiunii;
* `session.json` — parametrii, versiunile, totalurile și etichetele fiecărui ciclu.

Ciclul este cel din `view.py --demo`: benzile aduc materialul la liniile de preluare, brațul 1 ia
blocul, îl duce în universal și lasă roata pe banda 2, iar brațul 2 o sortează în locul familiei.
Prinderea nu este încă modelată (falcile doar ating piesa), deci piesa rămâne pe bandă: cadrele
arată piesa în falci, iar `docs/11-geaman-mujoco.md` §8 ține evidența a ce lipsește.

Se rulează din venviul geamănului (`simulation/mujoco/.venv`), singurul loc unde există `mujoco`
(ADR 0012): `make mj-record` ori direct `python run.py --cycles 2`.
"""

from __future__ import annotations

import argparse
import dataclasses
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

MUJOCO_DIR = Path(__file__).resolve().parent
if str(MUJOCO_DIR) not in sys.path:
    sys.path.insert(0, str(MUJOCO_DIR))

import mujoco  # noqa: E402  (importul cere calea de mai sus)

from cncdt_mj import (arms, camera, cell, layout, moves, part, povs,  # noqa: E402
                      scene, sensors, telemetry)

#: Unde se scriu sesiunile. Directorul e generat și ignorat de git: un ciclu de cinci familii
#: înseamnă mii de cadre, care n-au ce căuta în istoricul repo-ului.
RUNS_DIR = MUJOCO_DIR / "runs"

#: Cât de aproape de axul camerei fixe trebuie să ajungă piesa ca să se ia cadrul de proces.
#: 10 mm la 0,12 m/s înseamnă circa 0,08 s de mers, adică sub un pas de eșantionare.
ON_BELT_TRIGGER_M = 0.01


def _body_id(model: mujoco.MjModel, name: str) -> int:
    """Identificatorul unui corp, cu eroare care spune ce nume lipsește."""
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    if identifier < 0:
        raise ValueError(f"corp inexistent în scenă: {name}")
    return int(identifier)


@dataclass
class Session:
    """Sesiunea care se scrie: unde, cu ce parametri și ce s-a adunat până acum.

    Rândurile se țin în memorie până la sfârșit: o sesiune are zeci de mii de eșantioane, nu
    milioane, iar așa se pot valida și număra înainte de prima scriere pe disc.
    """

    directory: Path
    name: str
    seed: int
    family: str
    sample_hz: float
    start_ms: int
    cycles: int
    frames_on: bool = True

    #: Mărimile așchierii: vin din scenariu până la motorul de uzură (`telemetry.MachineReading`).
    machine: telemetry.MachineReading = field(default_factory=telemetry.MachineReading)

    samples: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)
    readings: list[dict[str, Any]] = field(default_factory=list)
    labels: list[dict[str, Any]] = field(default_factory=list)

    #: Timpul simulat cumulat de ciclurile încheiate: fiecare ciclu are scena lui, care pornește de
    #: la zero, iar ceasul sesiunii curge peste ele.
    elapsed_s: float = 0.0

    def path(self, *parts: str) -> Path:
        """Un drum în directorul sesiunii."""
        return self.directory.joinpath(*parts)


class Recorder:
    """Ce se întâmplă la fiecare pas de simulare: eșantioane, cadre, etichete.

    Se instalează ca `hook` la mișcarea brațelor și la transport, deci vede fiecare pas al
    ciclului. Eșantioanele se iau la rata cerută (10 Hz implicit), cadrele la momentele din
    `povs.frames_per_cycle`, iar starea mașinii se schimbă cu punctul de lucru la care s-a ajuns.
    """

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, parts: tuple[str, ...],
                 session: Session, shots: camera.Frames | None, label: Mapping[str, Any],
                 machined: str) -> None:
        self.model = model
        self.data = data
        self.parts = parts
        self.session = session
        self.shots = shots
        self.label = dict(label)
        self.machined = machined
        self.stage: str | None = None
        self.cycle_index = 0
        self.taken: set[str] = set()
        self.cycle_frames: list[str] = []
        self.steps = 0
        self.steps_per_sample = max(
            1, int(round(1.0 / (session.sample_hz * float(model.opt.timestep)))))

    # ------------------------------------------------------------ momentele ciclului
    def begin_cycle(self, index: int) -> None:
        """Deschide un ciclu: identificatorul lui și cadrele de luat de la zero."""
        self.cycle_index = index
        self.stage = None
        self.taken = set()
        self.cycle_frames = []
        self.steps = 0

    def enter(self, stage: str) -> None:
        """S-a ajuns la un punct de lucru: de aici starea mașinii este a lui."""
        self.stage = stage

    def leave(self) -> None:
        """S-a terminat punctul de lucru: celula se întoarce în așteptare."""
        self.stage = None

    # ------------------------------------------------------------ la fiecare pas
    def __call__(self, model: mujoco.MjModel, data: mujoco.MjData) -> None:
        """Un pas de simulare: eșantionul, apoi cadrul de pe bandă, dacă e cazul."""
        self.steps += 1
        if self.steps % self.steps_per_sample == 0:
            self.write_sample()
        if self.frames_on:
            self.capture_on_belt()

    def clock(self) -> float:
        """Timpul simulat al sesiunii: ciclurile încheiate plus ceasul ciclului de acum."""
        return self.session.elapsed_s + float(self.data.time)

    def ts_ms(self) -> int:
        """Marcajul de timp epoch de acum, pornind de la ceasul sesiunii."""
        return self.session.start_ms + int(round(self.clock() * 1000))

    def write_sample(self) -> None:
        """Un eșantion brut, cu efortul articulațiilor măsurat în scenă."""
        values = sensors.reading(self.model, self.data, self.parts)
        self.session.samples.append(telemetry.sample(
            seq=len(self.session.samples),
            ts_ms=self.ts_ms(),
            machine_state=telemetry.machine_state_for(self.stage),
            cycle=telemetry.cycle_id(self.cycle_index),
            part=telemetry.part_id(self.cycle_index),
            joint_effort=values["joint_effort_nm"],
            machine=self.session.machine,
            is_synthetic_defect=self.synthetic_defect,
        ))
        self.session.readings.append(sensors.reading_row(values))

    # ------------------------------------------------------------ cadrele
    def capture_on_belt(self) -> None:
        """Cadrul camerei fixe, luat când piesa mașinată trece prin dreptul ei.

        Camera fixă nu are punct de lucru: ea fotografiază piesa care trece pe bandă, o dată pe
        ciclu, când piesa intră în toleranța din jurul axului ei optic.
        """
        for item in povs.on_belt():
            if self.key(item, povs.ON_BELT) in self.taken:
                continue
            x = float(self.data.xpos[_body_id(self.model, self.machined)][0])
            if abs(x - float(item.belt_x_m)) <= ON_BELT_TRIGGER_M:
                self.capture(item, povs.ON_BELT)

    def key(self, item: povs.Pov, stage: str) -> str:
        """Cheia unui cadru: un punct de vedere poate fi fotografiat la mai multe momente.

        `arm1_wrist` se fotografiază și la preluare, și în universal, deci „l-am luat deja" nu se
        poate ține minte doar pe numele camerei: altfel al doilea cadru al aceleiași camere ar
        lipsi tăcut din sesiune, iar verificarea de la sfârșit ar cădea pe numărătoare.
        """
        return f"{item.name}@{stage}"

    @property
    def frames_on(self) -> bool:
        """Se scriu cadre? Fără randator, sesiunea rămâne doar cu telemetria."""
        return self.shots is not None

    def after_stage(self) -> None:
        """Cadrele planificate pentru punctul de lucru la care tocmai s-a ajuns."""
        if self.stage is None or not self.frames_on:
            return
        for item in povs.at_stage(self.stage):
            if self.key(item, self.stage) not in self.taken:
                self.capture(item, self.stage)

    def capture(self, item: povs.Pov, stage: str) -> None:
        """Un cadru al unui punct de vedere, plus rândurile lui: indexul și metadatele contractului.

        Mărimile de proces se citesc la fiecare cadru, dar nu intră în rând: `readings.jsonl` le
        scrie eșantionarea, iar cadrul are nevoie doar de eticheta piesei și de momentul ei.
        """
        sim_time = self.clock()
        name = f"{item.name}-{stage}.png"
        ts_ms = self.ts_ms()
        cycle = telemetry.cycle_id(self.cycle_index)
        path = self.session.path(telemetry.FRAMES_DIR, cycle, name)
        self.shots.write_pov(self.data, item, path)
        self.taken.add(self.key(item, stage))
        self.cycle_frames.append(name)
        self.session.frames.append(telemetry.frame_row(
            ts_ms=ts_ms, cycle=cycle, part=telemetry.part_id(self.cycle_index), pov=item.name,
            stage=stage, camera=item.camera, resolution=item.resolution,
            path=f"{telemetry.FRAMES_DIR}/{cycle}/{name}", sim_time_s=sim_time,
            machine_state=telemetry.machine_state_for(self.stage), label=self.label))
        if item.role is not None:
            self.session.snapshots.append(telemetry.qa_snapshot(
                ts_ms=ts_ms, cycle=cycle, part=telemetry.part_id(self.cycle_index),
                role=item.role, camera=item.camera or item.name, resolution=item.resolution,
                image_key=f"{telemetry.QA_SNAPSHOT_PREFIX}/{cycle}/{name}",
                machine_state=telemetry.machine_state_for(self.stage),
                machine=self.session.machine, is_synthetic_defect=self.synthetic_defect))

    @property
    def synthetic_defect(self) -> bool:
        """Adevărat când piesa ciclului are un defect cerut, nu unul ieșit din sămânță."""
        return str(self.label["defect"]) != "PASS"


def run_cycle(model: mujoco.MjModel, data: mujoco.MjData, recorder: Recorder,
              family: str, settle: int) -> float:
    """Un ciclu întreg: transport la liniile de preluare, apoi brațele, punct cu punct.

    Întoarce cât timp simulat a durat. Eșantioanele se iau pe tot parcursul, inclusiv când merg
    benzile goale, ca telemetria să acopere ciclul întreg, nu doar mișcarea brațelor.
    """
    started = float(data.time)
    parts = recorder.parts
    cell.bring_parts_to_pickup(model, data, parts, hook=recorder)
    for arm in arms.ARMS:
        tool = arms.tool_length(model, data, arm)
        for item in moves.route(arm, tool, family):
            recorder.enter(item.name)
            for stage, point in moves.stages(item):
                arms.reach_point(model, data, arm, point, item.approach, item.spin_deg,
                                 settle=settle, hook=recorder)
                if stage == "jos":
                    recorder.after_stage()
            recorder.leave()
    return float(data.time) - started


def build_parser() -> argparse.ArgumentParser:
    """Opțiunile sesiunii: câte cicluri, ce familie, unde se scrie."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cycles", type=int, default=2,
                        help="câte cicluri cuprinde sesiunea (implicit 2)")
    parser.add_argument("--seed", type=int, default=part.DEFAULT_SEED,
                        help="sămânța primului ciclu; ciclurile următoare cresc cu unu, deci "
                             "fiecare are altă piesă")
    parser.add_argument("--family", default=None, choices=part.family_names(),
                        help=f"familia pieselor din sesiune (implicit {part.family().name})")
    parser.add_argument("--out", default=None,
                        help="directorul sesiunii (implicit runs/<familie>-<sămânță>)")
    parser.add_argument("--sample-hz", type=float, default=10.0,
                        help="câte eșantioane pe secundă de timp simulat (implicit 10, ca "
                             "hot path-ul)")
    parser.add_argument("--start-ms", type=int, default=telemetry.SESSION_EPOCH_MS,
                        help="primul marcaj de timp epoch, în milisecunde (implicit unul fix, ca "
                             "două rulări să dea fișiere identice)")
    parser.add_argument("--demo-settle", type=int, default=300,
                        help="câți pași se așteaptă la fiecare punct de lucru")
    parser.add_argument("--frames", action=argparse.BooleanOptionalAction, default=True,
                        help="scrie cadrele și metadatele lor (implicit); `--no-frames` lasă doar "
                             "telemetria, mult mai repede")
    return parser


def run_session(args: argparse.Namespace) -> int:
    """Sesiunea întreagă: ciclurile, scrierea fișierelor și verificarea lor."""
    family = args.family or part.family().name
    directory = Path(args.out) if args.out else RUNS_DIR / f"{family}-{args.seed}"
    session = Session(directory=directory, name=directory.name, seed=args.seed, family=family,
                      sample_hz=args.sample_hz, start_ms=args.start_ms, cycles=args.cycles,
                      frames_on=args.frames)
    plan = povs.frames_per_cycle(family)
    print(f"Sesiune: {directory} | familia {family} | {args.cycles} cicluri | "
          f"{args.sample_hz:g} Hz | cadre: {'da' if args.frames else 'nu'}")
    print(f"  puncte de vedere: {', '.join(f'{item.name} ({stage})' for stage, item in plan)}")
    started = time.monotonic()
    failures: list[str] = []
    model: mujoco.MjModel | None = None
    for index in range(1, args.cycles + 1):
        seed = args.seed + index - 1
        model, metadata = scene.build_model(seed=seed, family=family)
        data = mujoco.MjData(model)
        scene.apply_nominal(model, data, metadata["placements"])
        scene.settle(model, data)
        machined = next(body for body, place in metadata["placements"].items()
                        if place["state"] == layout.STATE_MACHINED)
        label = dict(metadata["part"])
        shots = camera.Frames(model) if args.frames else None
        recorder = Recorder(model, data, cell.part_bodies(model), session, shots, label, machined)
        recorder.begin_cycle(index)
        duration = run_cycle(model, data, recorder, family, args.demo_settle)
        if shots is not None:
            shots.close()
        session.elapsed_s += duration
        session.labels.append(telemetry.cycle_record(
            index=index, seed=seed, label=label, sim_time_s=session.elapsed_s,
            samples=sum(1 for row in session.samples
                        if row["cycle_id"] == telemetry.cycle_id(index)),
            frames=recorder.cycle_frames))
        print(f"  {telemetry.cycle_id(index)}: {label['family']}/{label['defect']} "
              f"({label['verdict']}), sămânța {seed}, {len(recorder.cycle_frames)} cadre, "
              f"{duration:.2f} s simulat")

    totals = {
        "cycles": args.cycles,
        "samples": len(session.samples),
        "snapshots": len(session.snapshots),
        "frames": len(session.frames),
        "readings": len(session.readings),
        "sim_time_s": round(session.elapsed_s, 6),
        "wall_time_s": round(time.monotonic() - started, 3),
    }
    scenario = {
        "family": family,
        "seed": args.seed,
        "cycles": args.cycles,
        "sample_hz": args.sample_hz,
        "timestep_s": float(model.opt.timestep) if model is not None else 0.0,
        "mujoco_version": mujoco.__version__,
        "python": platform.python_version(),
        "start_ms": args.start_ms,
        "frames_on": args.frames,
        "device_id": telemetry.DEVICE_ID,
        "cell_id": telemetry.CELL_ID,
        "topic": telemetry.TELEMETRY_TOPIC,
        "povs": [item.name for item in povs.POVS],
        "machine": dataclasses.asdict(session.machine),
    }
    telemetry.write_jsonl(session.path(telemetry.SAMPLE_FILE), session.samples)
    telemetry.write_jsonl(session.path(telemetry.SNAPSHOT_FILE), session.snapshots)
    telemetry.write_jsonl(session.path(telemetry.FRAME_FILE), session.frames)
    telemetry.write_jsonl(session.path(telemetry.READING_FILE), session.readings)
    telemetry.write_json(session.path(telemetry.SESSION_FILE), telemetry.session_record(
        session=session.name, scenario=scenario, totals=totals, cycles=session.labels))

    for row in session.samples:
        failures.extend(f"eșantion: {item}" for item in telemetry.validate_sample(row))
    for row in session.snapshots:
        failures.extend(f"cadru: {item}" for item in telemetry.validate_snapshot(row))
    expected = len(plan) * args.cycles
    if args.frames and len(session.frames) != expected:
        failures.append(f"cadre lipsă: {len(session.frames)} din {expected}")

    print(f"\nSesiune scrisă în {directory}")
    for name in (telemetry.SAMPLE_FILE, telemetry.SNAPSHOT_FILE, telemetry.FRAME_FILE,
                 telemetry.READING_FILE, telemetry.SESSION_FILE):
        print(f"  {name:<18} {session.path(name).stat().st_size / 1024:8.1f} KB")
    print(f"  cadre            {totals['frames']} PNG, în {totals['cycles']} cicluri "
          f"({totals['wall_time_s']:.1f} s de ceas real)")
    print(f"  în contract      {totals['samples']} eșantioane, {totals['snapshots']} cadre de "
          f"inspecție, {totals['sim_time_s']:.2f} s simulat")

    if failures:
        print(f"\nSesiune: CĂZUTĂ ({len(failures)})")
        for failure in failures[:20]:
            print(f"  - {failure}")
        return 1
    print("\nSesiune: verde")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Punctul de intrare: opțiunile, apoi sesiunea."""
    return run_session(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
