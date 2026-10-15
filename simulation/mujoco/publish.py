#!/usr/bin/env python3
"""Urcă în bucket corpusul de antrenament al geamănului: senzorii și cadrele unei colectări.

Planul îl scrie `cncdt_mj/export.py` (pur, testabil), iar aici se execută: fiecare sesiune urcă
recursiv sub `datasets/mujoco/dt=<zi>/<rulare>/<familie>-<sămânță>/`, plus un `manifest.json` la
rădăcina rulării, care spune ce conține corpusul, cu ce versiune de contract a fost scris și de
unde se citește.

Urcarea se face cu **AWS CLI**, nu cu un SDK: credențialele ajung în mediu prin
`eval "$(./scripts/assume-operator.sh)"`, iar rolul de operator are deja acces la obiectele din
data lake. Așa nu se adaugă nicio dependență în niciunul dintre cele două venv-uri.

Fără `--upload`, comanda doar **citește** corpusul și tipărește planul: ce sesiuni, câte eșantioane,
câte cadre, ce chei, câtă uzură conțin. Este modul în care se verifică o colectare înainte de a
atinge bucketul.

Cu `--require-wear`, corpusul în care nicio componentă nu a coborât sub pragul de semnal de uzură și
în care nu s-a înregistrat niciun eșec este **refuzat**: fișierele ar fi complete, dar un model de
serii de timp nu are ce învăța din ele. `make mj-collect` îl folosește, ca o colectare de rutină să
nu ajungă în bucket fără degradare.

Rulare::

    python3 simulation/mujoco/publish.py --run-dir simulation/mujoco/runs          # doar planul
    python3 simulation/mujoco/publish.py --run-dir simulation/mujoco/runs \
        --bucket "$DATA_LAKE_BUCKET" --upload
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

MUJOCO_DIR = Path(__file__).resolve().parent
if str(MUJOCO_DIR) not in sys.path:
    sys.path.insert(0, str(MUJOCO_DIR))

from cncdt_mj import export  # noqa: E402  (importul cere calea de mai sus)

#: Variabila de mediu din care se ia bucketul când nu se dă `--bucket`. Se completează cu
#: `terraform output -raw data_lake_bucket` (stack-ul `platform`).
BUCKET_VAR = "DATA_LAKE_BUCKET"

#: Directorul implicit al corpusului: același în care scrie `run.py`.
RUNS_DIR = MUJOCO_DIR / "runs"


def log(message: str) -> None:
    print(f"[publish] {message}", flush=True)


def die(message: str, code: int = 1) -> None:
    print(f"[publish][eroare] {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def execute(command: list[str]) -> None:
    """Rulează o comandă de urcare, cu tot cu mesajul ei de eroare dacă pică."""
    finished = subprocess.run(command, check=False)
    if finished.returncode != 0:
        die(f"comanda a căzut cu {finished.returncode}: {' '.join(command)}")


def git_revision() -> str:
    """Revisionul din care s-a generat corpusul: în manifest, ca datele să fie trasabile."""
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=MUJOCO_DIR, capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def check_credentials() -> str:
    """Cine urcă: fără credențiale în mediu, comanda de urcare nu are rost."""
    result = subprocess.run(["aws", "sts", "get-caller-identity", "--output", "json"],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        die("AWS CLI nu are credențiale valide. Rulează întâi, în sesiunea curentă:\n"
            '    eval "$(./scripts/assume-operator.sh)"')
    identity = json.loads(result.stdout or "{}")
    return str(identity.get("Arn", ""))


def report(plan: export.ExportPlan) -> None:
    """Ce urcă: sesiunile, pe familii, cu ce s-a adunat în ele și câtă uzură conțin."""
    log(f"corpus: s3://{plan.bucket}/{plan.prefix}/")
    for item in plan.sessions:
        defects = ", ".join(f"{name}×{count}" for name, count in sorted(item.defects.items()))
        wear = item.coverage
        detail = ""
        if wear is not None:
            detail = (f" | h ≥ {wear.health_min:.3f}, {wear.dt_ms} ms pas, "
                      f"{len(wear.worn)} componente trecute de prag, {wear.failures} eșecuri")
        log(f"  {item.name:<18} {item.family:<12} {item.cycles} cicluri, "
            f"{item.samples} eșantioane, {item.frames} cadre, "
            f"{item.bytes / 1024:8.1f} KB  ({defects}){detail}")
    totals = {
        "sessions": len(plan.sessions),
        "samples": sum(item.samples for item in plan.sessions),
        "frames": sum(item.frames for item in plan.sessions),
    }
    log(f"total: {totals['sessions']} sesiuni, {totals['samples']} eșantioane de senzori, "
        f"{totals['frames']} cadre, {plan.bytes / 1024 / 1024:.1f} MB, "
        f"sănătate minimă {plan.health_min:.3f}, {plan.failures} eșecuri")
    if plan.covers_wear:
        log(f"uzură: da, măcar o sesiune a trecut sub h = {export.WEAR_SIGNAL_HEALTH:g}")
    else:
        log(f"uzură: NU — nicio componentă nu a coborât sub h = {export.WEAR_SIGNAL_HEALTH:g} "
            f"și nu s-a înregistrat niciun eșec; vezi `MJ_COLLECT_RATE`")


def build_parser() -> argparse.ArgumentParser:
    """Opțiunile urcării: ce se urcă, unde și dacă se atinge bucketul."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", default=str(RUNS_DIR),
                        help=f"directorul cu sesiunile de urcat (implicit {RUNS_DIR})")
    parser.add_argument("--bucket", default=os.environ.get(BUCKET_VAR, ""),
                        help=f"bucketul data lake (implicit din ${BUCKET_VAR})")
    parser.add_argument("--prefix", default=export.DATASETS_PREFIX,
                        help=f"prefixul corpusului în bucket (implicit {export.DATASETS_PREFIX})")
    parser.add_argument("--run", default=None,
                        help="numele rulării, sub prefix (implicit run-<UTC>, la ora urcării)")
    parser.add_argument("--dt", default=None,
                        help="ziua partiției, în forma 2026-10-15 (implicit ziua de azi, în UTC)")
    parser.add_argument("--upload", action="store_true",
                        help="urcă efectiv; fără el, comanda doar citește corpusul și tipărește "
                             "planul de chei")
    parser.add_argument("--require-wear", action="store_true",
                        help="refuză corpusul în care nicio componentă nu a coborât sub pragul de "
                             "semnal de uzură și nu s-a înregistrat niciun eșec: fișierele ar fi "
                             "complete, dar un model de uzură nu are ce învăța din ele")
    parser.add_argument("--json", action="store_true",
                        help="tipărește manifestul, ca să poată fi citit de alte unelte")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Citește corpusul, tipărește planul și, la cerere, îl urcă."""
    args = build_parser().parse_args(argv)
    run_dir = Path(args.run_dir)
    if not args.bucket:
        die(f"lipsește bucketul: dă `--bucket` ori pune ${BUCKET_VAR} în mediu (din "
            "`terraform output -raw data_lake_bucket`)")
    plan = export.plan_export(run_dir, args.bucket, dt=args.dt, run=args.run,
                              prefix=args.prefix)
    if not plan.sessions:
        die(f"nicio sesiune de urcat în {run_dir}; rulează `make mj-collect` ori `make mj-record`")

    report(plan)
    for problem in plan.problems:
        log(f"  problemă: {problem}")
    if not plan.ok:
        die(f"corpusul are {len(plan.problems)} probleme: se repară, apoi se reia urcarea")
    if args.require_wear and not plan.covers_wear:
        die("corpusul nu are semnal de uzură: nicio componentă nu a coborât sub "
            f"h = {export.WEAR_SIGNAL_HEALTH:g} și nu s-a înregistrat niciun eșec.\n"
            "Urcă viața mașinii, cu `MJ_COLLECT_RATE` (de pildă 20) ori cu `--rate` dat lui "
            "run.py, sau pornește sesiunea cu o componentă deja uzată (`--inject`)")

    extra: dict[str, object] = {
        "git": {"revision": git_revision()},
        "publish": {"tool": "simulation/mujoco/publish.py", "python": platform.python_version()},
    }
    if not args.upload:
        for item in plan.sessions:
            print("    " + " ".join(plan.command(item)), flush=True)
        if args.json:
            print(json.dumps(plan.manifest(created_ms=int(time.time() * 1000), extra=extra),
                             indent=2, ensure_ascii=False))
        log("plan scris; pentru urcare adaugă `--upload`")
        return 0

    arn = check_credentials()
    log(f"urcă ca {arn}")
    extra["uploaded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = plan.manifest(created_ms=int(time.time() * 1000), extra=extra)
    for item in plan.sessions:
        execute(plan.command(item))
    with tempfile.TemporaryDirectory(prefix="cncdt-manifest-") as workspace:
        path = Path(workspace) / export.MANIFEST_FILE
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        execute(["aws", "s3", "cp", str(path),
                 f"s3://{plan.bucket}/{plan.prefix}/{export.MANIFEST_FILE}"])
    log(f"gata: {len(plan.sessions)} sesiuni în s3://{plan.bucket}/{plan.prefix}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
