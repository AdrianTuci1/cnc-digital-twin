"""Corpusul de antrenare: ce se urcă din sesiunile geamănului și cum se numesc cheile.

O sesiune scrisă de `run.py` este un corpus complet: **senzorii** în `samples.jsonl` (contractul de
telemetrie), **camerele** în `frames/` și `frames.jsonl` (fiecare PNG cu eticheta piesei din el),
plus etichetele de uzură și de evenimente. Aici se hotărăște unde ajunge fiecare lucru în bucket,
ce scrie manifestul corpusului și ce este de verificat înainte de urcare.

Modul **pur** (fără `mujoco`, fără `boto3`, fără apeluri de rețea): planul se poate scrie și
verifica din `.venv`-ul proiectului, iar urcarea propriu-zisă este treaba lui `publish.py`.

Așezarea în bucket, sub bucketul data lake din `docs/01-architecture.md`:

```
datasets/mujoco/dt=<zi>/<rulare>/manifest.json
datasets/mujoco/dt=<zi>/<rulare>/<familie>-<sămânță>/{samples.jsonl, frames.jsonl, …, frames/}
```

`datasets/` este un prefix propriu, nu `raw/`. `raw/telemetry/` este Parquet de ferestre, scris de
Firehose și citit de tabelul Glue din Terraform, iar `raw/qa-snapshots/` este calea live: fiecare
scriere acolo cheamă Lambda-ul de inspecție. Un corpus de antrenament nu are ce căuta nici în
tabel, nici în declanșator: se citește direct, cu `cncdt_ml.offline` ori cu `build_features.py`,
care știu să deschidă JSON Lines.

Partiționarea pe `dt=` păstrează convenția din `curated/qa-verdicts/dt=<zi>/`, iar numele rulării
(`run-<UTC>`) face ca două colectări să nu se amestece niciodată.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import telemetry

#: Prefixul corpusului de antrenament al geamănului, sub bucketul data lake.
DATASETS_PREFIX = "datasets/mujoco"

#: Fișierele care intră în corpus, pe lângă cadre: exact ce scrie `run.py`.
CORPUS_FILES: tuple[str, ...] = (
    telemetry.SAMPLE_FILE,
    telemetry.SNAPSHOT_FILE,
    telemetry.FRAME_FILE,
    telemetry.READING_FILE,
    telemetry.WEAR_FILE,
    telemetry.EVENT_FILE,
    telemetry.SESSION_FILE,
)

#: Fișierul de manifest, la rădăcina rulării: ce conține corpusul și cum a fost generat.
MANIFEST_FILE = "manifest.json"


@dataclass(frozen=True)
class SessionEntry:
    """O sesiune de familie din corpus, cu ce s-a adunat în ea."""

    name: str
    directory: Path
    family: str
    seed: int
    cycles: int
    samples: int
    frames: int
    sim_time_s: float
    bytes: int
    defects: Mapping[str, int] = field(default_factory=dict)

    def key_prefix(self, prefix: str) -> str:
        """Prefixul de chei al sesiunii: întregul ei director urcă sub el."""
        return f"{prefix.rstrip('/')}/{self.name}"

    def record(self, prefix: str) -> dict[str, Any]:
        """Rândul din manifest pentru sesiunea asta."""
        return {
            "name": self.name,
            "prefix": self.key_prefix(prefix) + "/",
            "family": self.family,
            "seed": self.seed,
            "cycles": self.cycles,
            "samples": self.samples,
            "frames": self.frames,
            "sim_time_s": round(self.sim_time_s, 6),
            "bytes": self.bytes,
            "defects": dict(self.defects),
        }


@dataclass(frozen=True)
class ExportPlan:
    """Ce urcă: bucketul, prefixul rulării, sesiunile și ce s-a găsit în neregulă."""

    bucket: str
    prefix: str
    sessions: tuple[SessionEntry, ...]
    problems: tuple[str, ...] = ()
    source: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Se poate urca? Un corpus cu probleme nu se urcă: se repară și se reia."""
        return bool(self.sessions) and not self.problems

    @property
    def bytes(self) -> int:
        """Cât urcă, în octeți, fără manifest."""
        return sum(item.bytes for item in self.sessions)

    def command(self, session: SessionEntry) -> list[str]:
        """Comanda de urcare a unei sesiuni: directorul întreg, recursiv."""
        return ["aws", "s3", "cp", str(session.directory),
                f"s3://{self.bucket}/{session.key_prefix(self.prefix)}", "--recursive"]

    def manifest(self, *, created_ms: int, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Manifestul corpusului: ce este în el, cu ce s-a generat și de unde se citește.

        Un job de antrenare are nevoie de trei lucruri: unde sunt fișierele, ce etichete are
        fiecare sesiune și cu ce versiune de contract au fost scrise. Toate trei stau aici, ca
        antrenarea să nu ghicească din numele de directoare.
        """
        defects: dict[str, int] = {}
        families: list[str] = []
        for item in self.sessions:
            for defect, count in item.defects.items():
                defects[defect] = defects.get(defect, 0) + count
            if item.family not in families:
                families.append(item.family)
        manifest: dict[str, Any] = {
            "corpus": DATASETS_PREFIX,
            "bucket": self.bucket,
            "prefix": self.prefix + "/",
            "created_ms": int(created_ms),
            "files": {Path(name).stem: name for name in CORPUS_FILES},
            "frames_dir": telemetry.FRAMES_DIR,
            "sessions": [item.record(self.prefix) for item in self.sessions],
            "totals": {
                "sessions": len(self.sessions),
                "samples": sum(item.samples for item in self.sessions),
                "frames": sum(item.frames for item in self.sessions),
                "bytes": self.bytes,
            },
            "labels": {
                "families": families,
                "defects": dict(sorted(defects.items())),
            },
            "source": dict(self.source),
            "problems": list(self.problems),
        }
        if extra:
            manifest.update(dict(extra))
        return manifest


def run_name(when: datetime) -> str:
    """Numele rulării, în UTC: două colectări nu se pot amesteca sub același prefix."""
    return "run-" + when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S")


def run_date(when: datetime) -> str:
    """Ziua partiției, în UTC, în forma din `curated/qa-verdicts/dt=<zi>/`."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d")


