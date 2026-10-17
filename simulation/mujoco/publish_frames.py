#!/usr/bin/env python3
"""Urcă în bucket corpusul de cadre al geamănului, fără telemetrie.

Telemetria are deja corpusul ei (`training-data/mujoco/`), iar pentru antrenarea prinderii și a
clasificării de piese contează doar PNG-urile și indexul lor. Sesiunile scrise cu
`run.py --only-frames` conțin exact atît: `frames/`, `frames.jsonl` și `session.json`. Aici se
verifică legătura dintre index și fișiere, apoi se urcă sub un prefix propriu.

Cheile din bucket::

    training-data/grasp/dt=<zi>/run-<UTC>/manifest.json
    training-data/grasp/dt=<zi>/run-<UTC>/<familie>-<sămânță>/frames.jsonl
    training-data/grasp/dt=<zi>/run-<UTC>/<familie>-<sămânță>/frames/<ciclu>/*.png
    training-data/grasp/dt=<zi>/run-<UTC>/<familie>-<sămânță>/session.json

Urcarea folosește AWS CLI, ca niciun venv să nu capete `boto3`. Fără `--upload`, comanda doar
citește corpusul și tipărește planul.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

MUJOCO_DIR = Path(__file__).resolve().parent
if str(MUJOCO_DIR) not in sys.path:
    sys.path.insert(0, str(MUJOCO_DIR))

from cncdt_mj import telemetry  # noqa: E402

#: Prefixul corpusului de cadre în bucketul de antrenament.
FRAMES_PREFIX = "training-data/grasp"

#: Variabila din care se ia bucketul cînd nu se dă `--bucket`.
BUCKET_VAR = "DATA_LAKE_BUCKET"

#: Versiunea manifestului de cadre; o schimbare de cîmpuri cere bump aici.
SCHEMA_VERSION = "1.0.0"

#: Cîmpurile fără care un rând din `frames.jsonl` nu poate antrena nimic.
LABEL_FIELDS = ("part_id", "family", "defect", "verdict", "pov", "stage", "path")


def log(message: str) -> None:
    print(f"[frames] {message}", flush=True)


def die(message: str, code: int = 1) -> None:
    print(f"[frames][eroare] {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def execute(command: list[str]) -> None:
    finished = subprocess.run(command, check=False)
    if finished.returncode != 0:
        die(f"comanda a căzut cu {finished.returncode}: {' '.join(command)}")


def git_revision() -> str:
    """Revizia din care s-a randat corpusul, din git ori din mediu (container)."""
    from_environment = os.environ.get("CNCDT_GIT_REVISION", "").strip()
    if from_environment:
        return from_environment
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=MUJOCO_DIR, capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def check_credentials() -> str:
    result = subprocess.run(["aws", "sts", "get-caller-identity", "--output", "json"],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        die("AWS CLI nu are credențiale valide. Rulează întîi:\n"
            '    eval "$(./scripts/assume-operator.sh)"')
    return str(json.loads(result.stdout or "{}").get("Arn", ""))


def run_name(moment: datetime | None = None) -> str:
    return "run-" + (moment or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S")


def run_day(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_session(directory: Path) -> dict[str, Any]:
    """Metadatele sesiunii, plus indexul de cadre verificat separat."""
    record = json.loads((directory / telemetry.SESSION_FILE).read_text(encoding="utf-8"))
    scenario = record.get("scenario", {})
    totals = record.get("totals", {})
    rows = read_jsonl(directory / telemetry.FRAME_FILE)
    return {
        "name": directory.name,
        "directory": directory,
        "family": str(scenario.get("family", "")),
        "seed": int(scenario.get("seed", 0)),
        "cycles": int(totals.get("cycles", 0)),
        "sim_time_s": float(totals.get("sim_time_s", 0.0)),
        "declared_frames": int(totals.get("frames", 0)),
        "rows": rows,
        "bytes": directory_bytes(directory / telemetry.FRAMES_DIR)
        + (directory / telemetry.FRAME_FILE).stat().st_size,
    }


def directory_bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def validate_session(session: Mapping[str, Any]) -> list[str]:
    """Problemele care ar face corpusul inutil: index fără PNG, rînduri fără etichete."""
    name = str(session["name"])
    directory = Path(session["directory"])
    rows = list(session["rows"])
    problems: list[str] = []
    if not (directory / telemetry.FRAMES_DIR).is_dir():
        problems.append(f"{name}: lipsește directorul {telemetry.FRAMES_DIR}")
    if not rows:
        problems.append(f"{name}: {telemetry.FRAME_FILE} este gol")
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        missing = [field for field in LABEL_FIELDS if not row.get(field)]
        if missing:
            problems.append(f"{name}: rînd fără {', '.join(missing)}")
            continue
        key = (str(row["part_id"]), str(row["pov"]), str(row["stage"]))
        if key in seen:
            problems.append(f"{name}: cadru duplicat {key}")
        seen.add(key)
        path = directory / str(row["path"])
        if path.suffix.lower() != ".png":
            problems.append(f"{name}: {path} nu este PNG")
        elif not path.is_file():
            problems.append(f"{name}: lipsește {path}")
    declared = int(session["declared_frames"])
    if declared and declared != len(rows):
        problems.append(f"{name}: {len(rows)} cadre în index, {declared} în session.json")
    return problems


def session_record(session: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    rows = list(session["rows"])
    defects = Counter(str(row["defect"]) for row in rows)
    return {
        "name": session["name"],
        "prefix": f"{prefix.rstrip('/')}/{session['name']}/",
        "family": session["family"],
        "seed": session["seed"],
        "cycles": session["cycles"],
        "frames": len(rows),
        "grasp_frames": sum(1 for row in rows if str(row["pov"]).endswith("_grasp")),
        "sim_time_s": round(float(session["sim_time_s"]), 6),
        "bytes": int(session["bytes"]),
        "defects": dict(sorted(defects.items())),
    }


def run_manifest(sessions: Sequence[Mapping[str, Any]], *, run: str, bucket: str,
                 prefix: str, day: str, revision: str = "") -> dict[str, Any]:
    rows = [row for session in sessions for row in session["rows"]]
    families = Counter(str(row["family"]) for row in rows)
    defects = Counter(str(row["defect"]) for row in rows)
    verdicts = Counter(str(row["verdict"]) for row in rows)
    povs = Counter(str(row["pov"]) for row in rows)
    stages = Counter(str(row["stage"]) for row in rows)
    resolutions = Counter(f"{row['width']}×{row['height']}" for row in rows
                          if "width" in row and "height" in row)
    location = f"s3://{bucket}/{prefix}/dt={day}/{run}/"
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": run,
        "location": location,
        "dataset": {
            "sessions": len(sessions),
            "frames": len(rows),
            "grasp_frames": sum(1 for row in rows if str(row["pov"]).endswith("_grasp")),
            "resolution": sorted(resolutions),
            "povs": dict(sorted(povs.items())),
            "stages": dict(sorted(stages.items())),
        },
        "totals": {"frames": len(rows),
                   "bytes": sum(int(session["bytes"]) for session in sessions)},
        "sessions": [session_record(session, f"{prefix}/dt={day}/{run}") for session in sessions],
        "families": dict(sorted(families.items())),
        "defects": dict(sorted(defects.items())),
        "verdicts": dict(sorted(verdicts.items())),
        "git": {"revision": revision},
        "publish": {"tool": "simulation/mujoco/publish_frames.py",
                    "python": platform.python_version()},
        "index": telemetry.FRAME_FILE,
    }


def upload_commands(sessions: Sequence[Mapping[str, Any]], bucket: str, prefix: str,
                    day: str, run: str) -> list[list[str]]:
    """Comenzile AWS CLI care urcă doar cadrele, indexul și metadatele sesiunii."""
    base = f"s3://{bucket}/{prefix}/dt={day}/{run}"
    commands: list[list[str]] = []
    for session in sessions:
        directory = Path(session["directory"])
        target = f"{base}/{session['name']}"
        commands.extend([
            ["aws", "s3", "cp", str(directory / telemetry.FRAMES_DIR),
             f"{target}/{telemetry.FRAMES_DIR}", "--recursive"],
            ["aws", "s3", "cp", str(directory / telemetry.FRAME_FILE),
             f"{target}/{telemetry.FRAME_FILE}"],
            ["aws", "s3", "cp", str(directory / telemetry.SESSION_FILE),
             f"{target}/{telemetry.SESSION_FILE}"],
        ])
    return commands


def find_sessions(run_dir: Path) -> list[Path]:
    return sorted(path for path in run_dir.iterdir()
                  if path.is_dir() and (path / telemetry.SESSION_FILE).is_file())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True,
                        help="directorul rulării, cu subdirectoare <familie>-<sămânță>")
    parser.add_argument("--bucket", default=os.environ.get(BUCKET_VAR, ""),
                        help=f"bucketul data lake (implicit din ${BUCKET_VAR})")
    parser.add_argument("--prefix", default=FRAMES_PREFIX,
                        help=f"prefixul corpusului (implicit {FRAMES_PREFIX})")
    parser.add_argument("--run", default=None, help="numele rulării (implicit run-<UTC>)")
    parser.add_argument("--dt", default=None,
                        help="ziua partiției (implicit ziua de azi, în UTC)")
    parser.add_argument("--upload", action="store_true",
                        help="urcă efectiv; fără el, doar verifică și tipărește planul")
    parser.add_argument("--json", action="store_true", help="tipărește manifestul")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        die(f"nu există directorul {run_dir}")
    if not args.bucket:
        die(f"lipsește bucketul: dă `--bucket` ori pune ${BUCKET_VAR} în mediu")

    directories = find_sessions(run_dir)
    if not directories:
        die(f"niciun {telemetry.SESSION_FILE} în {run_dir}")
    sessions = [load_session(directory) for directory in directories]

    problems = [problem for session in sessions for problem in validate_session(session)]
    if problems:
        for problem in problems[:20]:
            log(f"  problemă: {problem}")
        die(f"corpusul are {len(problems)} probleme: se repară, apoi se reia urcarea")

    moment = datetime.now(timezone.utc)
    run = args.run or run_name(moment)
    day = args.dt or run_day(moment)
    manifest = run_manifest(sessions, run=run, bucket=args.bucket, prefix=args.prefix,
                            day=day, revision=git_revision())
    log(f"corpus: {manifest['totals']['frames']} cadre, "
        f"{manifest['totals']['bytes'] / 1024 / 1024:.2f} MB, "
        f"{manifest['dataset']['sessions']} sesiuni")
    log(f"clase:  " + ", ".join(f"{name}:{count}" for name, count in
                                sorted(manifest["defects"].items())))
    log(f"puncte: " + ", ".join(f"{name}:{count}" for name, count in
                                sorted(manifest["dataset"]["povs"].items())))
    log(f"destinație: {manifest['location']}")

    if not args.upload:
        for command in upload_commands(sessions, args.bucket, args.prefix, day, run):
            print("    " + " ".join(command), flush=True)
        if args.json:
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
        log("plan scris; pentru urcare adaugă `--upload`")
        return 0

    arn = check_credentials()
    log(f"urcă ca {arn}")
    with (run_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    for command in upload_commands(sessions, args.bucket, args.prefix, day, run):
        execute(command)
    execute(["aws", "s3", "cp", str(run_dir / "manifest.json"),
             f"s3://{args.bucket}/{args.prefix}/dt={day}/{run}/manifest.json"])
    log(f"gata: {manifest['totals']['frames']} cadre în {manifest['location']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
