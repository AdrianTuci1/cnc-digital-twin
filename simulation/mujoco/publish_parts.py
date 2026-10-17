#!/usr/bin/env python3
"""Urcă în bucket datasetul de imagini de calitate al pieselor.

Aceeași împărțire ca la corpusul geamănului: `cncdt_mj/part_images.py` ține planul și indexul
(pur), `render_parts.py` scrie cadrele, iar aici se verifică și se urcă. Urcarea folosește **AWS
CLI**, nu un SDK, ca niciun venv să nu capete `boto3`.

Cheile din bucket::

    training-data/parts/dt=<zi>/run-<UTC>/manifest.json
    training-data/parts/dt=<zi>/run-<UTC>/parts.jsonl
    training-data/parts/dt=<zi>/run-<UTC>/images.jsonl
    training-data/parts/dt=<zi>/run-<UTC>/images-train.jsonl
    training-data/parts/dt=<zi>/run-<UTC>/images/<familie>/<clasă>/<piesă>-v00.png

Indexul este pentru antrenare: fiecare rînd leagă PNG-ul de familie, defect și verdict. Fără
`--upload`, comanda doar citește datasetul și tipărește planul, deci se poate verifica o randare
înainte de a atinge bucketul.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

MUJOCO_DIR = Path(__file__).resolve().parent
if str(MUJOCO_DIR) not in sys.path:
    sys.path.insert(0, str(MUJOCO_DIR))

from cncdt_mj import part_images  # noqa: E402

#: Prefixul datasetului de piese în bucketul de antrenament.
PARTS_PREFIX = "training-data/parts"

#: Variabila din care se ia bucketul cînd nu se dă `--bucket`.
BUCKET_VAR = "DATA_LAKE_BUCKET"

#: Versiunea manifestului de imagini; o schimbare de cîmpuri cere bump aici.
SCHEMA_VERSION = "1.0.0"


def log(message: str) -> None:
    print(f"[parts] {message}", flush=True)


def die(message: str, code: int = 1) -> None:
    print(f"[parts][eroare] {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def execute(command: list[str]) -> None:
    finished = subprocess.run(command, check=False)
    if finished.returncode != 0:
        die(f"comanda a căzut cu {finished.returncode}: {' '.join(command)}")


def git_revision() -> str:
    """Revizia din care s-a randat datasetul, din git ori din mediu (container)."""
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


def validate(dataset_dir: str | Path, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Problemele care ar face datasetul inutil: imagini lipsă, rînduri fără etichetă.

    Se verifică înainte de urcare, cînd fiecare problemă se vede ieftin. Un index care trimite la
    un PNG inexistent este mai rău decît lipsa indexului: antrenarea ar cădea la mijlocul rulării.
    """
    root = Path(dataset_dir)
    problems: list[str] = []
    if not rows:
        problems.append("indexul de imagini este gol")
    seen: set[tuple[str, int]] = set()
    for row in rows:
        key = (str(row.get("part_id", "")), int(row.get("view", -1)))
        if key in seen:
            problems.append(f"cadru duplicat: {key[0]} v{key[1]}")
        seen.add(key)
        path = root / str(row.get("path", ""))
        if not row.get("path"):
            problems.append(f"{key[0]} v{key[1]} nu are cale")
        elif path.suffix.lower() != ".png":
            problems.append(f"{path} nu este PNG")
        elif not path.is_file():
            problems.append(f"lipsește {path}")
        for field in ("family", "defect", "verdict", "split"):
            if not row.get(field):
                problems.append(f"{key[0]} v{key[1]} nu are {field}")
    return problems


def dataset_manifest(dataset_dir: str | Path, rows: Sequence[Mapping[str, Any]], *,
                     run: str, bucket: str, prefix: str, day: str,
                     revision: str = "", render_seed: int = 0) -> dict[str, Any]:
    """Ce conține datasetul, în forma în care îl citește antrenarea."""
    root = Path(dataset_dir)
    families = Counter(str(row["family"]) for row in rows)
    defects = Counter(str(row["defect"]) for row in rows)
    verdicts = Counter(str(row["verdict"]) for row in rows)
    splits = Counter(str(row["split"]) for row in rows)
    widths = {int(row["width"]) for row in rows if "width" in row}
    heights = {int(row["height"]) for row in rows if "height" in row}
    total_bytes = sum((root / str(row["path"])).stat().st_size for row in rows
                      if (root / str(row["path"])).is_file())
    labels_path = root / "dataset" / "parts.jsonl"
    if not labels_path.is_file():
        labels_path = root / "parts.jsonl"
    labels = sum(1 for line in labels_path.read_text(encoding="utf-8").splitlines()
                 if line.strip()) if labels_path.is_file() else 0
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": run,
        "location": f"s3://{bucket}/{prefix}/dt={day}/{run}/",
        "dataset": {
            "parts": labels,
            "images": len(rows),
            "views_per_part": max(1, len(rows) // labels) if labels else 0,
            "resolution": sorted(f"{width}×{height}" for width in widths for height in heights),
            "camera": "top",
            "fovy_deg": part_images.FOVY_DEG,
            "render_seed": int(render_seed),
        },
        "totals": {"images": len(rows), "bytes": total_bytes},
        "families": dict(sorted(families.items())),
        "defects": dict(sorted(defects.items())),
        "verdicts": dict(sorted(verdicts.items())),
        "splits": dict(sorted(splits.items())),
        "git": {"revision": revision},
        "publish": {"tool": "simulation/mujoco/publish_parts.py",
                    "python": platform.python_version()},
        "index": part_images.INDEX_FILE,
    }


