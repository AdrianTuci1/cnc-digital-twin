"""Teste pentru corpusul de antrenare al geamănului: așezarea în bucket, manifestul, verificările.

Urcarea propriu-zisă (`simulation/mujoco/publish.py`) are nevoie de credențiale AWS, dar hotărârea
*ce* se urcă și *sub ce chei* stă într-un modul pur, `cncdt_mj/export.py`, care se citește din
`.venv`-ul proiectului. Aici se apără exact cele trei lucruri care ar strica un corpus: cheile sub
care ajung sesiunile, manifestul din care învață jobul de antrenare unde sunt fișierele și
refuzul de a urca o colectare pe jumătate.

Sesiunile de test se scriu cu aceleași funcții ca ale geamănului (`telemetry.write_jsonl`,
`telemetry.session_record`), ca verificările să fie apărate pe formatul real.

Urcarea completă, cu bucket: `make mj-collect`.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

import pytest

from cncdt_mj import export, povs, telemetry

#: Momentul de referință al testelor, într-o zonă care **nu** este UTC: numele rulării și ziua
#: partiției trebuie să iasă în UTC, oricare ar fi ceasul local.
MOMENT = datetime(2026, 10, 15, 13, 11, 12, tzinfo=timezone(timedelta(hours=3)))

RUN_PREFIX = f"{export.DATASETS_PREFIX}/dt=2026-10-15/run-20261015T101112"


def _png(path: Path) -> None:
    """Un PNG de unică folosință: verificarea numără fișiere, nu le deschide."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"cadru")


def _write_session(directory: Path, *, family: str = "wheel_100", seed: int = 100,
                   cycles: int = 2, frames_per_cycle: int = 2,
                   defects: Sequence[str] = ("PASS", "POROSITY")) -> Path:
    """O sesiune completă pe disc, în formatul scris de `run.py`.

    Fiecare ciclu primește câte un defect din listă, ca manifestul să aibă ce număra, iar fiecare
    eșantion primește rândul lui de uzură: alinierea celor două este una dintre verificări.
    """
    samples: list[dict] = []
    wear_rows: list[dict] = []
    readings: list[dict] = []
    snapshots: list[dict] = []
    frames: list[dict] = []
    labels: list[dict] = []
    for index in range(1, cycles + 1):
        cycle = telemetry.cycle_id(index)
        part = telemetry.part_id(index)
        defect = defects[(index - 1) % len(defects)]
        label = {
            "part_id": f"part-{index:08d}",
            "family": family,
            "defect": defect,
            "verdict": "PASS" if defect == "PASS" else "FAIL",
        }
        names: list[str] = []
        for shot, shot_pov in enumerate(povs.POVS[:frames_per_cycle]):
            name = f"{shot_pov.name}-belt1_pick.png"
            _png(directory / telemetry.FRAMES_DIR / cycle / name)
            names.append(name)
            frames.append(telemetry.frame_row(
                ts_ms=telemetry.SESSION_EPOCH_MS, cycle=cycle, part=part, pov=shot_pov.name,
                stage="belt1_pick", camera=shot_pov.camera, resolution=shot_pov.resolution,
                path=f"{telemetry.FRAMES_DIR}/{cycle}/{name}", sim_time_s=index + shot * 0.1,
                machine_state="LOADING", label=label))
            if shot_pov.role is not None:
                snapshots.append(telemetry.qa_snapshot(
                    ts_ms=telemetry.SESSION_EPOCH_MS, cycle=cycle, part=part,
                    role=shot_pov.role, camera=shot_pov.camera or shot_pov.name,
                    resolution=shot_pov.resolution,
                    image_key=f"{telemetry.QA_SNAPSHOT_PREFIX}/{cycle}/{name}",
                    machine_state="LOADING"))
        for seq in range(2):
            samples.append(telemetry.sample(
                seq=seq, ts_ms=telemetry.SESSION_EPOCH_MS, machine_state="LOADING",
                cycle=cycle, part=part, joint_effort=4.2,
                machine=telemetry.MachineReading(spindle_load=0.3, surface_quality=0.95)))
            # Rândul de diagnostic al sesiunii îl scrie `sensors.reading_row`, care are nevoie de
            # motorul de simulare; aici contează doar că fișierul are conținut.
            readings.append({"ts_ms": telemetry.SESSION_EPOCH_MS, "sim_time_s": index + seq * 0.1,
                             "cycle_id": cycle, "joint_effort_nm": 4.2, "grip_force_n": 12.0})
            wear_rows.append(telemetry.wear_row(
                ts_ms=telemetry.SESSION_EPOCH_MS, sim_time_s=index + seq * 0.1, cycle=cycle,
                machine_state="LOADING", snapshot={"belt_in": 0.9, "arm1": 0.85}))
        labels.append(telemetry.cycle_record(
            index=index, seed=seed, label=label, sim_time_s=float(index), samples=2, frames=names))

    telemetry.write_jsonl(directory / telemetry.SAMPLE_FILE, samples)
    telemetry.write_jsonl(directory / telemetry.WEAR_FILE, wear_rows)
    telemetry.write_jsonl(directory / telemetry.FRAME_FILE, frames)
    telemetry.write_jsonl(directory / telemetry.SNAPSHOT_FILE, snapshots)
    telemetry.write_jsonl(directory / telemetry.READING_FILE, readings)
    telemetry.write_json(directory / telemetry.SESSION_FILE, telemetry.session_record(
        session=directory.name,
        scenario={
            "family": family,
            "seed": seed,
            "cycles": cycles,
            "sample_hz": 10.0,
            "timestep_s": 0.002,
            "mujoco_version": "3.15.0",
            "python": "3.14.6",
            "wear": {"seed": seed, "rate": 1.0, "components": ["belt_in", "arm1"]},
        },
        totals={
            "cycles": cycles,
            "samples": len(samples),
            "frames": len(frames),
            "sim_time_s": float(cycles),
        },
        cycles=labels))
    # Etichetele de eșec și de intervenție: fișier legitim gol când nu s-a stricat nimic.
    telemetry.write_jsonl(directory / telemetry.EVENT_FILE, [])
    return directory


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """O colectare cu două familii, așezate ca la `make mj-collect`: rulare → familie-sămânță."""
    root = tmp_path / "runs" / "run-20261015T101112"
    _write_session(root / "wheel_100-100", family="wheel_100", seed=100)
    _write_session(root / "bushing_68-101", family="bushing_68", seed=101, defects=("PASS",))
    return root


