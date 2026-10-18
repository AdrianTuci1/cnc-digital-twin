"""Colectarea lungă a geamănului pe Modal: containere efemere care scriu corpusul direct în S3.

Geamănul rulează aici în containere separate, unul pe sesiune, toate în paralel, iar corpusul se
adună într-un volum Modal. La final, un singur container verifică și urcă rularea cu publisherul
potrivit (`publish.py`, `publish_frames.py` ori `publish_parts.py`) și AWS CLI (ADR 0015).

Identitatea, fără chei statice: secretul Modal `cncdt-aws` poartă doar `AWS_ROLE_ARN` și
`AWS_REGION`, iar containerul schimbă `MODAL_IDENTITY_TOKEN` pe credențiale temporare cu
`aws sts assume-role-with-web-identity`. Rolul este `cncdt-<mediu>-modal-collector`, cu trust legat
de spațiul de lucru Modal și politică doar pe bucket-ul corpusului (ADR 0006, ADR 0015).

Containerul are două feluri de sesiuni. Fără cadre (`--no-frames`) rulează pe CPU, fiindcă
modelul de uzură citește senzori tabulari. Cu cadre (`--frames`) rulează pe un GPU T4, cu
`MUJOCO_GL=egl`, și scrie aceleași PNG-uri ca local: cele patru cadre de proces plus cele șase
de prindere (apropiere, piesa în falci, ridicare), pe fiecare ciclu.

Aplicația mai are o intrare, `parts`, pentru datasetul de imagini de calitate: un container
generează manifestul de etichete, mai multe GPU-uri randează cîte o felie de piese, iar la final
un container verifică și urcă totul sub `training-data/parts/`.

Rulare, prin Makefile::

    make mj-modal-collect                                  # planul scurt, 1 sesiune pe familie
    make mj-modal-collect MJ_MODAL_CYCLES=500 MJ_MODAL_SESSIONS=4
    make mj-modal-parts                                    # 220 de piese × 4 cadre, 8 felii

Ori direct, din rădăcina repo-ului::

    modal run simulation/mujoco/modal_collect.py::main --families "wheel_100 hex_68" \
        --cycles 25 --rate 20 --frames --bucket cncdt-dev-training-data-<cont>
    modal run simulation/mujoco/modal_collect.py::parts --count 220 --shards 8 \
        --bucket cncdt-dev-training-data-<cont>
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
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

#: Ce se montează, cu calea din repo și calea din container. Se montează exact directoarele de
#: care atinge geamănul, nu tot repo-ul: 4 GB de venv-uri și rulări ar intra altfel în fiecare
#: container. `simulation/scripts` aduce generatorul de piese.
MOUNTS = (
    (MUJOCO_DIR, CONTAINER_MUJOCO,
     CODE_IGNORE + (".venv", ".venv/**", "runs", "runs/**", "renders", "renders/**")),
    (REPO_ROOT / "simulation" / "extensions" / "cncdt.cnc_cell",
     f"{CONTAINER_REPO}/simulation/extensions/cncdt.cnc_cell", CODE_IGNORE),
    (REPO_ROOT / "simulation" / "scripts", f"{CONTAINER_REPO}/simulation/scripts", CODE_IGNORE),
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


#: Bibliotecile EGL/GL pe care le încarcă `mujoco.Renderer` pe Linux. Fără ele, randarea pe GPU
#: cade la importul lui `mujoco` cu „failed to initialize EGL”.
IMAGE_APT = (
    "libglib2.0-0",
    "libegl1",
    "libgl1",
    "libglvnd0",
    "libgles2",
    "libosmesa6",
)


def build_image() -> modal.Image:
    """Imaginea containerului: pachetele geamănului plus codul repo-ului, la locurile lui."""
    built = (modal.Image.debian_slim(python_version="3.13")
             .apt_install(*IMAGE_APT)
             .env({"MUJOCO_GL": "egl", "PYOPENGL_PLATFORM": "egl"})
             .pip_install(*IMAGE_PACKAGES))
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


def collect_command(target: Path, family: str, seed: int, cycles: int, rate: float,
                    machining_s: float | None, injections: list[str],
                    frames: bool, only_frames: bool = False) -> list[str]:
    """Comanda lui `run.py` pentru o sesiune, cu sau fără cadre."""
    command = [sys.executable, f"{CONTAINER_MUJOCO}/run.py",
               "--family", family, "--cycles", str(cycles), "--seed", str(seed),
               "--rate", str(rate), "--out", str(target)]
    command += ["--frames", "--grasp-frames"] if frames else ["--no-frames"]
    if frames and only_frames:
        command += ["--only-frames", "--allow-grasp-failures"]
    if machining_s:
        command += ["--machining-s", str(machining_s)]
    for item in injections:
        command += ["--inject", item]
    return command


def run_collection(run: str, family: str, seed: int, cycles: int, rate: float,
                   machining_s: float | None, injections: list[str], frames: bool,
                   only_frames: bool = False) -> dict:
    """Colectează o sesiune în volum și spune ce a ieșit din ea.

    Un container, o sesiune: fizica este pe un singur fir, deci paralelismul vine din numărul de
    containere. Fișierele rămîn în volum pînă la urcare. Cu cadre, containerul are GPU și
    `MUJOCO_GL=egl`; fără, nu atinge niciun context grafic.
    """
    target = CORPUS_MOUNT / run / f"{family}-{seed}"
    command = collect_command(target, family, seed, cycles, rate, machining_s,
                              injections, frames, only_frames)
    print(f"[modal] {family}-{seed}: {' '.join(command)}", flush=True)
    finished = subprocess.run(command, check=False)
    if finished.returncode != 0:
        raise RuntimeError(f"colectarea {family}-{seed} a căzut cu {finished.returncode}")
    corpus.commit()
    return summarise_session(target, family, seed)


@app.function(image=image, volumes={str(CORPUS_MOUNT): corpus},
              cpu=1.0, memory=2048, timeout=6 * 3600)
def collect_session(run: str, family: str, seed: int, cycles: int, rate: float,
                    machining_s: float | None, injections: list[str]) -> dict:
    """Sesiune de senzori, pe CPU: fără cadre, fără GPU, fără context grafic."""
    return run_collection(run, family, seed, cycles, rate, machining_s, injections,
                          frames=False)


@app.function(image=image, volumes={str(CORPUS_MOUNT): corpus},
              gpu="T4", cpu=4.0, memory=8192, timeout=6 * 3600)
def collect_session_frames(run: str, family: str, seed: int, cycles: int, rate: float,
                           machining_s: float | None, injections: list[str],
                           only_frames: bool = True) -> dict:
    """Sesiune cu cadre, pe GPU: aceleași PNG-uri ca `make mj-collect` local.

    Implicit scrie **doar cadrele**: telemetria are deja corpusul ei, iar `run.py
    --only-frames` sare peste eșantioane, uzură și evenimente.
    """
    return run_collection(run, family, seed, cycles, rate, machining_s, injections,
                          frames=True, only_frames=only_frames)


def summarise_session(directory: Path, family: str, seed: int) -> dict:
    """Ce a scris sesiunea: cicluri, eșantioane, octeți. Se raportează pe mașina locală."""
    record = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    totals = record.get("totals", {})
    size = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
    return {"session": f"{family}-{seed}", "cycles": len(record.get("cycles", [])),
            "samples": int(totals.get("samples", 0)),
            "frames": int(totals.get("frames", 0)),
            "sim_time_s": float(totals.get("sim_time_s", 0.0)), "bytes": size}


@app.function(image=image, volumes={str(CORPUS_MOUNT): corpus}, timeout=10 * 60)
def existing_session(run: str, family: str, seed: int) -> dict | None:
    """Sumarul unei sesiuni deja scrise în volum, sau ``None`` dacă lipsește.

    Reluarea unei rulări sare peste sesiunile terminate, ca preempțiunea ori o singură prindere
    ratată să nu coste din nou toate ciclurile.
    """
    corpus.reload()
    target = CORPUS_MOUNT / run / f"{family}-{seed}"
    if not (target / "session.json").is_file() or not (target / "frames.jsonl").is_file():
        return None
    return summarise_session(target, family, seed)


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


@app.function(image=image, volumes={str(CORPUS_MOUNT): corpus},
              secrets=[modal.Secret.from_name(SECRET_NAME)], timeout=2 * 3600)
def publish_frames_run(run: str, bucket: str, revision: str = "",
                       keep_local: bool = False) -> str:
    """Urcă doar cadrele sesiunilor (`frames/`, index, `session.json`), sub `training-data/grasp`."""
    role_arn = os.environ.get("AWS_ROLE_ARN", "")
    region = os.environ.get("AWS_REGION", "eu-central-1")
    if not role_arn:
        raise RuntimeError(f"secretul {SECRET_NAME} nu are AWS_ROLE_ARN")
    corpus.reload()
    environment = {**os.environ, **sts_credentials(role_arn, region)}
    if revision:
        environment["CNCDT_GIT_REVISION"] = revision
    command = [sys.executable, f"{CONTAINER_MUJOCO}/publish_frames.py",
               "--run-dir", str(CORPUS_MOUNT / run), "--bucket", bucket,
               "--run", run, "--upload"]
    print(f"[modal] urcă: {' '.join(command)}", flush=True)
    finished = subprocess.run(command, check=False, env=environment)
    if finished.returncode != 0:
        raise RuntimeError(f"urcarea cadrelor a căzut cu {finished.returncode}: "
                           f"volumul rămîne neatins, pentru o reîncercare")
    if not keep_local:
        shutil.rmtree(CORPUS_MOUNT / run, ignore_errors=True)
        corpus.commit()
    return run


# ---------------------------------------------------------------- imagini de calitate (piese)
#: Subdirectorul din volum care ține dataseturile de piese: unul per rulare.
PARTS_MOUNT = "parts"

#: Generatorul procedural, montat din `simulation/scripts`.
PARTS_SCRIPT = f"{CONTAINER_REPO}/simulation/scripts/generate-parts.py"


def parts_dir(run: str) -> Path:
    return CORPUS_MOUNT / PARTS_MOUNT / run


def count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


@app.function(image=image, volumes={str(CORPUS_MOUNT): corpus},
              cpu=1.0, memory=2048, timeout=2 * 3600)
def prepare_parts(run: str, count: int, seed0: int, shuffle_seed: int) -> dict:
    """Scrie în volum manifestul de etichete și împărțirea train/val/test.

    Generarea este pură (numpy + `mapbox-earcut`), deci nu cere GPU; containerele de randare au
    nevoie doar de manifest, nu de o a doua generare.
    """
    corpus.reload()
    target = parts_dir(run)
    command = [sys.executable, PARTS_SCRIPT,
               "--count", str(count), "--seed0", str(seed0),
               "--shuffle-seed", str(shuffle_seed), "--balanced", "--split",
               "--out", str(target)]
    print(f"[modal] manifest: {' '.join(command)}", flush=True)
    finished = subprocess.run(command, check=False)
    if finished.returncode != 0:
        raise RuntimeError(f"generarea manifestului a căzut cu {finished.returncode}")
    corpus.commit()
    return {"parts": count_lines(target / "parts.jsonl")}


@app.function(image=image, volumes={str(CORPUS_MOUNT): corpus},
              gpu="T4", cpu=4.0, memory=8192, timeout=4 * 3600)
def render_part_shard(run: str, shard: int, shards: int, views: int, size: int,
                      render_seed: int) -> dict:
    """Randează o felie contiguă a manifestului, pe GPU, în directorul rulării.

    Feliile au blocuri discontigue de piese, deci scriu fișiere diferite în același director;
    indexul fiecăreia este separat (`images-shard-NN.jsonl`) și se unește la urcare. Poziția
    globală a piesei rămîne aceeași, ca unghiurile să nu depindă de împărțire.
    """
    corpus.reload()
    target = parts_dir(run)
    manifest = target / "parts.jsonl"
    if not manifest.is_file():
        raise RuntimeError(f"lipsește {manifest}: rulează întîi pregătirea manifestului")
    command = [sys.executable, f"{CONTAINER_MUJOCO}/render_parts.py",
               "--manifest", str(manifest), "--out", str(target),
               "--shard", str(shard), "--shards", str(shards),
               "--views", str(views), "--width", str(size), "--height", str(size),
               "--render-seed", str(render_seed)]
    print(f"[modal] felia {shard}/{shards}: {' '.join(command)}", flush=True)
    started = time.monotonic()
    finished = subprocess.run(command, check=False)
    if finished.returncode != 0:
        raise RuntimeError(f"randarea feliei {shard} a căzut cu {finished.returncode}")
    corpus.commit()
    index = (target / "images.jsonl" if shards == 1
             else target / f"images-shard-{shard:02d}.jsonl")
    return {"shard": shard, "images": count_lines(index),
            "seconds": time.monotonic() - started}


@app.function(image=image, volumes={str(CORPUS_MOUNT): corpus},
              secrets=[modal.Secret.from_name(SECRET_NAME)], timeout=2 * 3600)
def publish_parts_run(run: str, bucket: str, render_seed: int = 0, revision: str = "",
                      keep_local: bool = False) -> str:
    """Unește indexurile, verifică datasetul și îl urcă sub `training-data/parts/`."""
    role_arn = os.environ.get("AWS_ROLE_ARN", "")
    region = os.environ.get("AWS_REGION", "eu-central-1")
    if not role_arn:
        raise RuntimeError(f"secretul {SECRET_NAME} nu are AWS_ROLE_ARN")
    corpus.reload()
    environment = {**os.environ, **sts_credentials(role_arn, region)}
    if revision:
        environment["CNCDT_GIT_REVISION"] = revision
    command = [sys.executable, f"{CONTAINER_MUJOCO}/publish_parts.py",
               "--dataset-dir", str(parts_dir(run)), "--bucket", bucket,
               "--run", run, "--render-seed", str(render_seed), "--upload"]
    print(f"[modal] urcă: {' '.join(command)}", flush=True)
    finished = subprocess.run(command, check=False, env=environment)
    if finished.returncode != 0:
        raise RuntimeError(f"urcarea datasetului a căzut cu {finished.returncode}: "
                           f"volumul rămîne neatins, pentru o reîncercare")
    if not keep_local:
        shutil.rmtree(parts_dir(run), ignore_errors=True)
        corpus.commit()
    return run


@app.local_entrypoint()
def parts(count: int = 220, seed0: int = 0, shuffle_seed: int = 0,
          views: int = 4, size: int = 640, shards: int = 8, render_seed: int = 0,
          bucket: str = "", run: str = "", keep_local: bool = False,
          upload: bool = True) -> None:
    """Generează manifestul, randează feliile pe GPU și urcă datasetul de piese."""
    if count < 1:
        raise SystemExit("--count trebuie să fie cel puțin 1")
    if shards < 1:
        raise SystemExit("--shards trebuie să fie cel puțin 1")
    if not bucket:
        raise SystemExit("lipsește bucketul: dă `--bucket` (din `make training-data-status`)")
    name = run or "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    revision = git_revision()
    print(f"[modal] piese: {count} variante × {views} cadre la {size}×{size}, "
          f"în {shards} felii GPU", flush=True)
    print(f"[modal] urcă în s3://{bucket}/training-data/parts/dt=<zi UTC>/{name}/", flush=True)

    prepared = prepare_parts.remote(run=name, count=count, seed0=seed0,
                                    shuffle_seed=shuffle_seed)
    print(f"[modal] manifest: {prepared['parts']} piese", flush=True)

    handles = [render_part_shard.spawn(run=name, shard=shard, shards=shards, views=views,
                                       size=size, render_seed=render_seed)
               for shard in range(shards)]
    rows = [handle.get() for handle in handles]
    for row in sorted(rows, key=lambda item: item["shard"]):
        print(f"[modal] felia {row['shard']:>2}: {row['images']:>4} imagini, "
              f"{row['seconds']:6.1f} s", flush=True)
    print(f"[modal] total: {sum(row['images'] for row in rows)} imagini", flush=True)

    if not upload:
        print(f"[modal] urcare sărită: datele rămîn în volumul Modal, la "
              f"{PARTS_MOUNT}/{name}", flush=True)
        return
    published = publish_parts_run.remote(run=name, bucket=bucket, render_seed=render_seed,
                                         revision=revision, keep_local=keep_local)
    print(f"[modal] gata: {published} în s3://{bucket}/training-data/parts/", flush=True)


@app.local_entrypoint()
def main(families: str = "", cycles: int = 25, rate: float = 20.0, seed: int = 100,
         sessions_per_family: int = 1, machining_s: float = 0.0, inject: str = "",
         require_wear: bool = True, frames: bool = False, telemetry: bool = False,
         bucket: str = "", run: str = "", keep_local: bool = False,
         upload: bool = True, resume: bool = False) -> None:
    """Lansează sesiunile în paralel, apoi urcă rularea întreagă.

    Cu `--frames` și fără `--telemetry` (implicit), sesiunile scriu doar cadrele și urcă sub
    `training-data/grasp/`: telemetria are deja corpusul ei. `--telemetry` readuce sesiunea
    completă, cu senzori, uzură și evenimente, sub `training-data/mujoco/`.
    """
    family_list = split_list(families)
    if not family_list:
        raise SystemExit("lipsește lista de familii: dă `--families \"wheel_100 hex_68\"`")
    if not bucket:
        raise SystemExit("lipsește bucketul: dă `--bucket` (din `make training-data-status`)")
    sessions = [(family, session_seeds(seed, index, sessions_per_family))
                for index, family in enumerate(family_list)]
    total = sum(len(seeds) for _, seeds in sessions)
    name = run or "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    # Revisia se citește la lansare, nu la urcare: containerele primesc codul acum, iar între
    # timp un commit de documentație ar intra în manifest fără să fi trecut prin simulare.
    revision = git_revision()
    injections = split_list(inject)
    worker = collect_session_frames if frames else collect_session
    only_frames = bool(frames and not telemetry)
    frame_note = "cu cadre, pe GPU T4" if frames else "fără cadre, pe CPU"
    destination = "training-data/grasp" if only_frames else "training-data/mujoco"
    print(f"[modal] rularea {name}: {total} sesiuni, {cycles} cicluri, rata {rate}, "
          f"{frame_note}, în {len(sessions)} familii", flush=True)
    if only_frames:
        print("[modal] se scriu doar cadrele; telemetria există deja", flush=True)
    print(f"[modal] urcă în s3://{bucket}/{destination}/dt=<zi UTC>/{name}/", flush=True)

    rows: list[dict] = []
    handles = []
    for family, seeds in sessions:
        for one_seed in seeds:
            if resume:
                existing = existing_session.remote(run=name, family=family, seed=one_seed)
                if existing is not None:
                    print(f"[modal] {family}-{one_seed}: deja scrisă, sar peste", flush=True)
                    rows.append(existing)
                    continue
            arguments = {"run": name, "family": family, "seed": one_seed, "cycles": cycles,
                         "rate": rate, "machining_s": machining_s or None,
                         "injections": injections}
            if frames:
                arguments["only_frames"] = only_frames
            handles.append(worker.spawn(**arguments))

    for handle in handles:
        rows.append(handle.get())
    for row in rows:
        print(f"[modal] {row['session']:<20} {row['cycles']:>4} cicluri, "
              f"{row['samples']:>7} eșantioane, {row['frames']:>5} cadre, "
              f"{row['sim_time_s'] / 60:6.1f} min simulate, "
              f"{row['bytes'] / 1024 / 1024:6.2f} MB", flush=True)
    print(f"[modal] total: {sum(row['samples'] for row in rows)} eșantioane, "
          f"{sum(row['frames'] for row in rows)} cadre, "
          f"{sum(row['bytes'] for row in rows) / 1024 / 1024:.2f} MB", flush=True)

    if not upload:
        print(f"[modal] urcare sărită: datele rămîn în volumul Modal, la {name}", flush=True)
        return
    if only_frames:
        published = publish_frames_run.remote(run=name, bucket=bucket, revision=revision,
                                              keep_local=keep_local)
    else:
        published = publish_run.remote(run=name, bucket=bucket, require_wear=require_wear,
                                       revision=revision, keep_local=keep_local)
    print(f"[modal] gata: {published} în s3://{bucket}/{destination}/", flush=True)