def upload_commands(dataset_dir: str | Path, bucket: str, prefix: str,
                    day: str, run: str) -> list[list[str]]:
    """Comenzile AWS CLI care urcă datasetul, în ordinea în care se pot relua."""
    root = Path(dataset_dir)
    base = f"s3://{bucket}/{prefix}/dt={day}/{run}"
    commands = [
        ["aws", "s3", "cp", str(root / part_images.IMAGE_DIR), f"{base}/{part_images.IMAGE_DIR}",
         "--recursive"],
        ["aws", "s3", "cp", str(root / part_images.INDEX_FILE), f"{base}/{part_images.INDEX_FILE}"],
    ]
    for split in part_images.SPLITS:
        path = root / f"images-{split}.jsonl"
        if path.is_file():
            commands.append(["aws", "s3", "cp", str(path), f"{base}/{path.name}"])
    labels = root / "dataset" / "parts.jsonl"
    if not labels.is_file():
        labels = root / "parts.jsonl"
    if labels.is_file():
        commands.append(["aws", "s3", "cp", str(labels), f"{base}/parts.jsonl"])
    return commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-dir", required=True,
                        help="directorul scris de render_parts.py (are images/ și indexul)")
    parser.add_argument("--bucket", default=os.environ.get(BUCKET_VAR, ""),
                        help=f"bucketul data lake (implicit din ${BUCKET_VAR})")
    parser.add_argument("--prefix", default=PARTS_PREFIX,
                        help=f"prefixul datasetului (implicit {PARTS_PREFIX})")
    parser.add_argument("--run", default=None, help="numele rulării (implicit run-<UTC>)")
    parser.add_argument("--dt", default=None,
                        help="ziua partiției (implicit ziua de azi, în UTC)")
    parser.add_argument("--render-seed", type=int, default=0,
                        help="sămânța de randare, scrisă în manifest")
    parser.add_argument("--upload", action="store_true",
                        help="urcă efectiv; fără el, doar verifică și tipărește planul")
    parser.add_argument("--json", action="store_true", help="tipărește manifestul")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset_dir = Path(args.dataset_dir)
    if not dataset_dir.is_dir():
        die(f"nu există directorul {dataset_dir}")
    if not args.bucket:
        die(f"lipsește bucketul: dă `--bucket` ori pune ${BUCKET_VAR} în mediu")

    try:
        rows = part_images.merge_index(dataset_dir)
    except ValueError as error:
        die(str(error))
    part_images.write_indexes(dataset_dir, rows)
    problems = validate(dataset_dir, rows)
    if problems:
        for problem in problems[:20]:
            log(f"  problemă: {problem}")
        die(f"datasetul are {len(problems)} probleme: se repară, apoi se reia urcarea")

    moment = datetime.now(timezone.utc)
    run = args.run or run_name(moment)
    day = args.dt or run_day(moment)
    manifest = dataset_manifest(dataset_dir, rows, run=run, bucket=args.bucket,
                                prefix=args.prefix, day=day, revision=git_revision(),
                                render_seed=args.render_seed)
    log(f"dataset: {len(rows)} imagini, {manifest['totals']['bytes'] / 1024 / 1024:.2f} MB, "
        f"{manifest['dataset']['parts']} piese")
    log(f"clase:   " + ", ".join(f"{name}:{count}" for name, count in
                                sorted(manifest["defects"].items())))
    log(f"familii: " + ", ".join(f"{name}:{count}" for name, count in
                                sorted(manifest["families"].items())))
    log(f"destinație: {manifest['location']}")

    if not args.upload:
        for command in upload_commands(dataset_dir, args.bucket, args.prefix, day, run):
            print("    " + " ".join(command), flush=True)
        if args.json:
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
        log("plan scris; pentru urcare adaugă `--upload`")
        return 0

    arn = check_credentials()
    log(f"urcă ca {arn}")
    with (dataset_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    for command in upload_commands(dataset_dir, args.bucket, args.prefix, day, run):
        execute(command)
    execute(["aws", "s3", "cp", str(dataset_dir / "manifest.json"),
             f"s3://{args.bucket}/{args.prefix}/dt={day}/{run}/manifest.json"])
    log(f"gata: {len(rows)} imagini în {manifest['location']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