# ---------------------------------------------------------------- numele rulării


def test_run_name_and_date_are_utc() -> None:
    """Numele rulării și ziua partiției ies în UTC, din orice ceas local."""
    assert export.run_name(MOMENT) == "run-20261015T101112"
    assert export.run_date(MOMENT) == "2026-10-15"


# ---------------------------------------------------------------- sesiunile de pe disc


def test_session_directories_finds_every_session_below(run_dir: Path) -> None:
    """Se caută recursiv: sesiunile stau sub directorul rulării, familie cu familie."""
    found = export.session_directories(run_dir)
    assert [path.name for path in found] == ["bushing_68-101", "wheel_100-100"]


def test_session_directories_on_a_missing_run(tmp_path: Path) -> None:
    """Un director care nu există nu are sesiuni, fără să ridice eroare."""
    assert export.session_directories(tmp_path / "runs" / "niciunul") == ()


def test_session_entry_reads_totals_and_defects(run_dir: Path) -> None:
    """Sesiunea se citește din `session.json`, iar defectele se numără din cicluri."""
    directory = run_dir / "wheel_100-100"
    entry = export.session_entry(directory)
    assert entry.name == "wheel_100-100"
    assert entry.family == "wheel_100"
    assert entry.seed == 100
    assert entry.cycles == 2
    assert entry.samples == 4
    assert entry.frames == 4
    assert entry.sim_time_s == 2.0
    assert entry.bytes == export.directory_bytes(directory) > 0
    assert entry.defects == {"PASS": 1, "POROSITY": 1}


# ---------------------------------------------------------------- cheile din bucket