def session_directories(run_dir: Path) -> tuple[Path, ...]:
    """Sesiunile de sub directorul dat, oricât de adânc: cele care au `session.json`.

    Se caută recursiv fiindcă o colectare poate scrie într-un singur director de rulare, iar
    `make mj-record` scrie direct în `runs/`, familie cu familie.
    """
    if not run_dir.is_dir():
        return ()
    found = [path for path in sorted(run_dir.rglob(telemetry.SESSION_FILE)) if path.is_file()]
    return tuple(path.parent for path in found)


def session_entry(directory: Path) -> SessionEntry:
    """Citește o sesiune de pe disc: ce declară `session.json` și cât ocupă pe disc."""
    record = json.loads(directory.joinpath(telemetry.SESSION_FILE).read_text(encoding="utf-8"))
    totals = record.get("totals", {})
    cycles = record.get("cycles", [])
    defects: dict[str, int] = {}
    for item in cycles:
        # Eticheta ciclului stă la nivelul de sus al rândului (`telemetry.cycle_record`): familia,
        # defectul și verdictul piesei, fără un sub-obiect `label`.
        defect = str(item.get("defect", ""))
        if defect:
            defects[defect] = defects.get(defect, 0) + 1
    return SessionEntry(
        name=directory.name,
        directory=directory,
        family=str(record.get("scenario", {}).get("family", "")),
        seed=int(record.get("scenario", {}).get("seed", 0)),
        cycles=len(cycles),
        samples=int(totals.get("samples", 0)),
        frames=int(totals.get("frames", 0)),
        sim_time_s=float(totals.get("sim_time_s", 0.0)),
        bytes=directory_bytes(directory),
        defects=defects,
    )


