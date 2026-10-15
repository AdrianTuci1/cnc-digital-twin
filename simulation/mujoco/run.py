#!/usr/bin/env python3
"""Sesiunea de colectare: cicluri întregi, cu telemetrie și cadre, scrise ca dataset.

O sesiune este o suită de cicluri pe aceeași celulă, fiecare cu piesa lui (altă sămânță), și scoate
exact ce cere antrenarea:

* `samples.jsonl` — eșantioane brute la ~10 Hz, cu câmpurile din `cnc.telemetry.v1.json`;
* `snapshots.jsonl` — metadatele cadrului care decide ruta piesei (rolul `sorting`), exact ce s-ar
  publica pe MQTT și în bucketul de date;
* `frames.jsonl` — indexul cadrelor: fiecare PNG al fiecărui punct de vedere, cu eticheta piesei
  (familie, defect, verdict) — pe el se antrenează clasificarea;
* `frames/` — PNG-urile, pe cicluri;
* `readings.jsonl` — mărimile care nu au coloană în contract (efort pe braț, forță de strângere,
  poziția și viteza piesei, contacte), ca diagnostic al sesiunii;
* `wear.jsonl` — sănătatea componentelor, aliniată cu eșantioanele;
* `events.jsonl` — etichetele: eșecurile componentelor și intervențiile de operator;
* `session.json` — parametrii, versiunile, totalurile și etichetele fiecărui ciclu.

Uzura este cea din `cncdt_mj/wear.py`: benzi, brațe și mașină se uzează cu timpul lor de lucru, iar
uzura se acumulează **peste cicluri** — scena se recompune la fiecare ciclu, modelul de uzură nu.
Mașina nu se animă: nu are axe în scenă, iar prelucrarea intră în dataset ca fereastră de timp cu
starea `CUTTING` și mărimile care ies din uzură. Cu `--rate` viața mașinii se comprimă, iar cu
`--inject` se pleacă de la o componentă deja uzată, ca sesiunile scurte să prindă tot lanțul.

Ciclul este cel din `view.py --demo`: benzile aduc materialul la liniile de preluare, brațul 1 ia
blocul, îl duce în universal și lasă roata pe banda 2, iar brațul 2 o sortează în locul familiei.
Prinderea nu este încă modelată (falcile doar ating piesa), deci piesa rămâne pe bandă: cadrele
arată piesa în falci, iar `docs/11-geaman-mujoco.md` §8 ține evidența a ce lipsește.

Se rulează din venviul geamănului (`simulation/mujoco/.venv`), singurul loc unde există `mujoco`
(ADR 0012): `make mj-record` ori direct `python run.py --cycles 2`.
"""

from __future__ import annotations

import argparse
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

from cncdt_mj import (arms, belt, camera, cell, moves, part, povs, scene,  # noqa: E402
                      sensors, telemetry, wear)

#: Unde se scriu sesiunile. Directorul e generat și ignorat de git: un ciclu de cinci familii
#: înseamnă mii de cadre, care n-au ce căuta în istoricul repo-ului.
RUNS_DIR = MUJOCO_DIR / "runs"

#: Cât durează prelucrarea unei piese, în secunde simulate. Nu animă nimic: este fereastra în care
#: motorul de uzură lasă scula să se uzeze, iar eșantioanele poartă starea `CUTTING`.
MACHINING_S = 12.0

#: Punctul de lucru în care materialul intră în mașină. Fereastra de așchiere se pune imediat după
#: el: brațul 1 a lăsat piesa în universal, iar până la așezarea pe banda 2 mașina taie.
MACHINE_TARGET = "cnc_chuck"

