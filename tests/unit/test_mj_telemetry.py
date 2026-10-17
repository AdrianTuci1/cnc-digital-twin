"""Teste pentru ce iese din sesiunea de colectare a geamănului MuJoCo: contracte, puncte de vedere.

Sesiunea în sine (`simulation/mujoco/run.py`) are nevoie de motor, fiindcă randează cadre. Ce se
scrie însă în fișiere este pur: eșantionul are exact câmpurile din `cnc.telemetry.v1.json`,
metadatele cadrului sunt cele din `cnc.qa.snapshot.v1.json`, iar planul capturii este registrul din
`cncdt_mj/povs.py`. Testele de aici apără tocmai aceste trei contracte, plus faptul că fiecare punct
de vedere se fotografiază măcar o dată pe ciclu.

Rularea completă — cadre scrise, sesiune verde — este `make mj-record`, iar verificarea scenei
`make mj-check` (ADR 0012).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from cncdt_mj import layout, moves, part, povs, telemetry, wear

TELEMETRY_SCHEMA = telemetry.SCHEMAS_DIR / "cnc.telemetry.v1.json"
SNAPSHOT_SCHEMA = telemetry.SCHEMAS_DIR / "cnc.qa.snapshot.v1.json"

CYCLE = telemetry.cycle_id(1)
PART = telemetry.part_id(1)


def _sample(**overrides: object) -> dict:
    """Un eșantion nominal, cu câmpurile cerute de contract."""
    fields: dict = {
        "seq": 1,
        "ts_ms": telemetry.SESSION_EPOCH_MS,
        "machine_state": "LOADING",
        "cycle": CYCLE,
        "part": PART,
        "joint_effort": 4.2,
    }
    fields.update(overrides)
    return telemetry.sample(**fields)


def _snapshot(role: str = povs.ROLE_SORTING, **overrides: object) -> dict:
    """Metadatele unui cadru de inspecție, cu câmpurile cerute de contract."""
    fields: dict = {
        "ts_ms": telemetry.SESSION_EPOCH_MS,
        "cycle": CYCLE,
        "part": PART,
        "role": role,
        "camera": "arm2_wrist_camera",
        "resolution": povs.PROCESS_FRAME,
        "image_key": f"{telemetry.QA_SNAPSHOT_PREFIX}/{CYCLE}/arm2_wrist-belt2_pick",
        "machine_state": "INSPECTING",
    }
    fields.update(overrides)
    return telemetry.qa_snapshot(**fields)


# ---------------------------------------------------------------- eșantionul brut


def test_esantionul_are_exact_campurile_din_contract() -> None:
    """Eșantionul scris în sesiune are exact cheile schemei, nici mai multe, nici mai puține.

    Schema are `additionalProperties: false`, iar Firehose cade la conversia Parquet dacă
    înregistrarea are câmpuri în plus, deci egalitatea se cere în ambele sensuri.
    """
    schema = telemetry.load_schema(TELEMETRY_SCHEMA)
    record = _sample()
    assert set(record) == set(schema["properties"])
    assert set(schema["required"]) <= set(record)
    assert telemetry.validate_sample(record) == []


def test_validarea_prinde_ce_iese_din_contract() -> None:
    """Validatorul prinde câmpul în plus, identificatorul scris greșit și numărul care e boolean."""
    record = _sample()
    assert telemetry.validate_sample({**record, "camera": "x"}), "câmpul în plus a trecut"
    assert telemetry.validate_sample({**record, "cycle_id": "1"}), "ciclu fără formatul cerut"
    assert telemetry.validate_sample({**record, "joint_effort": True}), "boolean trecut ca număr"
    assert telemetry.validate_sample({**record, "machine_state": "PAUZA"}), "stare necunoscută"


def test_esantionul_cere_o_stare_cunoscuta_a_masinii() -> None:
    """Starea mașinii se validează la construcție, nu la scriere: nimic nevalid nu ajunge pe disc."""
    with pytest.raises(ValueError):
        _sample(machine_state="OFF")


def test_masuratorile_masinii_se_scriu_sub_numele_din_contract() -> None:
    """Uzura, vibrația și restul mărimilor fără sursă în scenă intră în eșantion sub numele lor."""
    reading = telemetry.MachineReading(tool_wear_index=0.4, coolant_fault=True)
    record = _sample(machine=reading)
    assert record["tool_wear_index"] == pytest.approx(0.4)
    assert record["coolant_fault"] is True
    assert record["machine_state"] == "LOADING"
    assert telemetry.validate_sample(record) == []


def test_identificatorii_au_formatul_din_contract() -> None:
    """Ciclul și piesa au șase cifre, ca în expresiile regulate ale schemei."""
    assert telemetry.cycle_id(1) == "cyc-000001"
    assert telemetry.part_id(7) == "part-000007"
    for builder in (telemetry.cycle_id, telemetry.part_id):
        for index in (0, 1_000_000):
            with pytest.raises(ValueError):
                builder(index)


# ---------------------------------------------------------------- cadrul de inspecție


def test_metadatele_cadrului_poarta_rolurile_si_versiunea_din_contract() -> None:
    """Rolurile care declanșează inspecția sunt cele din schemă, la versiunea publicată.

    Schema ține și rolul `inspection`, pentru cadrele fixe din extensia Isaac Sim; geamănul MuJoCo
    produce doar `sorting`, de la camera de pe flanșa brațului 2.
    """
    schema = telemetry.load_schema(SNAPSHOT_SCHEMA)
    version = schema["properties"]["schema_version"]["const"]
    roles = set(schema["properties"]["camera_role"]["enum"])
    assert {povs.ROLE_SORTING, "inspection"} <= roles
    for role in (povs.ROLE_SORTING, "inspection"):
        record = _snapshot(role=role)
        assert telemetry.validate_snapshot(record) == []
        assert record["schema_version"] == version
        assert record["image_key"].startswith(f"{telemetry.QA_SNAPSHOT_PREFIX}/")
        assert record["resolution"] == list(povs.PROCESS_FRAME)


def test_marcajul_de_timp_al_cadrului_este_iso_in_utc() -> None:
    """`captured_at` este ora epoch a sesiunii, în forma cerută de schemă."""
    assert telemetry.iso_time(telemetry.SESSION_EPOCH_MS) == "2025-10-09T08:53:20+00:00"


def test_cadrul_de_inspectie_are_exact_campurile_din_contract() -> None:
    """Cadrul are exact cheile schemei, în ambele sensuri.

    Schema s-a închis la 1.2.0 (`additionalProperties: false`), deci un câmp în plus cade la
    validare. Egalitatea se cere totuși explicit: ea este cea care prinde și câmpul scris din
    greșeală, și câmpul uitat.
    """
    schema = telemetry.load_schema(SNAPSHOT_SCHEMA)
    record = _snapshot()
    assert set(record) == set(schema["properties"])
    assert set(schema["required"]) <= set(record)
    assert schema["additionalProperties"] is False
    assert telemetry.validate_snapshot({**record, "camera_frame": "x"}), "câmpul în plus a trecut"


def test_cadrul_de_inspectie_nu_poarta_eticheta() -> None:
    """Nici schema, nici cadrul scris nu conțin ceva din răspuns.

    Cadrul pornește Lambda-ul de inspecție. Dacă ar duce cu el indicii de uzură ori flagul de
    defect cerut, modelul ar citi răspunsul în loc să îl deducă din imagine, iar pe linia reală
    câmpurile nu există. Eticheta stă în `frames.jsonl` și în manifest, legată prin `part_id`.
    """
    schema = telemetry.load_schema(SNAPSHOT_SCHEMA)
    assert not set(schema["properties"]) & set(telemetry.LABEL_FIELDS)
    assert not set(_snapshot()) & set(telemetry.LABEL_FIELDS)
    assert {"family", "defect", "verdict", "tool_wear_index"} <= set(telemetry.LABEL_FIELDS)


def test_cadrul_nu_poate_primi_eticheta() -> None:
    """Scriitorul cadrului nu are parametru pentru etichetă: nu există cale de a o strecura."""
    with pytest.raises(TypeError):
        telemetry.qa_snapshot(
            ts_ms=telemetry.SESSION_EPOCH_MS, cycle=CYCLE, part=PART,
            role=povs.ROLE_SORTING, camera="arm2_wrist_camera",
            resolution=povs.PROCESS_FRAME, image_key="raw/qa-snapshots/cadru.png",
            machine_state="INSPECTING", tool_wear_index=0.5)


# ---------------------------------------------------------------- starea mașinii


def test_starea_masinii_acopera_toate_punctele_de_lucru() -> None:
    """Fiecare punct de lucru al rutelor, pentru fiecare familie, are o stare din contract."""
    used: set[str] = set()
    for arm in moves.ROUTES:
        for family in layout.RACK_FAMILIES:
            for stage in moves.route_names(arm, family):
                state = telemetry.stage_state(stage)
                assert state in telemetry.MACHINE_STATES, stage
                used.add(state)
    assert telemetry.stage_state("rack_empty") == "UNLOADING"
    assert telemetry.machine_state_for(None) == telemetry.IDLE_STATE
    used.add(telemetry.machine_state_for(None))
    with pytest.raises(KeyError):
        telemetry.stage_state("belt9_pick")
    # `CUTTING` este singura stare pe care rutele nu o dau: mașina nu are axe în scenă, iar
    # prelucrarea intră în sesiune ca fereastră de timp (`wear.PHASE_CUTTING`), nu ca punct de lucru.
    assert set(telemetry.MACHINE_STATES) - used == {wear.PHASE_CUTTING}


# ---------------------------------------------------------------- punctele de vedere


def test_registrul_punctelor_de_vedere_are_nume_unice_si_camere_din_plan() -> None:
    """Punctele de vedere privesc camere care există: cele de pe flanșe și cea liberă."""
    names = [item.name for item in povs.POVS]
    assert names == ["arm1_wrist", "arm2_wrist", "cell", "arm1_grasp", "arm2_grasp"]
    assert len(set(names)) == len(names)
    known = {f"{item['key']}_wrist_camera" for item in layout.ARMS}
    for item in povs.POVS:
        assert item.camera is None or item.camera in known, item.name
        assert povs.pov(item.name) is item


def test_verdictul_vine_de_la_camera_de_pe_flansa_bratului_2() -> None:
    """Rolul `sorting` este al camerei care vede piesa ținută, nu al unei camere fixe."""
    assert povs.pov("arm2_wrist").role == povs.ROLE_SORTING
    assert [item.name for item in povs.POVS if item.role == povs.ROLE_SORTING] == ["arm2_wrist"]
    assert not hasattr(povs, "ROLE_INSPECTION"), "nu mai există rol de cameră fixă în geamăn"
    assert not hasattr(povs, "ON_BELT") and not hasattr(povs, "on_belt")


def test_fiecare_punct_de_vedere_are_un_moment_de_captura() -> None:
    """Un punct de vedere fără moment nu s-ar fotografia niciodată, tăcut."""
    for item in povs.POVS:
        assert item.stages or item.grasp_points, item.name
    assert {item.name for item in povs.at_stage("belt2_pick")} == {"arm2_wrist"}
    assert {item.name for item in povs.at_stage("cnc_chuck")} == {"arm1_wrist"}
    assert {item.name for item in povs.at_grasp("belt1_pick", "jos")} == {"arm1_grasp"}
    assert {item.name for item in povs.at_grasp("belt2_pick", "ridicare")} == {"arm2_grasp"}
    assert povs.at_grasp("belt2_pick", "trecere 1") == ()


def test_planul_unui_ciclu_are_toate_cadrele_o_singura_data() -> None:
    """Patru cadre pe ciclu, fiecare `camera@moment` o singură dată, pe orice familie.

    Aici se prinde regresia care a lipsit un cadru: `arm1_wrist` se fotografiază la două momente,
    deci „l-am luat deja" nu se poate ține minte doar pe numele camerei.
    """
    for family in layout.RACK_FAMILIES:
        plan = povs.frames_per_cycle(family)
        counted = Counter(item.name for _, item in plan)
        assert counted == Counter({"arm1_wrist": 2, "arm2_wrist": 1, "cell": 1}), \
            family
        keys = [f"{item.name}@{stage}" for stage, item in plan]
        assert len(keys) == len(set(keys)), f"cadru luat de două ori: {keys}"
        stages = {stage for stage, _ in plan}
        assert stages <= set(moves.route_names("arm1", family)) \
            | set(moves.route_names("arm2", family))
        assert layout.rack_target(family) in stages


def test_planul_de_prindere_adauga_trei_momente_pe_fiecare_brat() -> None:
    """Secvența de prindere dă cîte trei cadre pe braț, fără să dubleze cadrul de proces."""
    for family in layout.RACK_FAMILIES:
        plan = povs.frames_per_cycle(family, grasp=True)
        counted = Counter(item.name for _, item in plan)
        assert counted == Counter({"arm1_wrist": 2, "arm2_wrist": 1, "cell": 1,
                                   "arm1_grasp": 3, "arm2_grasp": 3}), family
        keys = [f"{item.name}@{stage}" for stage, item in plan]
        assert len(keys) == len(set(keys)), f"cadru luat de două ori: {keys}"
        grasp_stages = {stage for stage, item in plan if item.name.endswith("_grasp")}
        assert grasp_stages == {
            "belt1_pick-sus", "belt1_pick-jos", "belt1_pick-ridicare",
            "belt2_pick-sus", "belt2_pick-jos", "belt2_pick-ridicare",
        }


# ---------------------------------------------------------------- fișierele sesiunii


def test_randul_din_frames_poarta_eticheta_piesei() -> None:
    """Fiecare cadru din dataset are familia, defectul și verdictul piesei din el."""
    variant = part.load_variant(part.DEFAULT_SEED, None, layout.RACK_FAMILIES[0])
    label = part.describe(variant)
    row = telemetry.frame_row(
        ts_ms=telemetry.SESSION_EPOCH_MS, cycle=CYCLE, part=PART, pov="arm2_wrist",
        stage="belt2_pick", camera="arm2_wrist_camera", resolution=povs.PROCESS_FRAME,
        path=f"{telemetry.FRAMES_DIR}/{CYCLE}/arm2_wrist-belt2_pick.png", sim_time_s=0.4,
        machine_state="IDLE", label=label)
    assert row["variant_id"] == label["part_id"]
    assert row["family"] == label["family"]
    assert row["defect"] == label["defect"]
    assert row["is_synthetic_defect"] is (label["defect"] != "PASS")
    assert row["path"].startswith(f"{telemetry.FRAMES_DIR}/")


def test_sesiunea_declara_fisierele_si_versiunile_publicate() -> None:
    """`session.json` spune cum se numesc fișierele și ce versiune de contract au."""
    record = telemetry.session_record(session="wheel_100-7", scenario={"python": "3.13"},
                                      totals={"samples": 533}, cycles=[])
    assert record["files"] == {
        "samples": telemetry.SAMPLE_FILE,
        "snapshots": telemetry.SNAPSHOT_FILE,
        "frames": telemetry.FRAME_FILE,
        "readings": telemetry.READING_FILE,
        "wear": telemetry.WEAR_FILE,
        "events": telemetry.EVENT_FILE,
        "frames_dir": telemetry.FRAMES_DIR,
    }
    assert record["schemas"]["samples"] == \
        telemetry.load_schema(TELEMETRY_SCHEMA)["properties"]["schema_version"]["const"]
    assert record["schemas"]["snapshots"] == \
        telemetry.load_schema(SNAPSHOT_SCHEMA)["properties"]["schema_version"]["const"]


def test_fisierele_se_scriu_si_se_citesc_inapoi(tmp_path: Path) -> None:
    """Scrierea este JSON Lines pentru rânduri și JSON indentat pentru sesiune."""
    rows = [_sample(seq=index, ts_ms=telemetry.SESSION_EPOCH_MS + 100 * index)
            for index in range(3)]
    path = tmp_path / telemetry.SAMPLE_FILE
    assert telemetry.write_jsonl(path, rows) == 3
    read_back = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert read_back == rows
    session = tmp_path / telemetry.SESSION_FILE
    telemetry.write_json(session, {"session": "wheel_100-7"})
    assert json.loads(session.read_text(encoding="utf-8"))["session"] == "wheel_100-7"


def test_identitatea_celulei_este_cea_a_extensiei() -> None:
    """Implicit, geamănul se dă drept aceeași celulă ca extensia Isaac Sim, pe același topic."""
    assert telemetry.DEVICE_ID == "cnc-cell-01"
    assert telemetry.CELL_ID == "cell-a"
    assert telemetry.TELEMETRY_TOPIC == f"cncdt/{telemetry.ENVIRONMENT}/cnc/{telemetry.CELL_ID}" \
        "/telemetry"
