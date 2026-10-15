"""Colectarea lungă a geamănului pe Modal: containere efemere care scriu corpusul direct în S3.

Geamănul rulează aici în containere separate, unul pe sesiune, toate în paralel, iar corpusul se
adună într-un volum Modal. La final, un singur container urcă tot volumul, cu `publish.py` și AWS
CLI, exact calea din `make mj-collect` (ADR 0015).

Identitatea, fără chei statice: secretul Modal `cncdt-aws` poartă doar `AWS_ROLE_ARN` și
`AWS_REGION`, iar containerul schimbă `MODAL_IDENTITY_TOKEN` pe credențiale temporare cu
`aws sts assume-role-with-web-identity`. Rolul este `cncdt-<mediu>-modal-collector`, cu trust legat
de spațiul de lucru Modal și politică doar pe bucket-ul corpusului (ADR 0006, ADR 0015).

Prima fază este **doar senzori**, fără cadre: `run.py --no-frames` nu deschide niciun randator,
deci containerul nu are nevoie nici de GPU, nici de context grafic, iar modelul de uzură citește
oricum senzori tabulari (`docs/11` §4).

Rulare, prin Makefile::

    make mj-modal-collect                                  # planul scurt, 1 sesiune pe familie
    make mj-modal-collect MJ_MODAL_CYCLES=500 MJ_MODAL_SESSIONS=4

Ori direct, din rădăcina repo-ului::

    modal run simulation/mujoco/modal_collect.py --families "wheel_100 bushing_68" --cycles 25 \
        --rate 20 --bucket cncdt-dev-training-data-<cont>
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import modal

#: Directorul geamănului pe mașina locală: de aici se montează codul în container.
MUJOCO_DIR = Path(__file__).resolve().parent

#: Rădăcina repo-ului: în container se recreează aceeași structură, fiindcă geamănul își ia
#: așezarea celulei din `docs/diagrams/cell_layout.py`, piesele din extensia Isaac Sim și
#: contractul din `telemetry/schemas/`, toate prin căi relative la rădăcină (`cncdt_mj/__init__.py`).
REPO_ROOT = MUJOCO_DIR.parent.parent
CONTAINER_REPO = "/opt/cncdt"
CONTAINER_MUJOCO = f"{CONTAINER_REPO}/simulation/mujoco"

#: Ce nu are ce căuta în container: venv-ul local, colectările de dinainte, randările, cache-urile.
CODE_IGNORE = ("__pycache__", "**/__pycache__", "*.pyc", ".pytest_cache", "**/.pytest_cache")

#: Ce se montează, cu calea din repo și calea din container. Se montează exact cele patru
#: directoare de care atinge geamănul, nu tot repo-ul: 4 GB de venv-uri și rulări ar intra altfel
#: în fiecare container.
MOUNTS = (
    (MUJOCO_DIR, CONTAINER_MUJOCO,
     CODE_IGNORE + (".venv", ".venv/**", "runs", "runs/**", "renders", "renders/**")),
    (REPO_ROOT / "simulation" / "extensions" / "cncdt.cnc_cell",
     f"{CONTAINER_REPO}/simulation/extensions/cncdt.cnc_cell", CODE_IGNORE),
    (REPO_ROOT / "docs" / "diagrams", f"{CONTAINER_REPO}/docs/diagrams",
     CODE_IGNORE + ("*.png", "*.svg", "*.gv")),
    (REPO_ROOT / "telemetry" / "schemas", f"{CONTAINER_REPO}/telemetry/schemas", CODE_IGNORE),
)

#: Volumul Modal în care fiecare sesiune își scrie fișierele până la urcare.
CORPUS_MOUNT = Path("/corpus")
VOLUME_NAME = "cncdt-mujoco-corpus"

#: Secretul Modal cu identitatea federată: `AWS_ROLE_ARN` și `AWS_REGION`, fără chei de acces.
SECRET_NAME = "cncdt-aws"

#: Versiunile sînt cele din `requirements.txt`, ca un corpus colectat aici să fie identic cu unul
#: colectat local. AWS CLI vine din pip: `sts` și `s3 cp` sînt singurele lui folosiri.
IMAGE_PACKAGES = (
    "mujoco==3.15.0",
    "mujoco-menagerie==2026.10.1",
    "numpy==2.5.4",
    "mapbox_earcut==2.1.0",
    "awscli==1.46.1",
)


def build_image() -> modal.Image:
    """Imaginea containerului: pachetele geamănului plus codul repo-ului, la locurile lui."""
    built = modal.Image.debian_slim(python_version="3.13").pip_install(*IMAGE_PACKAGES)
    for local, remote, ignore in MOUNTS:
        built = built.add_local_dir(local, remote_path=remote, ignore=list(ignore))
    return built


app = modal.App("cncdt-mujoco-collect")
image = build_image()

corpus = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


def split_list(value: str) -> list[str]:
    """Desparte o listă scrisă pe o linie de comandă: virgule ori spații, fără goluri."""
    return [item for item in value.replace(",", " ").split() if item]


def session_seeds(base_seed: int, index: int, sessions_per_family: int) -> list[int]:
    """Semințele unei familii: câte unul pe sesiune, aliniat cu `make mj-collect`.

    Cu o singură sesiune pe familie, semințele sînt `sămânță + index`, exact ca în colectarea
    locală; cu mai multe, fiecare familie primește un bloc contiguu, ca două familii să nu
    împartă aceeași sămânță.
    """
    start = base_seed + index * sessions_per_family
    return [start + offset for offset in range(sessions_per_family)]


def git_revision() -> str:
    """Revizia locală, trimisă în container: acolo nu există depozit git."""
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=MUJOCO_DIR, capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def sts_credentials(role_arn: str, region: str) -> dict[str, str]:
    """Schimbă tokenul de identitate Modal pe credențiale temporare, prin AWS CLI.

    `sts:AssumeRoleWithWebIdentity` nu cere chei: tokenul containerului este dovada, iar trust-ul
    rolului verifică spațiul de lucru și audiența.
    """
    token = os.environ.get("MODAL_IDENTITY_TOKEN", "")
    if not token:
        raise RuntimeError("lipsește MODAL_IDENTITY_TOKEN: containerul nu are identitate Modal")
    session = "modal-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    command = ["aws", "sts", "assume-role-with-web-identity",
               "--role-arn", role_arn,
               "--role-session-name", session,
               "--web-identity-token", token,
               "--duration-seconds", "3600",
               "--region", region,
               "--output", "json"]
    finished = subprocess.run(command, capture_output=True, text=True, check=False)
    if finished.returncode != 0:
        raise RuntimeError(f"asumarea rolului a căzut: {finished.stderr.strip()}")
    credentials = json.loads(finished.stdout)["Credentials"]
    return {
        "AWS_ACCESS_KEY_ID": credentials["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": credentials["SecretAccessKey"],
        "AWS_SESSION_TOKEN": credentials["SessionToken"],
        "AWS_REGION": region,
        "AWS_DEFAULT_REGION": region,
    }


@app.function(image=image, volumes={str(CORPUS_MOUNT): corpus},
              cpu=1.0, memory=2048, timeout=6 * 3600)
def collect_session(run: str, family: str, seed: int, cycles: int, rate: float,
                    machining_s: float | None, injections: list[str]) -> dict:
    """Colectează o sesiune în volum și spune ce a ieșit din ea.

    Un container, o sesiune: fizica este pe un singur fir, deci paralelismul vine din numărul de
    containere. Fișierele rămîn în volum pînă la urcare.
    """
    target = CORPUS_MOUNT / run / f"{family}-{seed}"
    command = [sys.executable, f"{CONTAINER_MUJOCO}/run.py",
               "--family", family, "--cycles", str(cycles), "--seed", str(seed),
               "--rate", str(rate), "--out", str(target), "--no-frames"]
    if machining_s:
        command += ["--machining-s", str(machining_s)]
    for item in injections:
        command += ["--inject", item]
    print(f"[modal] {family}-{seed}: {' '.join(command)}", flush=True)
    finished = subprocess.run(command, check=False)
    if finished.returncode != 0:
        raise RuntimeError(f"colectarea {family}-{seed} a căzut cu {finished.returncode}")
    corpus.commit()
    return summarise_session(target, family, seed)


def summarise_session(directory: Path, family: str, seed: int) -> dict:
    """Ce a scris sesiunea: cicluri, eșantioane, octeți. Se raportează pe mașina locală."""
    record = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    totals = record.get("totals", {})
    size = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
    return {"session": f"{family}-{seed}", "cycles": len(record.get("cycles", [])),
            "samples": int(totals.get("samples", 0)),
            "sim_time_s": float(totals.get("sim_time_s", 0.0)), "bytes": size}


@app.function(image=image, volumes={str(CORPUS_MOUNT): corpus},
              secrets=[modal.Secret.from_name(SECRET_NAME)], timeout=2 * 3600)
def publish_run(run: str, bucket: str, require_wear: bool = True,
                prefix: str = "training-data/mujoco", revision: str = "",
                keep_local: bool = False) -> str:
    """Urcă tot volumul rulării, cu aceeași verificare ca la colectarea locală.

    Credențialele se cer abia aici, cu puțin înainte de urcare: sesiunea rolului ține o oră, iar
    simularea poate dura mai mult, fără să atingă vreodată AWS. Copia din volum se șterge după o
    urcare reușită, ca volumul să nu adune rulări; `keep_local` o păstrează pentru inspecție.
    """
    role_arn = os.environ.get("AWS_ROLE_ARN", "")
    region = os.environ.get("AWS_REGION", "eu-central-1")
    if not role_arn:
        raise RuntimeError(f"secretul {SECRET_NAME} nu are AWS_ROLE_ARN")
    corpus.reload()
    environment = {**os.environ, **sts_credentials(role_arn, region)}
    if revision:
        environment["CNCDT_GIT_REVISION"] = revision
    command = [sys.executable, f"{CONTAINER_MUJOCO}/publish.py",
               "--run-dir", str(CORPUS_MOUNT / run), "--bucket", bucket,
               "--prefix", prefix, "--run", run, "--upload"]
    if require_wear:
        command.append("--require-wear")
    print(f"[modal] urcă: {' '.join(command)}", flush=True)
    finished = subprocess.run(command, check=False, env=environment)
    if finished.returncode != 0:
        raise RuntimeError(f"urcarea a căzut cu {finished.returncode}: volumul rămîne neatins, "
                           f"pentru o reîncercare")
    if not keep_local:
        shutil.rmtree(CORPUS_MOUNT / run, ignore_errors=True)
        corpus.commit()
    return run


@app.local_entrypoint()
def main(families: str = "", cycles: int = 25, rate: float = 20.0, seed: int = 100,
         sessions_per_family: int = 1, machining_s: float = 0.0, inject: str = "",
         require_wear: bool = True, frames: bool = False, bucket: str = "", run: str = "",
         keep_local: bool = False) -> None:
    """Lansează sesiunile în paralel, apoi urcă rularea întreagă."""
    if frames:
        raise SystemExit(
            "cadrele nu sînt suportate pe Modal în faza asta: randarea cere GPU și context "
            "grafic, iar modelul de uzură citește senzori. Rulează fără `--frames`.")
    family_list = split_list(families)
    if not family_list:
        raise SystemExit("lipsește lista de familii: dă `--families \"wheel_100 bushing_68\"`")
    if not bucket:
        raise SystemExit("lipsește bucketul: dă `--bucket` (din `make training-data-status`)")
    sessions = [(family, session_seeds(seed, index, sessions_per_family))
                for index, family in enumerate(family_list)]
    total = sum(len(seeds) for _, seeds in sessions)
    name = run or "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    injections = split_list(inject)
    print(f"[modal] rularea {name}: {total} sesiuni, {cycles} cicluri, rata {rate}, "
          f"fără cadre, în {len(sessions)} familii", flush=True)
    print(f"[modal] urcă în s3://{bucket}/training-data/mujoco/dt=<zi UTC>/{name}/", flush=True)

    handles = []
    for family, seeds in sessions:
        for one_seed in seeds:
            handles.append(collect_session.spawn(
                run=name, family=family, seed=one_seed, cycles=cycles, rate=rate,
                machining_s=machining_s or None, injections=injections))

    rows = []
    for handle in handles:
        rows.append(handle.get())
    for row in rows:
        print(f"[modal] {row['session']:<20} {row['cycles']:>4} cicluri, "
              f"{row['samples']:>7} eșantioane, {row['sim_time_s'] / 60:6.1f} min simulate, "
              f"{row['bytes'] / 1024 / 1024:6.2f} MB", flush=True)
    print(f"[modal] total: {sum(row['samples'] for row in rows)} eșantioane, "
          f"{sum(row['bytes'] for row in rows) / 1024 / 1024:.2f} MB", flush=True)

    published = publish_run.remote(run=name, bucket=bucket, require_wear=require_wear,
                                   revision=git_revision(), keep_local=keep_local)
    print(f"[modal] gata: {published} în s3://{bucket}/training-data/mujoco/", flush=True)
