"""Ce iese dintr-o sesiune: eșantioanele brute, cadrele de inspecție și fișierele sesiunii.

Contractele sunt cele publicate, nu unele noi. Eșantionul are **exact** câmpurile din
`telemetry/schemas/cnc.telemetry.v1.json` (schema are `additionalProperties: false`, iar Firehose
cade la conversia Parquet dacă apare un câmp în plus), iar metadatele cadrului de inspecție sunt
cele din `cnc.qa.snapshot.v1.json`. Validarea se face aici, cu un validator mic peste fișierul de
schemă: nu se adaugă `jsonschema` ca dependență (vezi `telemetry/schemas/README.md`).

Mărimile care nu au coloană în contract — efortul pe braț, forța falcilor, poziția și viteza
piesei, contactele — nu se strecoară în eșantion: se scriu alături, în `readings.jsonl`, ca
diagnostic al sesiunii. Tot acolo ajung, în `wear.jsonl`, sănătatea fiecărei componente, și în
`events.jsonl`, eșecurile și acțiunile de operator: fișierul de etichete al detectorului.

Mărimile așchierii stau în :class:`MachineReading`, sub numele din contract, și le compune motorul
de uzură (`wear.WearModel.reading`): scena nu are axele mașinii, dar uzura sculei, a axului și a
răcirii se vede în eșantion, fără să fie nevoie de geometrie mișcătoare.

**Fără MuJoCo**: modulul se importă din `.venv`-ul proiectului, unde motorul nu există (ADR 0012).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import REPO_ROOT

#: Schemele publicate; validarea citește exact fișierele care intră în pipeline.
SCHEMAS_DIR = REPO_ROOT / "telemetry" / "schemas"

#: Fișierele unei sesiuni, în directorul ei. Numele intră și în `session.json`.
SAMPLE_FILE = "samples.jsonl"
SNAPSHOT_FILE = "snapshots.jsonl"
FRAME_FILE = "frames.jsonl"
READING_FILE = "readings.jsonl"
WEAR_FILE = "wear.jsonl"
EVENT_FILE = "events.jsonl"
FRAMES_DIR = "frames"
SESSION_FILE = "session.json"

#: Identitatea celulei. Implicit aceleași valori ca extensia Isaac Sim (`cnc_cell/config.py`), ca
#: eșantioanele geamănului să arate ca ale celulei reale; se pot schimba din mediu.
ENVIRONMENT = os.environ.get("CNCDT_ENVIRONMENT", "dev")
DEVICE_ID = os.environ.get("CNCDT_DEVICE_ID", "cnc-cell-01")
CELL_ID = os.environ.get("CNCDT_CELL_ID", "cell-a")
TELEMETRY_TOPIC = os.environ.get("CNCDT_TELEMETRY_TOPIC",
                                 f"cncdt/{ENVIRONMENT}/cnc/{CELL_ID}/telemetry")

#: Prefixul din bucketul de date sub care ajung metadatele cadrelor de inspecție. Notificarea S3 a
#: Lambda-ului de inspecție filtrează exact pe el.
QA_SNAPSHOT_PREFIX = os.environ.get("CNCDT_QA_PREFIX", "raw/qa-snapshots")

#: Timpul de pornire al sesiunii, în milisecunde epoch. Fix, ca două rulări cu aceeași sămânță să
#: dea fișiere identice (criteriul fazei 3); `--start-ms` îl pune pe ceasul real al unei sesiuni vii.
SESSION_EPOCH_MS = 1_760_000_000_000

#: Enumerația stărilor mașinii, exact cea din contract.
MACHINE_STATES = ("IDLE", "LOADING", "CUTTING", "UNLOADING", "INSPECTING")

#: Starea mașinii în care se află celula când nu lucrează: transportul benzilor și așezarea.
IDLE_STATE = "IDLE"

#: Câmpuri care poartă răspunsul. Nu au ce căuta într-un cadru de inspecție: fișierul JSON
#: pornește inferența, iar inferența nu are voie să știe ce trebuie să deducă. Un model care
#: primește `tool_wear_index` (= `1 - h`) ori `is_synthetic_defect` citește parametrul intern al
#: simulării, nu o măsurătoare, iar pe linia reală coloanele nu există.
#:
#: Geamănul nu are cum să le scrie: `qa_snapshot` nu are parametru pentru ele, iar schema
#: `cnc.qa.snapshot.v1.json` este închisă. Aceleași nume sunt păzite de scriitorul din Kit
#: (``cnc_cell/telemetry/qa_camera.py``, care primește un dicționar de context) și de cititorul
#: din Lambda (``lambdas/qa_vision_inference/qa_vision_core.py``).
LABEL_FIELDS: tuple[str, ...] = (
    "is_synthetic_defect",
    "tool_wear_index",
    "joint_friction_index",
    "surface_quality",
    "family",
    "defect",
    "verdict",
)


@dataclass(frozen=True)
class MachineReading:
    """Mărimile mașinii dintr-un eșantion, sub numele din contract.

    Le compune motorul de uzură (`wear.WearModel.reading`): scula uzată încarcă axul și scade
    calitatea suprafeței, rulmentul obținut tremură și încălzește, pompa uzată dă mai puțin debit.
    Câmpul `coolant_fault` este chiar coloana din contract, adică felul în care simularea semnalează
    o pană de răcire injectată.
    """

    spindle_load: float = 0.0
    vibration: float = 0.0
    temp_c: float = 22.0
    coolant_flow_lpm: float = 0.0
    axis_feed: float = 0.0
    tool_wear_index: float = 0.0
    joint_friction_index: float = 0.0
    surface_quality: float = 1.0
    coolant_fault: bool = False


#: Starea mașinii la fiecare punct de lucru al ciclului. Se citește din ruta brațelor, nu din
#: ordinea în care se întâmplă lucrurile: brațul 1 încarcă și descarcă mașina, brațul 2 inspectează
#: și sortează.
STAGE_STATES: Mapping[str, str] = {
    "belt1_pick": "LOADING",
    "cnc_chuck": "LOADING",
    "belt2_drop": "UNLOADING",
    "belt2_pick": "INSPECTING",
}


def cycle_id(index: int) -> str:
    """Identificatorul unui ciclu, în formatul din contract (`cyc-000001`)."""
    if not 1 <= index <= 999_999:
        raise ValueError(f"numărul ciclului iese din formatul contractului: {index}")
    return f"cyc-{index:06d}"


def part_id(index: int) -> str:
    """Identificatorul de trasabilitate al piesei din ciclul dat (`part-000001`).

    Nu este identificatorul variantei din generator (`part-00000007`, opt cifre): acela este
    eticheta de antrenare și se scrie alături, în `variant_id`.
    """
    if not 1 <= index <= 999_999:
        raise ValueError(f"numărul piesei iese din formatul contractului: {index}")
    return f"part-{index:06d}"


def stage_state(stage: str) -> str:
    """Starea mașinii la un punct de lucru al ciclului.

    Locurile din raft primesc `UNLOADING`: acolo se descarcă piesa din celulă. `CUTTING` nu apare
    încă nicăieri, fiindcă scena nu are axele mașinii: starea există în contract, iar prelucrarea
    o va aduce cu ea (`docs/11-geaman-mujoco.md` §8, faza 2).
    """
    if stage in STAGE_STATES:
        return STAGE_STATES[stage]
    if stage.startswith("rack_"):
        return "UNLOADING"
    raise KeyError(f"punct de lucru fără stare a mașinii: {stage}")


def machine_state_for(stage: str | None) -> str:
    """Starea mașinii pentru un moment al ciclului; fără punct de lucru, celula nu lucrează."""
    return IDLE_STATE if stage is None else stage_state(stage)


def iso_time(ts_ms: int) -> str:
    """Marcajul de timp epoch, în forma ISO cerută de metadatele cadrului de inspecție."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------- validarea, peste fișierul de schemă


