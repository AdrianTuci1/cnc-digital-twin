"""Citirea corpusului MuJoCo pentru notebook-uri și antrenare.

Geamănul scrie o sesiune ca un director cu `samples.jsonl` (eșantioane brute la ~10 Hz),
`wear.jsonl` (sănătatea componentelor), `events.jsonl` (eșecuri și acțiuni) și `frames.jsonl`
(cadrele de inspecție, cu eticheta piesei). Aici stau funcțiile mici care leagă fișierele de pe
disc de modulele pure din :mod:`cncdt_ml.features`, :mod:`cncdt_ml.windows` și
:mod:`cncdt_ml.offline`.

Modulul este **pur**: fără MuJoCo, fără pandas, fără AWS. Notebook-urile, testele și un viitor
`train.py` îl pot importa direct din `.venv`-ul proiectului.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .features import compute_window_features, enriched_record
from .windows import group_windows, window_start_ms

#: Numele fișierelor dintr-o sesiune, aceleași pe care le scrie `cncdt_mj.telemetry`.
SAMPLE_FILE = "samples.jsonl"
FRAME_FILE = "frames.jsonl"
WEAR_FILE = "wear.jsonl"
EVENT_FILE = "events.jsonl"
SESSION_FILE = "session.json"
FRAMES_DIR = "frames"


@dataclass(frozen=True)
class SessionInfo:
    """Ce declară `session.json`, fără să încarce eșantioanele."""

    name: str
    directory: Path
    family: str
    seed: int
    cycles: int
    samples: int
    frames: int
    sim_time_s: float

    def record(self) -> dict[str, Any]:
        """Rândul de audit, gata de tipărit ori de scris într-un JSON."""
        return {
            "name": self.name,
            "directory": str(self.directory),
            "family": self.family,
            "seed": self.seed,
            "cycles": self.cycles,
            "samples": self.samples,
            "frames": self.frames,
            "sim_time_s": round(self.sim_time_s, 6),
        }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Rândurile unui JSON Lines; un fișier lipsă înseamnă listă goală."""
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def session_directories(root: Path) -> tuple[Path, ...]:
    """Directoarele cu `session.json` de sub rădăcina dată, recursive și sortate."""
    if not root.is_dir():
        return ()
    return tuple(sorted(path.parent for path in root.rglob(SESSION_FILE) if path.is_file()))


def session_info(directory: Path) -> SessionInfo:
    """Citește metadatele unei sesiuni; câmpurile lipsă primesc valori neutre."""
    record = json.loads((directory / SESSION_FILE).read_text(encoding="utf-8"))
    scenario = record.get("scenario") or {}
    totals = record.get("totals") or {}
    return SessionInfo(
        name=directory.name,
        directory=directory,
        family=str(scenario.get("family", "")),
        seed=int(scenario.get("seed", 0)),
        cycles=len(record.get("cycles") or []),
        samples=int(totals.get("samples", 0)),
        frames=int(totals.get("frames", 0)),
        sim_time_s=float(totals.get("sim_time_s", 0.0)),
    )


#: Pragurile etichetelor MuJoCo. Sunt aceleași valori ca în `cncdt_ml.labels`, pentru uzura
#: sculei și frecarea articulațiilor; coolantul are nevoie de altă calibrare (vezi mai jos).
TOOL_WEAR_REPLACE = 0.75
JOINT_FRICTION_REPAIR = 0.70
TEMP_CRITICAL_C = 78.0