#: Ce se uzează cât merg benzile: numai ele. Brațele se adaugă când se mișcă, mașina când taie.
BELT_WORK = wear.Work(belts=wear.BELT_COMPONENTS)


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

    #: Uzura: aceeași peste toate ciclurile sesiunii, fiindcă scena se recompune, mașina nu.
    wear: wear.WearModel = field(default_factory=wear.WearModel)

    samples: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)
    readings: list[dict[str, Any]] = field(default_factory=list)
    wear_rows: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    labels: list[dict[str, Any]] = field(default_factory=list)

    #: Timpul simulat cumulat de ciclurile încheiate: fiecare ciclu are scena lui, care pornește de
    #: la zero, iar ceasul sesiunii curge peste ele.
    elapsed_s: float = 0.0

    def path(self, *parts: str) -> Path:
        """Un drum în directorul sesiunii."""
        return self.directory.joinpath(*parts)


class Recorder:
    """Ce se întâmplă la fiecare pas de simulare: eșantioane, cadre, etichete și uzură.

    Se instalează ca `hook` la mișcarea brațelor și la transport, deci vede fiecare pas al
    ciclului. Eșantioanele se iau la rata cerută (10 Hz implicit), cadrele la momentele din
    `povs.frames_per_cycle`, starea mașinii se schimbă cu faza ori cu punctul de lucru la care s-a
    ajuns, iar uzura avansează cu timpul de lucru al fiecărei componente.
    """

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, parts: tuple[str, ...],
                 session: Session, shots: camera.Frames | None,
                 label: Mapping[str, Any]) -> None:
        self.model = model
        self.data = data
        self.parts = parts
        self.session = session
        self.shots = shots
        self.label = dict(label)
        self.stage: str | None = None
        self.phase: str | None = None
        self.work = wear.Work()
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

    def begin_phase(self, phase: str | None, work: wear.Work) -> None:
        """Schimbă faza mașinii: ce stare are și cine se uzează de aici înainte.

        Fazele care nu ies din rutele brațelor sunt cele două din contract, `CUTTING` și `IDLE`
        (`wear.PHASE_CUTTING`, `wear.PHASE_TRANSPORT`); în rest starea vine din punctul de lucru.
        """
        self.phase = phase
        self.work = work

    def enter(self, stage: str) -> None:
        """S-a ajuns la un punct de lucru: de aici starea mașinii este a lui."""
        self.stage = stage

    def leave(self) -> None:
        """S-a terminat punctul de lucru: celula se întoarce în așteptare."""
        self.stage = None

    @property
    def machine_state(self) -> str:
        """Starea mașinii de acum: faza, dacă există, altfel punctul de lucru."""
        return self.phase or telemetry.machine_state_for(self.stage)

    @property
    def belt_scales(self) -> dict[str, float]:
        """Scala de viteză a fiecărei benzi, din uzura de acum."""
        return self.session.wear.belt_speed_scales()

    # ------------------------------------------------------------ la fiecare pas
    def __call__(self, model: mujoco.MjModel, data: mujoco.MjData) -> None:
        """Un pas de simulare: uzura, apoi eșantionul, dacă a venit rândul lui."""
        self.steps += 1
        for failure in self.session.wear.advance(float(model.opt.timestep), self.work):
            self.session.events.append(self.failure_row(failure))
        if self.steps % self.steps_per_sample == 0:
            self.write_sample()

    def clock(self) -> float:
        """Timpul simulat al sesiunii: ciclurile încheiate plus ceasul ciclului de acum."""
        return self.session.elapsed_s + float(self.data.time)

    def ts_ms(self) -> int:
        """Marcajul de timp epoch de acum, pornind de la ceasul sesiunii."""
        return self.session.start_ms + int(round(self.clock() * 1000))

    # ------------------------------------------------------------ ce se scrie
    def failure_row(self, failure: wear.Failure) -> dict[str, Any]:
        """Un eșec al motorului de uzură, ca rând de etichetă în `events.jsonl`."""
        return telemetry.event_row(
            ts_ms=self.ts_ms(), sim_time_s=self.clock(), cycle=telemetry.cycle_id(self.cycle_index),
            event="failure", kind=failure.kind, component=failure.component,
            detail=f"sănătate {failure.health:.3f} ({self.session.wear.severity(failure.component)})",
            severity=self.session.wear.severity(failure.component), health=failure.health,
            duration_s=failure.duration_s)

    def write_sample(self) -> None:
        """Un eșantion brut, cu efortul articulațiilor măsurat în scenă și uzura aplicată peste."""
        machine = self.session.wear.reading(cutting=self.phase == wear.PHASE_CUTTING)
        values = sensors.reading(self.model, self.data, self.parts,
                                effort_scale=self.session.wear.arm_effort_scales(),
                                belt_speed_mps=(
                                    belt.BELT_SURFACE_SPEED_MPS
                                    * self.session.wear.belt_speed_scale()))
        self.session.samples.append(telemetry.sample(
            seq=len(self.session.samples),
            ts_ms=self.ts_ms(),
            machine_state=self.machine_state,
            cycle=telemetry.cycle_id(self.cycle_index),
            part=telemetry.part_id(self.cycle_index),
            joint_effort=values["joint_effort_nm"],
            machine=machine,
            is_synthetic_defect=self.synthetic_defect,
        ))
        self.session.readings.append(sensors.reading_row(values))
        self.session.wear_rows.append(telemetry.wear_row(
            ts_ms=self.ts_ms(), sim_time_s=self.clock(),
            cycle=telemetry.cycle_id(self.cycle_index), machine_state=self.machine_state,
            snapshot=self.session.wear.snapshot()))

    # ------------------------------------------------------------ cadrele
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
                machine_state=telemetry.machine_state_for(self.stage)))

    @property
    def synthetic_defect(self) -> bool:
        """Adevărat când piesa ciclului are un defect cerut, nu unul ieșit din sămânță.

        Câmpul intră doar în eșantionul de telemetrie, ca etichetă de antrenare. Cadrul de
        inspecție rămâne fără el, fiindcă acela pornește inferența.
        """
        return str(self.label["defect"]) != "PASS"


