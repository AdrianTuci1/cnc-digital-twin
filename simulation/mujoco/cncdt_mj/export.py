"""Corpusul de antrenare: ce se urcă din sesiunile geamănului și cum se numesc cheile.

O sesiune scrisă de `run.py` este un corpus complet: **senzorii** în `samples.jsonl` (contractul de
telemetrie), **camerele** în `frames/` și `frames.jsonl` (fiecare PNG cu eticheta piesei din el),
plus etichetele de uzură și de evenimente. Aici se hotărăște unde ajunge fiecare lucru în bucket,
ce scrie manifestul corpusului și ce este de verificat înainte de urcare.

Modul **pur** (fără `mujoco`, fără `boto3`, fără apeluri de rețea): planul se poate scrie și
verifica din `.venv`-ul proiectului, iar urcarea propriu-zisă este treaba lui `publish.py`.

Așezarea în bucket, sub bucketul data lake din `docs/01-architecture.md`:

```
training-data/mujoco/dt=<zi>/<rulare>/manifest.json
training-data/mujoco/dt=<zi>/<rulare>/<familie>-<sămânță>/{samples.jsonl, frames.jsonl, …, frames/}
```

`datasets/` este un prefix propriu, nu `raw/`. `raw/telemetry/` este Parquet de ferestre, scris de
Firehose și citit de tabelul Glue din Terraform, iar `raw/qa-snapshots/` este calea live: fiecare
scriere acolo cheamă Lambda-ul de inspecție. Un corpus de antrenament nu are ce căuta nici în
tabel, nici în declanșator: se citește direct, cu `cncdt_ml.offline` ori cu `build_features.py`,
care știu să deschidă JSON Lines.

Partiționarea pe `dt=` păstrează convenția din `curated/qa-verdicts/dt=<zi>/`, iar numele rulării
(`run-<UTC>`) face ca două colectări să nu se amestece niciodată.

Pe lângă verificarea integrității, modulul **măsoară semnalul de uzură** al fiecărei sesiuni
(:class:`Coverage`): pasul seriei de timp, găurile din ea, cea mai mică sănătate atinsă, componentele
care au trecut pragul și eșecurile înregistrate. Cifrele intră în manifest, deci răspunsul la „are
corpusul ce ne trebuie ca să învățăm uzura?" se citește din bucket, nu se speră.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any, Mapping

from . import telemetry

#: Prefixul corpusului de antrenament al geamănului, sub bucketul data lake.
DATASETS_PREFIX = "training-data/mujoco"

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

#: Sub ce sănătate trebuie să ajungă măcar o componentă ca sesiunea să conțină **semnal de uzură**.
#: La rata nominală (`run.py --rate 1`) un ciclu mișcă sănătatea cu ~0,006: fișierele sunt complete,
#: dar un model de serii de timp nu are ce învăța din ele. Colectarea implicită comprimă viața
#: mașinii (`MJ_COLLECT_RATE`), iar pragul de aici este poarta care cere asta.
WEAR_SIGNAL_HEALTH = 0.9

#: Componentele a căror sănătate se urmărește: chiar cele șapte din `wear.COMPONENTS`.
HEALTH_SUFFIX = "_health"


@dataclass(frozen=True)
class Coverage:
    """Cât de mult semnal de uzură are o sesiune și dacă seria ei de timp este continuă.

    Se citește din `wear.jsonl` (aliniat pe `ts_ms` cu eșantioanele) și din `events.jsonl`. Un
    antrenament pe serii de timp are nevoie de trei lucruri: un ceas fără găuri, sănătatea
    componentelor la fiecare pas și momentele în care ceva a cedat. Toate trei se măsoară aici,
    ca să se vadă din manifest dacă un corpus poate susține un asemenea model.
    """

    rows: int
    dt_ms: int
    gaps: int
    health_min_by_component: Mapping[str, float]
    failures: int
    actions: int

    @property
    def health_min(self) -> float:
        """Cea mai mică sănătate din sesiune, oricare componentă: cât de departe s-a ajuns."""
        return min(self.health_min_by_component.values(), default=1.0)

    @property
    def worn(self) -> tuple[str, ...]:
        """Componentele trecute de pragul de semnal de uzură, în ordine alfabetică."""
        return tuple(sorted(name for name, value in self.health_min_by_component.items()
                            if value < WEAR_SIGNAL_HEALTH))

    @property
    def carries_wear(self) -> bool:
        """Sesiunea a apucat să uzeze ceva ori să treacă un prag de severitate."""
        return self.health_min < WEAR_SIGNAL_HEALTH or self.failures > 0

    def record(self) -> dict[str, Any]:
        """Blocul din manifest: ce s-a măsurat, nu ce s-ar putea spera."""
        return {
            "rows": self.rows,
            "dt_ms": self.dt_ms,
            "gaps": self.gaps,
            "health_min": round(self.health_min, 6),
            "health_min_by_component": {name: round(value, 6) for name, value
                                        in sorted(self.health_min_by_component.items())},
            "worn": list(self.worn),
            "failures": self.failures,
            "actions": self.actions,
        }


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
    coverage: Coverage | None = None

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
            "coverage": self.coverage.record() if self.coverage is not None else {},
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

    @property
    def covers_wear(self) -> bool:
        """Corpusul are ce ar învăța un model de uzură: măcar o sesiune cu semnal.

        Se cere măcar una, nu toate: o colectare poate amesteca familii sănătoase cu familii
        duse până la prag, iar un model de serii de timp are nevoie de traiectoria completă,
        nu de fiecare sesiune în parte.
        """
        return any(item.coverage is not None and item.coverage.carries_wear
                   for item in self.sessions)

    @property
    def health_min(self) -> float:
        """Cea mai mică sănătate din corpus: cât de departe s-a ajuns cu uzura."""
        values = [item.coverage.health_min for item in self.sessions if item.coverage is not None]
        return min(values) if values else 1.0

    @property
    def failures(self) -> int:
        """Câte eșecuri au fost înregistrate, în tot corpusul."""
        return sum(item.coverage.failures for item in self.sessions if item.coverage is not None)

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
                "health_min": round(self.health_min, 6),
                "failures": self.failures,
                "carries_wear": self.covers_wear,
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
    """Citește o sesiune de pe disc: ce declară `session.json`, cât ocupă și ce uzură conține."""
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
        coverage=coverage(directory),
    )


def coverage(directory: Path) -> Coverage:
    """Măsoară semnalul de uzură al unei sesiuni, din fișierele ei.

    `wear.jsonl` are un rând la fiecare eșantion, aliniat pe `ts_ms` și pe `cycle_id`, deci ceasul
    seriei se citește de aici: pasul median dintre rânduri și găurile mai mari decât o dată și
    jumătate din pas — la hotarul dintre cicluri scena se recompune, iar acolo pasul este mai mare.
    Eșecurile și acțiunile se numără din `events.jsonl`, fișierul de etichete.
    """
    moments: list[int] = []
    minima: dict[str, float] = {}
    for row in _read_jsonl(directory / telemetry.WEAR_FILE):
        moments.append(int(row.get("ts_ms", 0)))
        for name, value in row.items():
            if name.endswith(HEALTH_SUFFIX):
                component = name[: -len(HEALTH_SUFFIX)]
                minima[component] = min(minima.get(component, 1.0), float(value))
    steps = sorted(step for step in (second - first for first, second in pairwise(moments))
                   if step > 0)
    dt_ms = steps[len(steps) // 2] if steps else 0
    events = _read_jsonl(directory / telemetry.EVENT_FILE)
    return Coverage(
        rows=len(moments),
        dt_ms=int(dt_ms),
        gaps=sum(1 for step in steps if dt_ms and step > 1.5 * dt_ms),
        health_min_by_component=minima,
        failures=sum(1 for row in events if row.get("event") == "failure"),
        actions=sum(1 for row in events if row.get("event") == "action"),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Rândurile unui JSON Lines, ca obiecte; fișierul lipsă înseamnă listă goală."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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
    elif not entry.coverage.health_min_by_component:
        # Fără coloane de sănătate, fișierul nu mai spune ce s-a uzat: manifestul ar minți liniștit
        # că sesiunea este sănătoasă (h = 1,0), iar antrenarea ar primi etichete false.
        problems.append(f"{name}: {telemetry.WEAR_FILE} nu are nicio coloană de sănătate")
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