def _number(record: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    """Valoarea numerică a unui câmp, cu fallback pentru None și bool."""
    value = record.get(key)
    if isinstance(value, bool) or value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def window_label(record: Mapping[str, Any]) -> str:
    """Eticheta unei ferestre MuJoCo, calibrată pe scara geamănului.

    `cncdt_ml.labels.health_label` folosește pragul de coolant al celulei Isaac Sim (3 LPM).
    În MuJoCo, media pe fereastră este ~1,8 LPM fiindcă mașina stă mult în `IDLE`, deci acel
    prag ar eticheta aproape orice fereastră ca `coolant`. Aici coolantul nu decide eticheta:
    se folosesc defectul majoritar al pieselor, uzura maximă a sculei, frecarea maximă a
    articulațiilor și temperatura maximă. Calibrarea coolantului rămâne un pas separat, după o
    colectare cu ferestre numai în `CUTTING`.
    """
    if record.get("defect_majority"):
        return "defect"
    if _number(record, "tool_wear_index_max") >= TOOL_WEAR_REPLACE:
        return "tool_wear"
    if _number(record, "joint_friction_index_max") >= JOINT_FRICTION_REPAIR:
        return "joint_friction"
    if _number(record, "temp_c_max") >= TEMP_CRITICAL_C:
        return "thermal"
    return "healthy"


def build_windows(directory: Path, window_s: int = 60) -> list[dict[str, Any]]:
    """Ferestrele unei sesiuni, pe contractul îmbogățit.

    Fiecare fereastră trece prin :func:`cncdt_ml.features.compute_window_features` și
    :func:`cncdt_ml.features.enriched_record`, deci are exact coloanele pe care le vede și
    Lambda-ul. Se adaugă `window_key`, `window_start_ms`, `session` și `family`, ca notebook-ul
    să poată grupa și evalua fără să ghicească din ordinea rândurilor.

    Pentru etichete se adaugă agregările care nu încap în contract: proporția de piese cu defect
    din fereastră (`defect_share`, `defect_majority`), uzura maximă a sculei, frecarea maximă a
    articulațiilor și temperatura maximă. `twin_label` și `healthy` se calculează din ele cu
    :func:`window_label`.
    """
    samples = read_jsonl(directory / SAMPLE_FILE)
    if not samples:
        return []
    info = session_info(directory)
    windows: list[dict[str, Any]] = []
    for key, chunk in sorted(group_windows(samples, window_s).items()):
        features = compute_window_features(chunk)
        record = enriched_record(features, raw_samples=chunk, window_s=window_s)
        defect_flags = [bool(sample.get("is_synthetic_defect")) for sample in chunk]
        record["window_key"] = key
        record["window_start_ms"] = window_start_ms(key, window_s)
        record["session"] = info.name
        record["family"] = info.family
        record["defect_share"] = round(sum(defect_flags) / len(defect_flags), 6)
        record["defect_majority"] = sum(defect_flags) * 2 > len(defect_flags)
        record["tool_wear_index_max"] = round(
            max(_number(sample, "tool_wear_index") for sample in chunk), 6
        )
        record["joint_friction_index_max"] = round(
            max(_number(sample, "joint_friction_index") for sample in chunk), 6
        )
        record["temp_c_max"] = round(features.temp_c_max, 6)
        record["twin_label"] = window_label(record)
        record["healthy"] = record["twin_label"] == "healthy"
        windows.append(record)
    return windows


def load_wear(directory: Path) -> list[dict[str, Any]]:
    """Sănătatea componentelor, un rând per eșantion."""
    return read_jsonl(directory / WEAR_FILE)


def load_events(directory: Path) -> list[dict[str, Any]]:
    """Eșecurile și acțiunile operatorului: etichetele episoadelor."""
    return read_jsonl(directory / EVENT_FILE)


def load_frames(directory: Path) -> list[dict[str, Any]]:
    """Metadatele cadrelor de inspecție, cu eticheta piesei."""
    return read_jsonl(directory / FRAME_FILE)


def frame_path(directory: Path, row: Mapping[str, Any]) -> Path:
    """Calea absolută a unui cadru, din câmpul `path` al rândului."""
    return directory / str(row.get("path", ""))


def corpus_summary(root: Path) -> list[dict[str, Any]]:
    """Auditul tuturor sesiunilor de sub rădăcină, ca listă de rânduri."""
    return [session_info(directory).record() for directory in session_directories(root)]