def directory_bytes(directory: Path) -> int:
    """Cât ocupă un director, în octeți, fișiere obișnuite."""
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def check_session(directory: Path) -> list[str]:
    """Ce lipsește dintr-o sesiune, ca corpusul să fie întreg și de încredere.

    Se verifică exact ce ar putea produce o sesiune pe jumătate: fișiere lipsă ori goale, totaluri
    care nu se potrivesc cu ce este pe disc (cadre indexate, PNG-uri, rânduri de uzură). Un corpus
    cu găuri se vede la antrenare, când este scump; aici se vede înainte de urcare.
    """
    problems: list[str] = []
    name = directory.name
    for item in CORPUS_FILES:
        path = directory / item
        if not path.is_file():
            problems.append(f"{name}: lipsește {item}")
        elif path.stat().st_size == 0 and item != telemetry.EVENT_FILE:
            # `events.jsonl` chiar are zero octeți când niciun eșec nu s-a produs: fișierul gol
            # este o informație, nu o gaură.
            problems.append(f"{name}: {item} este gol")
    if problems:
        return problems
    try:
        entry = session_entry(directory)
    except (json.JSONDecodeError, OSError, ValueError) as error:
        return [f"{name}: session.json nu se poate citi ({error})"]
    if entry.samples <= 0:
        problems.append(f"{name}: nicio mostră de senzor în {telemetry.SAMPLE_FILE}")
    indexed = _count_lines(directory / telemetry.FRAME_FILE)
    if indexed != entry.frames:
        problems.append(f"{name}: {indexed} cadre în {telemetry.FRAME_FILE}, "
                        f"{entry.frames} în {telemetry.SESSION_FILE}")
    on_disk = sum(1 for path in (directory / telemetry.FRAMES_DIR).rglob("*.png"))
    if on_disk != entry.frames:
        problems.append(f"{name}: {on_disk} cadre pe disc, {entry.frames} în session.json")
    wear_rows = _count_lines(directory / telemetry.WEAR_FILE)
    if wear_rows != entry.samples:
        problems.append(f"{name}: {wear_rows} rânduri de uzură la {entry.samples} eșantioane")
    return problems


def _count_lines(path: Path) -> int:
    """Câte rânduri are un JSON Lines, fără să îl încarce în memorie."""
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def plan_export(run_dir: Path, bucket: str, *, when: datetime | None = None,
                dt: str | None = None, run: str | None = None,
                prefix: str = DATASETS_PREFIX) -> ExportPlan:
    """Planul de urcare: ce sesiuni intră, sub ce chei și ce este de reparat.

    Se poate scrie fără credențiale, deci se poate citi înainte de a atinge bucketul: la urcare,
    planul este și lista de comenzi, iar problemele opresc urcarea.
    """
    moment = when or datetime.now(timezone.utc)
    day = dt or run_date(moment)
    name = run or run_name(moment)
    root = f"{prefix.rstrip('/')}/dt={day}/{name}"
    directories = session_directories(run_dir)
    sessions: list[SessionEntry] = []
    problems: list[str] = []
    for directory in directories:
        found = check_session(directory)
        problems.extend(found)
        if not found:
            sessions.append(session_entry(directory))
    if not directories:
        problems.append(f"nicio sesiune în {run_dir}: nimic de urcat")
    source: dict[str, Any] = {}
    if sessions:
        first = json.loads(sessions[0].directory.joinpath(
            telemetry.SESSION_FILE).read_text(encoding="utf-8"))
        scenario = first.get("scenario", {})
        source = {
            "tool": "simulation/mujoco/run.py",
            "mujoco_version": scenario.get("mujoco_version", ""),
            "python": scenario.get("python", ""),
            "timestep_s": scenario.get("timestep_s", 0.0),
            "sample_hz": scenario.get("sample_hz", 0.0),
            "wear": scenario.get("wear", {}),
        }
        schemas = first.get("schemas", {})
        source["schemas"] = dict(schemas)
    return ExportPlan(bucket=bucket, prefix=root, sessions=tuple(sessions),
                      problems=tuple(problems), source=source)