def test_plan_places_every_session_under_the_run(run_dir: Path) -> None:
    """Cheile: prefix de corpus, zi de partiție, rulare, apoi directorul familiei întreg."""
    plan = export.plan_export(run_dir, "exemplu-data-lake", when=MOMENT)
    assert plan.ok
    assert plan.prefix == RUN_PREFIX
    assert [item.key_prefix(plan.prefix) for item in plan.sessions] == [
        f"{RUN_PREFIX}/bushing_68-101", f"{RUN_PREFIX}/wheel_100-100"]
    assert plan.bytes == sum(item.bytes for item in plan.sessions)


def test_command_uploads_the_session_directory_recursively(run_dir: Path) -> None:
    """Urcarea este `aws s3 cp` pe directorul sesiunii, recursiv: AWS CLI, fără SDK."""
    plan = export.plan_export(run_dir, "exemplu-data-lake", when=MOMENT)
    session = plan.sessions[0]
    assert plan.command(session) == [
        "aws", "s3", "cp", str(session.directory),
        f"s3://exemplu-data-lake/{RUN_PREFIX}/{session.name}", "--recursive"]


def test_prefix_and_run_name_can_be_forced(run_dir: Path) -> None:
    """O colectare se poate urca sub alt prefix ori cu alt nume de rulare."""
    plan = export.plan_export(run_dir, "exemplu-data-lake", dt="2026-01-02", run="run-test",
                              prefix="datasets/mujoco/")
    assert plan.prefix == "datasets/mujoco/dt=2026-01-02/run-test"


# ---------------------------------------------------------------- manifestul


def test_manifest_says_what_the_corpus_holds(run_dir: Path) -> None:
    """Manifestul este contractul cu antrenarea: fișiere, sesiuni, totaluri, etichete, sursă."""
    plan = export.plan_export(run_dir, "exemplu-data-lake", when=MOMENT)
    manifest = plan.manifest(created_ms=1_760_000_000_000,
                             extra={"git": {"revision": "abc1234"}})
    assert manifest["corpus"] == export.DATASETS_PREFIX
    assert manifest["bucket"] == "exemplu-data-lake"
    assert manifest["prefix"] == RUN_PREFIX + "/"
    assert manifest["created_ms"] == 1_760_000_000_000
    assert manifest["files"]["samples"] == telemetry.SAMPLE_FILE
    assert manifest["files"]["wear"] == telemetry.WEAR_FILE
    assert manifest["frames_dir"] == telemetry.FRAMES_DIR
    assert [item["name"] for item in manifest["sessions"]] == [
        "bushing_68-101", "wheel_100-100"]
    assert manifest["sessions"][0]["prefix"] == f"{RUN_PREFIX}/bushing_68-101/"
    assert manifest["totals"] == {"sessions": 2, "samples": 8, "frames": 8,
                                  "bytes": plan.bytes}
    assert manifest["labels"]["families"] == ["bushing_68", "wheel_100"]
    assert manifest["labels"]["defects"] == {"PASS": 3, "POROSITY": 1}
    assert manifest["source"]["mujoco_version"] == "3.15.0"
    assert manifest["source"]["schemas"]["samples"] == "1.0.0"
    assert manifest["problems"] == []
    assert manifest["git"] == {"revision": "abc1234"}


# ---------------------------------------------------------------- verificările


def test_check_session_accepts_a_complete_session(run_dir: Path) -> None:
    """O sesiune întreagă nu are nicio problemă, cu `events.jsonl` gol cu voie."""
    assert export.check_session(run_dir / "wheel_100-100") == []


def test_check_session_reports_a_missing_file(run_dir: Path) -> None:
    """Un fișier lipsă oprește urcarea: un corpus cu găuri se vede la antrenare, scump."""
    directory = run_dir / "wheel_100-100"
    (directory / telemetry.READING_FILE).unlink()
    assert export.check_session(directory) == [
        f"{directory.name}: lipsește {telemetry.READING_FILE}"]


def test_check_session_reports_an_empty_file(run_dir: Path) -> None:
    """Zero octeți la eșantioane înseamnă colectare căzută, nu colectare fără evenimente."""
    directory = run_dir / "wheel_100-100"
    (directory / telemetry.SAMPLE_FILE).write_text("", encoding="utf-8")
    assert export.check_session(directory) == [
        f"{directory.name}: {telemetry.SAMPLE_FILE} este gol"]