def machining(model: mujoco.MjModel, data: mujoco.MjData, recorder: Recorder,
              seconds: float) -> None:
    """Fereastra de așchiere: mașina nu se animă, doar ceasul și uzura merg înainte.

    Piesa nu se schimbă sub ochii noștri — scena are două corpuri, blocul brut pe banda 1 și piesa
    mașinată pe banda 2 — deci prelucrarea nu are ce mișca. Ce are, în schimb, este timpul de lucru
    al sculei, al axului și al răcirii, iar de acolo ies mărimile din contract și eticheta
    `CUTTING`: pe fereastra asta se învață că scula uzată încarcă axul și scade calitatea.
    """
    recorder.begin_phase(wear.PHASE_CUTTING, wear.Work(cutting=True))
    steps = max(1, int(round(seconds / float(model.opt.timestep))))
    for _ in range(steps):
        cell.apply_belt_forces(model, data, recorder.belt_scales, recorder.parts)
        mujoco.mj_step(model, data)
        recorder(model, data)


def run_cycle(model: mujoco.MjModel, data: mujoco.MjData, recorder: Recorder,
              family: str, settle: int, machining_s: float = MACHINING_S) -> float:
    """Un ciclu întreg: transport la liniile de preluare, apoi brațele, punct cu punct.

    Întoarce cât timp simulat a durat. Eșantioanele se iau pe tot parcursul, inclusiv când merg
    benzile goale, ca telemetria să acopere ciclul întreg, nu doar mișcarea brațelor. Fiecare
    interval are timpul lui de lucru: benzile cară cât merg, brațul când se mișcă, mașina cât taie.

    Viteza benzilor vine din uzura lor, pe fiecare bandă: una uzată cară mai încet, iar una care
    alunecă stă pe loc, deci piesa nu mai ajunge la linia de preluare și bugetul de pași se
    consumă. Scena nu are altă cale prin care uzura să ajungă la piese decât prin forța benzii.
    """
    started = float(data.time)
    parts = recorder.parts
    recorder.begin_phase(wear.PHASE_TRANSPORT, BELT_WORK)
    cell.bring_parts_to_pickup(model, data, parts, hook=recorder,
                               speed_scale=recorder.belt_scales)
    for arm in arms.ARMS:
        tool = arms.tool_length(model, data, arm)
        for item in moves.route(arm, tool, family):
            recorder.begin_phase(None, wear.Work(belts=wear.BELT_COMPONENTS, arms=(arm,)))
            recorder.enter(item.name)
            for stage, point in moves.stages(item):
                arms.reach_point(model, data, arm, point, item.approach, item.spin_deg,
                                 settle=settle, hook=recorder)
                if stage == "jos":
                    recorder.after_stage()
            recorder.leave()
            if item.name == MACHINE_TARGET:
                machining(model, data, recorder, machining_s)
    recorder.begin_phase(None, wear.Work())
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
    parser.add_argument("--machining-s", type=float, default=MACHINING_S,
                        help="cât durează prelucrarea unei piese, în secunde simulate (implicit "
                             f"{MACHINING_S:g}): fereastra cu starea CUTTING în care se uzează "
                             "scula, axul și răcirea")
    parser.add_argument("--rate", type=float, default=1.0,
                        help="compresia vieții mașinii: 1 este uzura nominală, 20 o termină de 20 "
                             "de ori mai iute (implicit 1). Nu atinge ceasul, doar sănătatea")
    parser.add_argument("--inject", action="append", default=[], metavar="COMPONENTĂ[:CÂT]",
                        help="pornește sesiunea cu o componentă deja uzată, cu cantitatea dată "
                             "din 1 (implicit 0.25); repetabil. Componentele sunt: "
                             + ", ".join(wear.component_names()))
    parser.add_argument("--frames", action=argparse.BooleanOptionalAction, default=True,
                        help="scrie cadrele și metadatele lor (implicit); `--no-frames` lasă doar "
                             "telemetria, mult mai repede")
    return parser