@lru_cache(maxsize=8)
def load_schema(path: str | Path) -> dict[str, Any]:
    """Schema JSON, citită o dată pe proces."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _check_type(value: Any, expected: str) -> bool:
    """Potrivirea de tip, cu `bool` scos din numere (`True` nu este un număr valid)."""
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _check(value: Any, rule: Mapping[str, Any], path: str, errors: list[str]) -> None:
    """Verifică o valoare față de regula ei, adunând toate nepotrivirile."""
    expected = rule.get("type")
    if expected is not None and not _check_type(value, str(expected)):
        errors.append(f"{path}: se aștepta {expected}, a venit {type(value).__name__}")
        return
    if "const" in rule and value != rule["const"]:
        errors.append(f"{path}: trebuia {rule['const']!r}, a venit {value!r}")
    if "enum" in rule and value not in rule["enum"]:
        errors.append(f"{path}: {value!r} nu este în {list(rule['enum'])}")
    if "pattern" in rule and isinstance(value, str) \
            and re.fullmatch(str(rule["pattern"]), value) is None:
        errors.append(f"{path}: {value!r} nu respectă {rule['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in rule and value < rule["minimum"]:
            errors.append(f"{path}: {value} sub minimul {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:
            errors.append(f"{path}: {value} peste maximul {rule['maximum']}")
    if isinstance(value, list):
        if "minItems" in rule and len(value) < rule["minItems"]:
            errors.append(f"{path}: {len(value)} elemente, minim {rule['minItems']}")
        if "maxItems" in rule and len(value) > rule["maxItems"]:
            errors.append(f"{path}: {len(value)} elemente, maxim {rule['maxItems']}")
        if "items" in rule:
            for index, item in enumerate(value):
                _check(item, rule["items"], f"{path}[{index}]", errors)


def validate(record: Mapping[str, Any], schema: Mapping[str, Any]) -> list[str]:
    """Nepotrivirile dintre o înregistrare și schema ei; listă goală înseamnă conformitate.

    Validatorul acoperă exact construcțiile din schemele proiectului: tip, `const`, `enum`,
    `pattern`, `minimum`/`maximum`, `required`, `additionalProperties`, `items` cu `minItems` și
    `maxItems`. Nu este un validator general de JSON Schema, și nu se folosește ca unul.
    """
    errors: list[str] = []
    if schema.get("type") != "object":
        return [f"schema neașteptată: type={schema.get('type')!r}"]
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in record:
            errors.append(f"{key}: lipsește")
    if schema.get("additionalProperties") is False:
        for key in sorted(set(record) - set(properties)):
            errors.append(f"{key}: câmp în afara contractului")
    for key, value in record.items():
        rule = properties.get(key)
        if rule is not None:
            _check(value, rule, key, errors)
    return errors


def validate_sample(record: Mapping[str, Any]) -> list[str]:
    """Eșantionul brut, față de `cnc.telemetry.v1.json`."""
    return validate(record, load_schema(SCHEMAS_DIR / "cnc.telemetry.v1.json"))


def validate_snapshot(record: Mapping[str, Any]) -> list[str]:
    """Metadatele cadrului de inspecție, față de `cnc.qa.snapshot.v1.json`."""
    return validate(record, load_schema(SCHEMAS_DIR / "cnc.qa.snapshot.v1.json"))


def _require(errors: Sequence[str], what: str) -> None:
    """Ridică eroare dacă validarea a găsit ceva: nimic nevalid nu ajunge pe disc."""
    if errors:
        raise ValueError(f"{what} iese din contract: " + "; ".join(errors))


# ---------------------------------------------------------------- eșantionul brut


def sample(*, seq: int, ts_ms: int, machine_state: str, cycle: str, part: str,
           joint_effort: float, machine: MachineReading = MachineReading(),
           is_synthetic_defect: bool = False) -> dict[str, Any]:
    """Un eșantion brut, cu exact câmpurile din contract.

    `joint_effort` este măsurat în scenă (`sensors.joint_effort_nm`), restul mărimilor mașinii vin
    din :class:`MachineReading`. `is_synthetic_defect` este adevărat când piesa ciclului are un
    defect cerut de operator, nu unul ieșit din sămânță.
    """
    if machine_state not in MACHINE_STATES:
        raise ValueError(f"stare de mașină necunoscută: {machine_state}")
    record = {
        "schema_version": "1.0.0",
        "device_id": DEVICE_ID,
        "cell_id": CELL_ID,
        "topic": TELEMETRY_TOPIC,
        "ts_ms": int(ts_ms),
        "machine_state": machine_state,
        "cycle_id": cycle,
        "part_id": part,
        "sample_seq": int(seq),
        "spindle_load": float(machine.spindle_load),
        "vibration": float(machine.vibration),
        "temp_c": float(machine.temp_c),
        "coolant_flow_lpm": float(machine.coolant_flow_lpm),
        "joint_effort": float(joint_effort),
        "axis_feed": float(machine.axis_feed),
        "tool_wear_index": float(machine.tool_wear_index),
        "joint_friction_index": float(machine.joint_friction_index),
        "surface_quality": float(machine.surface_quality),
        "coolant_fault": bool(machine.coolant_fault),
        "is_synthetic_defect": bool(is_synthetic_defect),
    }
    _require(validate_sample(record), "eșantionul")
    return record


# ---------------------------------------------------------------- cadrul de inspecție


def qa_snapshot(*, ts_ms: int, cycle: str, part: str, role: str, camera: str,
                resolution: Sequence[int], image_key: str, machine_state: str,
                image_captured: bool = True) -> dict[str, Any]:
    """Metadatele unui cadru de inspecție, cu exact câmpurile cerute de contract.

    Fișierul JSON este cel care declanșează Lambda-ul de inspecție, iar imaginea stă alături, sub
    același nume cu extensia `.png`: `image_key` este cheia din bucket, exact calea relativă a
    fișierului din sesiune.

    Cadrul nu conține nimic din ce a decis geometria piesei. Indiciile de uzură și flagul de
    defect cerut au ieșit din snapshot odată cu versiunea 1.2.0: ele sunt parametrul intern al
    simulării, iar modelul antrenat trebuie să deducă verdictul din imagine. Eticheta rămâne în
    `frames.jsonl` și în manifestul de etichete, legată prin `part_id`, de unde o citește doar
    antrenarea.
    """
    record = {
        "schema_version": "1.2.0",
        "captured_at": iso_time(ts_ms),
        "cycle_id": cycle,
        "part_id": part,
        "camera_role": role,
        "image_captured": bool(image_captured),
        "image_key": image_key,
        "camera": camera,
        "resolution": [int(value) for value in resolution],
        "device_id": DEVICE_ID,
        "cell_id": CELL_ID,
        "machine_state": machine_state,
        "telemetry_topic": TELEMETRY_TOPIC,
    }
    _require(validate_snapshot(record), "metadatele cadrului")
    return record


# ---------------------------------------------------------------- etichetele sesiunii


def frame_row(*, ts_ms: int, cycle: str, part: str, pov: str, stage: str, camera: str | None,
              resolution: Sequence[int], path: str, sim_time_s: float, machine_state: str,
              label: Mapping[str, Any]) -> dict[str, Any]:
    """Un rând din `frames.jsonl`: cadrul, momentul lui și eticheta piesei din el.

    Aici stă datasetul de antrenare: fiecare cadru al fiecărui punct de vedere, cu familia,
    defectul și verdictul piesei. `label` este eticheta dată de generatorul procedural
    (`part.describe`), deci `defect == "PASS"` înseamnă o piesă conformă.
    """
    return {
        "ts_ms": int(ts_ms),
        "sim_time_s": round(float(sim_time_s), 6),
        "cycle_id": cycle,
        "part_id": part,
        "pov": pov,
        "stage": stage,
        "camera": camera,
        "resolution": [int(value) for value in resolution],
        "path": path,
        "machine_state": machine_state,
        "variant_id": str(label["part_id"]),
        "family": str(label["family"]),
        "defect": str(label["defect"]),
        "verdict": str(label["verdict"]),
        "is_synthetic_defect": str(label["defect"]) != "PASS",
    }


def cycle_record(*, index: int, seed: int, label: Mapping[str, Any], sim_time_s: float,
                 samples: int, frames: Sequence[str],
                 grasps: Sequence[Mapping[str, Any]] = (),
                 failures: Sequence[str] = (),
                 route: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Etichetele unui ciclu: identificatorii din contract și eticheta de antrenare a piesei.

    `grasps` este jurnalul fiecărei prinderi (braț, corp, moment, contact, deschidere), iar
    `failures` listează verificările căzute. Amîndouă intră în `session.json`, fiindcă un ciclu
    care nu și-a dus piesa în raft nu are ce căuta într-un corpus verde. `route` este ruta
    verdictului (compartiment ori rebut), cu familia citită de model, cînd ciclul a fost sortat.
    """
    return {
        "cycle_id": cycle_id(index),
        "part_id": part_id(index),
        "seed": int(seed),
        "variant_id": str(label["part_id"]),
        "family": str(label["family"]),
        "defect": str(label["defect"]),
        "verdict": str(label["verdict"]),
        "sim_time_s": round(float(sim_time_s), 6),
        "samples": int(samples),
        "frames": list(frames),
        "grasps": [dict(grasp) for grasp in grasps],
        "grasp_success": bool(grasps) and all(bool(row["success"]) for row in grasps),
        "failures": list(failures),
        "route": dict(route) if route else None,
    }