def test_check_session_reports_missing_frames(run_dir: Path) -> None:
    """Numărul de PNG-uri trebuie să fie cel declarat: un cadru pierdut strică eticheta."""
    directory = run_dir / "wheel_100-100"
    first = min((directory / telemetry.FRAMES_DIR).rglob("*.png"))
    first.unlink()
    problems = export.check_session(directory)
    assert problems == [f"{directory.name}: 3 cadre pe disc, 4 în session.json"]


def test_check_session_reports_a_broken_frame_index(run_dir: Path) -> None:
    """Indexul cadrelor și totalul din `session.json` trebuie să spună aceeași poveste."""
    directory = run_dir / "wheel_100-100"
    rows = (directory / telemetry.FRAME_FILE).read_text(encoding="utf-8").splitlines()
    (directory / telemetry.FRAME_FILE).write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
    problems = export.check_session(directory)
    assert problems == [
        f"{directory.name}: 3 cadre în {telemetry.FRAME_FILE}, 4 în {telemetry.SESSION_FILE}"]


def test_check_session_refuses_a_session_without_cameras(tmp_path: Path) -> None:
    """Fără cadre nu există dataset de viziune, chiar dacă senzorii sunt complet."""
    directory = _write_session(tmp_path / "wheel_60-100", family="wheel_60", cycles=1,
                               frames_per_cycle=0)
    problems = export.check_session(directory)
    assert f"{directory.name}: {telemetry.FRAME_FILE} este gol" in problems
    assert f"{directory.name}: {telemetry.SNAPSHOT_FILE} este gol" in problems


def test_check_session_reports_wear_rows_out_of_step(run_dir: Path) -> None:
    """Uzura se scrie la fiecare eșantion; dacă rândurile nu se potrivesc, etichetele sunt false."""
    directory = run_dir / "wheel_100-100"
    rows = (directory / telemetry.WEAR_FILE).read_text(encoding="utf-8").splitlines()
    (directory / telemetry.WEAR_FILE).write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
    problems = export.check_session(directory)
    assert problems == [f"{directory.name}: 3 rânduri de uzură la 4 eșantioane"]


def test_check_session_reports_an_unreadable_session(run_dir: Path) -> None:
    """Un `session.json` tăiat la scriere se vede ca problemă, nu ca excepție."""
    directory = run_dir / "wheel_100-100"
    (directory / telemetry.SESSION_FILE).write_text('{"session": "wheel_100-100"',
                                                    encoding="utf-8")
    problems = export.check_session(directory)
    assert len(problems) == 1 and problems[0].startswith(
        f"{directory.name}: session.json nu se poate citi")


def test_plan_refuses_a_corpus_with_problems(run_dir: Path) -> None:
    """Sesiunea cu probleme este scoasă din plan, iar planul întreg nu se mai urcă."""
    (run_dir / "wheel_100-100" / telemetry.WEAR_FILE).unlink()
    plan = export.plan_export(run_dir, "exemplu-data-lake", when=MOMENT)
    assert not plan.ok
    assert [item.name for item in plan.sessions] == ["bushing_68-101"]
    assert plan.problems == ("wheel_100-100: lipsește wear.jsonl",)


def test_plan_on_an_empty_run_reports_nothing_to_upload(tmp_path: Path) -> None:
    """O rulare fără sesiuni spune explicit că nu are ce urca."""
    empty = tmp_path / "runs"
    empty.mkdir()
    plan = export.plan_export(empty, "exemplu-data-lake", when=MOMENT)
    assert not plan.ok
    assert plan.sessions == ()
    assert plan.problems == (f"nicio sesiune în {empty}: nimic de urcat",)


def test_source_lands_in_the_manifest_as_json(run_dir: Path) -> None:
    """Sursa din manifest se serializează: jobul de antrenare o citește, nu o ghicește."""
    plan = export.plan_export(run_dir, "exemplu-data-lake", when=MOMENT)
    manifest = plan.manifest(created_ms=0)
    written = json.loads(json.dumps(manifest, ensure_ascii=False))
    assert written["source"]["wear"]["components"] == ["belt_in", "arm1"]
    assert written["source"]["tool"] == "simulation/mujoco/run.py"