def run_session(args: argparse.Namespace) -> int:
    """Sesiunea întreagă: ciclurile, scrierea fișierelor și verificarea lor."""
    family = args.family or part.family().name
    directory = Path(args.out) if args.out else RUNS_DIR / f"{family}-{args.seed}"
    try:
        injections = wear.parse_injections(args.inject)
    except ValueError as error:
        print(f"Injecție nevalidă: {error}")
        return 1
    wear_model = wear.WearModel(seed=args.seed, rate=args.rate)
    session = Session(directory=directory, name=directory.name, seed=args.seed, family=family,
                      sample_hz=args.sample_hz, start_ms=args.start_ms, cycles=args.cycles,
                      frames_on=args.frames, wear=wear_model)
    plan = povs.frames_per_cycle(family)
    print(f"Sesiune: {directory} | familia {family} | {args.cycles} cicluri | "
          f"{args.sample_hz:g} Hz | cadre: {'da' if args.frames else 'nu'}")
    print(f"  puncte de vedere: {', '.join(f'{item.name} ({stage})' for stage, item in plan)}")
    print(f"  uzură: rata {args.rate:g}×, așchiere {args.machining_s:g} s pe ciclu"
          + (f", injecții: {', '.join(args.inject)}" if args.inject else ""))
    for action in wear_model.apply_actions(injections):
        # Injecțiile se fac înainte de primul ciclu, la ceasul zero al sesiunii: poartă
        # identificatorul ciclului 1, fiindcă acela este ciclul pe care îl modelează.
        session.events.append(telemetry.event_row(
            ts_ms=args.start_ms, sim_time_s=wear_model.now_s,
            cycle=telemetry.cycle_id(1), event="action", kind=action.command,
            component=action.component, detail=action.detail))
    started = time.monotonic()
    failures: list[str] = []
    model: mujoco.MjModel | None = None
    for index in range(1, args.cycles + 1):
        seed = args.seed + index - 1
        model, metadata = scene.build_model(seed=seed, family=family)
        data = mujoco.MjData(model)
        scene.apply_nominal(model, data, metadata["placements"])
        scene.settle(model, data)
        label = dict(metadata["part"])
        shots = camera.Frames(model) if args.frames else None
        recorder = Recorder(model, data, cell.part_bodies(model), session, shots, label)
        recorder.begin_cycle(index)
        duration = run_cycle(model, data, recorder, family, args.demo_settle, args.machining_s)
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
        "wear_rows": len(session.wear_rows),
        "events": len(session.events),
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
        "wear": {
            "seed": args.seed,
            "rate": args.rate,
            "machining_s": args.machining_s,
            "injections": list(args.inject),
            "components": wear_model.component_table(),
            "health_end": {name: round(value, 6) for name, value in wear_model.health.items()},
        },
    }
    telemetry.write_jsonl(session.path(telemetry.SAMPLE_FILE), session.samples)
    telemetry.write_jsonl(session.path(telemetry.SNAPSHOT_FILE), session.snapshots)
    telemetry.write_jsonl(session.path(telemetry.FRAME_FILE), session.frames)
    telemetry.write_jsonl(session.path(telemetry.READING_FILE), session.readings)
    telemetry.write_jsonl(session.path(telemetry.WEAR_FILE), session.wear_rows)
    telemetry.write_jsonl(session.path(telemetry.EVENT_FILE), session.events)
    telemetry.write_json(session.path(telemetry.SESSION_FILE), telemetry.session_record(
        session=session.name, scenario=scenario, totals=totals, cycles=session.labels))

    for row in session.samples:
        failures.extend(f"eșantion: {item}" for item in telemetry.validate_sample(row))
    for row in session.snapshots:
        failures.extend(f"cadru: {item}" for item in telemetry.validate_snapshot(row))
    expected = len(plan) * args.cycles
    if args.frames and len(session.frames) != expected:
        failures.append(f"cadre lipsă: {len(session.frames)} din {expected}")
    if len(session.wear_rows) != len(session.samples):
        failures.append(f"rânduri de uzură lipsă: {len(session.wear_rows)} din "
                        f"{len(session.samples)}")

    print(f"\nSesiune scrisă în {directory}")
    for name in (telemetry.SAMPLE_FILE, telemetry.SNAPSHOT_FILE, telemetry.FRAME_FILE,
                 telemetry.READING_FILE, telemetry.WEAR_FILE, telemetry.EVENT_FILE,
                 telemetry.SESSION_FILE):
        print(f"  {name:<18} {session.path(name).stat().st_size / 1024:8.1f} KB")
    print(f"  cadre            {totals['frames']} PNG, în {totals['cycles']} cicluri "
          f"({totals['wall_time_s']:.1f} s de ceas real)")
    print(f"  în contract      {totals['samples']} eșantioane, {totals['snapshots']} cadre de "
          f"inspecție, {totals['sim_time_s']:.2f} s simulat")

    def health(*names: str) -> float:
        """Sănătatea cea mai mică dintre componentele date: cea care dă tonul celulei."""
        return min(wear_model.health_of(name) for name in names)

    print(f"  uzură la final   scula h={health('cnc_tool'):.3f} | articulații "
          f"h={health(*wear.ARM_COMPONENTS):.3f} | benzi h={health(*wear.BELT_COMPONENTS):.3f} | "
          f"calitate {wear_model.surface_quality():.3f} | {wear_model.temp_c:.1f} °C")
    print("  componente       " + ", ".join(
        f"{name} {wear_model.severity(name)}" for name in wear.component_names()))
    print(f"  etichete         {totals['events']} evenimente "
          f"({sum(1 for row in session.events if row['event'] == 'failure')} eșecuri, "
          f"{sum(1 for row in session.events if row['event'] == 'action')} acțiuni)")

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