# ---------------------------------------------------------------- uzura și evenimentele


def wear_row(*, ts_ms: int, sim_time_s: float, cycle: str, machine_state: str,
             snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Un rând din `wear.jsonl`: sănătatea componentelor, aliniată cu eșantioanele.

    Uzura nu are coloană în contract (singurele urme sunt `tool_wear_index`,
    `joint_friction_index` și `surface_quality`), dar detectorul are nevoie de traiectoria
    completă, pe componente, ca să știe ce s-a stricat și când. Rândul se scrie la fiecare
    eșantion, deci se aliniază pe `sim_time_s` cu `samples.jsonl`.
    """
    return {
        "ts_ms": int(ts_ms),
        "sim_time_s": round(float(sim_time_s), 6),
        "cycle_id": cycle,
        "machine_state": machine_state,
        **{key: value for key, value in snapshot.items()},
    }


def event_row(*, ts_ms: int, sim_time_s: float, cycle: str, event: str, kind: str,
              component: str, detail: str = "", severity: str = "",
              health: float | None = None, duration_s: float | None = None) -> dict[str, Any]:
    """Un rând din `events.jsonl`: un eșec ori o acțiune de operator.

    Acesta este fișierul de **etichete**: `event` spune dacă s-a stricat ceva ori dacă a intervenit
    cineva, `kind` spune ce anume, iar `sim_time_s` leagă eticheta de eșantioanele din jur. Fără
    el, un detector antrenat pe ferestre nu ar avea de unde să învețe preavizul.
    """
    row: dict[str, Any] = {
        "ts_ms": int(ts_ms),
        "sim_time_s": round(float(sim_time_s), 6),
        "cycle_id": cycle,
        "event": event,
        "kind": kind,
        "component": component,
        "detail": detail,
    }
    if health is not None:
        row["health"] = round(float(health), 6)
        row["severity"] = severity
    if duration_s is not None:
        row["duration_s"] = round(float(duration_s), 6)
    return row


def session_record(*, session: str, scenario: Mapping[str, Any], totals: Mapping[str, Any],
                   cycles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """`session.json`: cu ce parametri s-a generat sesiunea, ce conține și ce etichete are.

    Cifrele care se schimbă cu versiunea de MuJoCo — versiunea, pasul de integrare, rata de
    eșantionare — stau aici, ca fișierele să se poată compara între rulări (`docs/11-geaman-mujoco.md`
    §9, determinismul are limite).
    """
    return {
        "session": session,
        "scenario": dict(scenario),
        "totals": dict(totals),
        "files": {
            "samples": SAMPLE_FILE,
            "snapshots": SNAPSHOT_FILE,
            "frames": FRAME_FILE,
            "readings": READING_FILE,
            "wear": WEAR_FILE,
            "events": EVENT_FILE,
            "frames_dir": FRAMES_DIR,
        },
        "schemas": {
            "samples": load_schema(SCHEMAS_DIR / "cnc.telemetry.v1.json")["properties"]
            ["schema_version"]["const"],
            "snapshots": load_schema(SCHEMAS_DIR / "cnc.qa.snapshot.v1.json")["properties"]
            ["schema_version"]["const"],
        },
        "cycles": [dict(cycle) for cycle in cycles],
    }


# ---------------------------------------------------------------- scrierea fișierelor


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    """Scrie rândurile ca JSON Lines, unul pe linie; întoarce câte au fost."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Scrie un obiect JSON, indentat, ca să se poată citi și cu ochii."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
                    encoding="utf-8")
