"""Teste pentru motorul de uzură al geamănului MuJoCo: ecuația, efectele și etichetele.

Uzura este partea pură a fazei 2 (`cncdt_mj/wear.py`, numpy, fără motor), deci se testează din
`.venv`-ul proiectului, ca orice modul pur (ADR 0012). Ce se apără aici:

* ecuația din `docs/11-geaman-mujoco.md` §3a — timpul până la degradat este cel documentat, iar
  integrarea nu depinde de pas;
* efectele în contract — indicii rămân în interval, viteza benzii scade cu cel mult 8%, efortul
  brațului crește cu cel mult 80%, mărimile de așchiere ies din profilul nominal;
* etichetele — eșecurile au hazard crescător cu uzura, iar acțiunile de operator se înregistrează
  și se văd în sănătatea componentelor.

Ce nu se poate testa aici, fiindcă are nevoie de motor: că viteza scalată a benzii ajunge în forța
scrisă de `cell.py` și că efortul scalat ajunge în măsurătoarea din scenă. Acelea se văd în
`make mj-record` (coloanele `belt_speed_mps` și `joint_effort` din `readings.jsonl`).
"""

from __future__ import annotations

import math

import pytest

from cncdt_mj import telemetry, wear

ALL = wear.component_names()


def _work_for(name: str) -> wear.Work:
    """Timpul de lucru în care se uzează componenta dată: taie, cară ori se mișcă."""
    if name in wear.CNC_COMPONENTS:
        return wear.Work(cutting=True)
    if name in wear.BELT_COMPONENTS:
        return wear.Work(belts=(name,))
    return wear.Work(arms=(name,))


def _health_after_seconds(component: wear.Component, seconds: float, rate: float) -> float:
    """Soluția ecuației, scrisă separat de motor: `h^q(t) = 1 - q c t`."""
    power = 1.0 - component.exponent
    decay = component.rate() * rate * (1.0 + component.load_sensitivity * component.load)
    remaining = 1.0 - power * decay * seconds
    if remaining <= 0.0:
        return wear.MIN_HEALTH
    return max(wear.MIN_HEALTH, remaining ** (1.0 / power))


def _failures_of_belts(health: float, seed: int, hours: float = 4.0, step_s: float = 60.0) -> int:
    """Câte eșecuri de bandă trage motorul, cu sănătatea de plecare dată.

    Timpul se împarte în pași de un minut: într-un singur pas mare, probabilitatea este 1 la orice
    sănătate, iar comparația dintre uzat și nou nu mai spune nimic. Sesiunea avansează și mai mărunt,
    la fiecare pas de simulare.
    """
    model = wear.WearModel(seed=seed)
    for name in wear.BELT_COMPONENTS:
        model.inject(name, 1.0 - health)
    drawn = 0
    for _ in range(int(hours * 3600.0 / step_s)):
        drawn += len(model.advance(step_s, wear.Work(belts=wear.BELT_COMPONENTS)))
    return drawn


# ---------------------------------------------------------------- ecuația


def test_munca_din_demonstratie_include_scula_doar_in_fereastra_de_aschiere() -> None:
    """În viewer, uzura se avansează pe pas, nu pe fază: fără `cutting`, CNC-ul nu se uzează."""
    idle = wear.demo_work(False)
    cutting = wear.demo_work(True)

    assert set(wear.ARM_COMPONENTS) <= set(idle.components())
    assert not set(wear.CNC_COMPONENTS) & set(idle.components())
    assert set(wear.CNC_COMPONENTS) <= set(cutting.components())


def test_uzura_ajunge_la_degradat_exact_in_timpul_documentat() -> None:
    """Fiecare componentă ajunge la `h = 0.3` în timpul ei nominal de lucru.

    Timpii sunt cei din `docs/11-geaman-mujoco.md` §3a: scula în 30 de minute de așchiere, brațele
    în 90 de minute de mișcare, benzile în câteva ore. Integrarea este exactă, deci verificarea se
    face cu o singură trecere, nu cu pași mărunți.
    """
    for component in wear.COMPONENTS:
        model = wear.WearModel(seed=1)
        model.advance(component.degraded_after_s, _work_for(component.name))
        assert model.health_of(component.name) == pytest.approx(
            wear.DEGRADED_HEALTH, abs=1e-9), component.name


def test_uzura_nu_depinde_de_pasul_de_integrare() -> None:
    """Aceeași uzură la un pas mare și la mulți pași mici: altfel timpul nominal ar minți.

    Sesiunea avansează uzura la fiecare pas de simulare (2 ms), iar testele de mai sus la un pas
    de 30 de minute: dacă ecuația s-ar rezolva aproximativ, cele două ar da alt rezultat.
    """
    coarse = wear.WearModel(seed=2)
    coarse.advance(600.0, wear.Work(cutting=True))
    fine = wear.WearModel(seed=2)
    for _ in range(600):
        fine.advance(1.0, wear.Work(cutting=True))
    for name in wear.CNC_COMPONENTS:
        assert coarse.health_of(name) == pytest.approx(fine.health_of(name), abs=1e-9), name


def test_uzura_curge_doar_cu_timpul_de_lucru_al_componentei() -> None:
    """Ce nu lucrează nu se uzează: scula stă cât timp brațul se mișcă, și invers."""
    model = wear.WearModel(seed=3)
    model.advance(1800.0, wear.Work(arms=("arm1",)))
    assert model.health_of("arm1") < 1.0
    assert model.health_of("cnc_tool") == 1.0
    assert model.health_of("belt_in") == 1.0
    model.advance(0.0, wear.Work(cutting=True))
    assert model.health_of("cnc_tool") == 1.0


def test_uzura_nu_coboara_sub_sanatatea_minima() -> None:
    """Orice sarcină, oricât de lungă, lasă componenta peste `MIN_HEALTH`.

    Contractul cere ca indicii de uzură să rămână în intervalul închis în care intră și 1: la
    sănătate zero, `tool_wear_index` ar fi exact 1, iar acolo schema nu mai are loc de rotunjire.
    """
    model = wear.WearModel(seed=4, rate=1000.0)
    model.advance(10 * 3600.0, wear.Work(cutting=True, belts=wear.BELT_COMPONENTS,
                                         arms=wear.ARM_COMPONENTS))
    for name in ALL:
        assert model.health_of(name) == pytest.approx(wear.MIN_HEALTH, abs=1e-12), name
        assert 0.0 <= model.wear_index(name) <= 1.0, name


def test_rata_de_uzura_comprima_viata_fara_sa_atinga_ceasul() -> None:
    """`--rate` scurtează viața componentei, iar ceasul sesiunii curge la fel (ADR 0007)."""
    nominal = wear.WearModel(seed=5)
    nominal.advance(600.0, wear.Work(cutting=True))
    compressed = wear.WearModel(seed=5, rate=2.0)
    compressed.advance(600.0, wear.Work(cutting=True))
    assert compressed.health_of("cnc_tool") < nominal.health_of("cnc_tool")
    assert compressed.now_s == pytest.approx(nominal.now_s)
    assert nominal.health_of("cnc_tool") == pytest.approx(
        _health_after_seconds(nominal.components["cnc_tool"], 600.0, 1.0), abs=1e-9)
    assert compressed.health_of("cnc_tool") == pytest.approx(
        _health_after_seconds(compressed.components["cnc_tool"], 600.0, 2.0), abs=1e-9)


def test_hazardul_creste_cu_uzura() -> None:
    """Eșecurile se îndesesc pe măsură ce componenta se uzează: `λ = λ0 exp(γ (1 - h))`."""
    component = wear.COMPONENTS[0]
    assert component.hazard_per_s(1.0) == pytest.approx(
        component.hazard_per_hour / 3600.0, rel=1e-12)
    assert (component.hazard_per_s(0.5) / component.hazard_per_s(1.0)
            == pytest.approx(math.exp(component.hazard_gamma * 0.5), rel=1e-12))


# ---------------------------------------------------------------- efectele în contract


def test_indicii_din_contract_ramân_in_interval() -> None:
    """Toți indicii din `cnc.telemetry.v1.json` rămân în [0, 1], la orice uzură."""
    for wear_index in (0.0, 0.4, 0.9):
        model = wear.WearModel(seed=6)
        for name in wear.CNC_COMPONENTS + wear.ARM_COMPONENTS:
            model.inject(name, wear_index)
        for index in (model.tool_wear_index(), model.joint_friction_index(),
                      model.surface_quality()):
            assert 0.0 <= index <= 1.0
        assert model.surface_quality() == pytest.approx(
            max(0.0, 1.0 - model.tool_wear_index() ** 1.2), abs=1e-6)


def test_benzile_uzate_scad_viteza_cu_cel_mult_opt_la_suta() -> None:
    """Driftul din §3b: 8% la sănătate minimă, iar banda care alunecă stă pe loc."""
    model = wear.WearModel(seed=7)
    assert model.belt_speed_scales() == {name: 1.0 for name in wear.BELT_COMPONENTS}
    model.repair("belt_in")
    model.inject("belt_in", 1.0 - wear.MIN_HEALTH)
    scale = model.belt_speed_scales()["belt_in"]
    assert scale == pytest.approx(1.0 - wear.BELT_SPEED_DROP * (1.0 - wear.MIN_HEALTH), abs=1e-6)
    assert scale >= 1.0 - wear.BELT_SPEED_DROP
    model.failures.append(wear.Failure(0.0, "belt_in", "belt_slip", 0.2, 2.5))
    model.active["belt_in"] = model.failures[-1]
    assert model.belt_speed_scales()["belt_in"] == 0.0
    assert model.belt_speed_scales()["belt_out"] > 0.0
    assert model.belt_speed_scale() == 0.0


def test_bratele_uzate_cer_mai_mult_efort() -> None:
    """Frecarea din articulații: efortul măsurat în scenă se scalează cu cel mult 1,8."""
    model = wear.WearModel(seed=8)
    assert model.arm_effort_scales() == {name: 1.0 for name in wear.ARM_COMPONENTS}
    model.inject("arm1", 1.0 - wear.MIN_HEALTH)
    scales = model.arm_effort_scales()
    assert scales["arm1"] == pytest.approx(
        1.0 + wear.ARM_EFFORT_GAIN * (1.0 - wear.MIN_HEALTH), abs=1e-6)
    assert scales["arm1"] <= 1.0 + wear.ARM_EFFORT_GAIN
    assert scales["arm2"] == 1.0
    assert model.joint_friction_index() == pytest.approx(model.wear_index("arm1"), abs=1e-6)


def test_marimile_de_aschiere_ies_din_profilul_nominal() -> None:
    """Fără uzură și fără zgomot, citirile sunt exact profilul nominal al așchierii."""
    model = wear.WearModel(seed=9)
    reading = model.reading(cutting=True, noise=False)
    assert reading.spindle_load == pytest.approx(model.profile.spindle_load)
    assert reading.vibration == pytest.approx(model.profile.vibration)
    assert reading.coolant_flow_lpm == pytest.approx(model.profile.coolant_lpm)
    assert reading.axis_feed == pytest.approx(model.profile.axis_feed)
    assert reading.tool_wear_index == 0.0
    assert reading.joint_friction_index_arm1 == 0.0
    assert reading.joint_friction_index_arm2 == 0.0
    assert reading.surface_quality == 1.0
    assert not reading.coolant_fault


def test_scula_uzata_incarca_axul_si_scade_calitatea() -> None:
    """Efectele documentate ale sculei: mai multă sarcină pe ax, așchiere mai mică, calitate jos."""
    new = wear.WearModel(seed=10).reading(cutting=True, noise=False)
    worn_model = wear.WearModel(seed=10)
    worn_model.inject("cnc_tool", 0.9)
    worn = worn_model.reading(cutting=True, noise=False)
    assert worn.spindle_load > new.spindle_load
    assert worn.axis_feed < new.axis_feed
    assert worn.surface_quality < new.surface_quality
    assert worn.tool_wear_index == pytest.approx(0.9, abs=1e-6)


def test_la_repaus_ramân_doar_indicii() -> None:
    """Mașina care nu taie nu are sarcină, debit și avans: are stare, nu măsurătoare."""
    model = wear.WearModel(seed=11)
    model.inject("cnc_tool", 0.5)
    reading = model.reading(cutting=False, noise=False)
    assert reading.spindle_load == 0.0
    assert reading.axis_feed == 0.0
    assert reading.coolant_flow_lpm == 0.0
    assert reading.vibration == pytest.approx(model.profile.idle_vibration)
    assert reading.tool_wear_index == pytest.approx(0.5, abs=1e-6)


def test_temperatura_urca_la_aschiere_si_coboara_la_repaus() -> None:
    """Termometrul are inerție: urcă spre ținta de așchiere, apoi se întoarce la ambient."""
    model = wear.WearModel(seed=12)
    assert model.reading(cutting=True, noise=False).temp_c == pytest.approx(model.ambient_c)
    model.advance(5 * model.profile.thermal_tau_s, wear.Work(cutting=True))
    hot = model.reading(cutting=True, noise=False).temp_c
    assert hot > model.ambient_c + 0.9 * model.profile.temp_rise_c
    model.advance(20 * model.profile.thermal_tau_s, wear.Work())
    assert model.reading(cutting=False, noise=False).temp_c == pytest.approx(
        model.ambient_c, abs=0.1)


def test_esecurile_apar_mai_des_când_componenta_este_uzata() -> None:
    """Cu aceleași semințe, o bandă uzată cade de multe ori mai des decât una bună."""
    seeds = range(40)
    healthy = sum(_failures_of_belts(1.0, seed) for seed in seeds)
    worn = sum(_failures_of_belts(wear.MIN_HEALTH, seed) for seed in seeds)
    assert worn > 10 * healthy, (healthy, worn)

    model = wear.WearModel(seed=13)
    for name in wear.BELT_COMPONENTS:
        model.inject(name, 1.0 - wear.MIN_HEALTH)
    drawn = model.advance(600.0, wear.Work(belts=wear.BELT_COMPONENTS))
    assert drawn, "o bandă la sănătatea minimă trebuie să alunece în zece minute"
    assert all(item.kind == "belt_slip" for item in drawn)
    assert all(item.component in wear.BELT_COMPONENTS for item in drawn)


def test_esecurile_se_sting_dupa_durata_lor() -> None:
    """Un eșec cu durată se termină singur; scula ruptă ține până la reparație."""
    model = wear.WearModel(seed=14)
    model.active["belt_in"] = wear.Failure(0.0, "belt_in", "belt_slip", 0.5, 2.5)
    model.advance(1.0, wear.Work(belts=("belt_in",)))
    assert model.belt_speed_scales()["belt_in"] == 0.0
    model.advance(2.0, wear.Work(belts=("belt_in",)))
    assert model.belt_speed_scales()["belt_in"] > 0.0

    model.active["cnc_tool"] = wear.Failure(0.0, "cnc_tool", "tool_break", 0.2, None)
    model.advance(10 * 3600.0, wear.Work(cutting=True))
    assert model.health_of("cnc_tool") == pytest.approx(wear.MIN_HEALTH)
    assert model.surface_quality() < 1.0
    model.apply_action("replace_tool")
    assert model.surface_quality() == pytest.approx(1.0)


def test_fiecare_esec_are_durată_și_are_unde_se_scrie() -> None:
    """Eșecurile din registru poartă un fel și, în afară de scula ruptă, o durată."""
    assert all(item.failure_kind for item in wear.COMPONENTS)
    tool = next(item for item in wear.COMPONENTS if item.name == "cnc_tool")
    assert tool.failure_duration_s is None
    for component in wear.COMPONENTS:
        if component.name != "cnc_tool":
            assert component.failure_duration_s


# ---------------------------------------------------------------- acțiunile de operator


def test_comenzile_din_contract_ajung_la_componentele_documentate() -> None:
    """Numele din `cnc.control.v1.json` mișcă exact componentele pe care le numesc."""
    model = wear.WearModel(seed=15)
    model.apply_action("inject_tool_wear", amount=0.4)
    assert model.wear_index("cnc_tool") == pytest.approx(0.4, abs=1e-6)
    assert model.wear_index("arm1") == 0.0

    model.apply_action("inject_joint_friction", amount=0.3)
    assert model.joint_friction_index() == pytest.approx(0.3, abs=1e-6)

    model.apply_action("repair_joints")
    assert model.joint_friction_index() == 0.0
    assert model.wear_index("cnc_tool") == pytest.approx(0.4, abs=1e-6)

    model.apply_action("replace_tool")
    assert model.tool_wear_index() == 0.0
    assert [action.command for action in model.actions] == [
        "inject_tool_wear", "inject_joint_friction", "repair_joints", "replace_tool"]


def test_acțiunile_se_înregistrează_cu_momentul_lor() -> None:
    """Fiecare acțiune lasă o etichetă, cu ceasul la care s-a cerut."""
    model = wear.WearModel(seed=16)
    model.advance(120.0, wear.Work(cutting=True))
    action = model.apply_action("inject_wear", name="belt_out", amount=0.5)
    assert action.sim_time_s == pytest.approx(120.0)
    assert action.component == "belt_out"
    assert action.detail
    assert model.health_of("belt_out") == pytest.approx(0.5, abs=1e-6)


def test_repararea_ridică_sănătatea_și_stinge_esecul() -> None:
    """`repair` readuce componenta la 1 și o scoate din eșecul în curs."""
    model = wear.WearModel(seed=17)
    model.inject("belt_in", 0.8)
    model.active["belt_in"] = wear.Failure(0.0, "belt_in", "belt_slip", 0.2, None)
    model.apply_action("repair", name="belt_in")
    assert model.health_of("belt_in") == 1.0
    assert "belt_in" not in model.active
    assert model.belt_speed_scales()["belt_in"] == 1.0


def test_accelerarea_cerută_grăbește_uzura_cât_ține() -> None:
    """`inject_fault_burst` ține rata ridicată pe durata cerută, apoi o lasă la loc."""
    plain = wear.WearModel(seed=18)
    plain.inject("cnc_tool", 0.1)
    plain.advance(60.0, wear.Work(cutting=True))

    burst = wear.WearModel(seed=18)
    burst.apply_action("inject_fault_burst", name="cnc_tool", amount=0.1, duration_s=60.0)
    burst.advance(60.0, wear.Work(cutting=True))
    during = burst.wear_index("cnc_tool")
    assert during > 3.0 * plain.wear_index("cnc_tool")

    burst.advance(60.0, wear.Work(cutting=True))
    after = burst.wear_index("cnc_tool") - during
    assert 0.0 < after < during
    assert "cnc_tool" not in burst.bursts


def test_resetul_și_rata_se_văd_în_tabelul_componentelor() -> None:
    """`session.json` scrie rata folosită, iar resetul o aduce la 1 și sănătatea la plin."""
    model = wear.WearModel(seed=19, rate=25.0)
    table = model.component_table()
    assert [row["name"] for row in table] == list(ALL)
    assert table[0]["rate_per_s"] == pytest.approx(
        model.components["belt_in"].rate() * 25.0, rel=1e-12)
    model.inject("arm2", 0.9)
    model.apply_action("reset_wear")
    assert model.rate == 1.0
    assert all(health == 1.0 for health in model.health.values())


def test_comenzile_necunoscute_și_injecțiile_necunoscute_ies_cu_eroare() -> None:
    """O comandă din afara vocabularului, ori o componentă care nu există, nu trece tăcut."""
    model = wear.WearModel(seed=20)
    with pytest.raises(KeyError):
        model.apply_action("polish_everything")
    with pytest.raises(KeyError):
        model.health_of("belt9")
    with pytest.raises(KeyError):
        model.inject("belt9", 0.1)
    with pytest.raises(ValueError):
        model.set_rate(0.0)


def test_injecțiile_de_pe_linia_de_comandă_se_citesc_ca_acțiuni() -> None:
    """`--inject componentă:cât` devine o comandă `inject_wear`, cu 0.25 când nu se dă câtul."""
    assert wear.parse_injections(["cnc_tool:0.5", "belt_out"]) == (
        {"command": "inject_wear", "component": "cnc_tool", "amount": 0.5},
        {"command": "inject_wear", "component": "belt_out", "amount": 0.25},
    )
    model = wear.WearModel(seed=21)
    applied = model.apply_actions(wear.parse_injections(["cnc_coolant:0.75"]))
    assert [action.command for action in applied] == ["inject_wear"]
    assert model.wear_index("cnc_coolant") == pytest.approx(0.75, abs=1e-6)
    for bad in ("", ":0.5", "cnc_tool:0", "cnc_tool:1.5", "cnc_tool:x"):
        with pytest.raises(ValueError):
            wear.parse_injections([bad])


# ---------------------------------------------------------------- ce se scrie în fișiere


def test_snapshot_ul_are_o_coloană_pe_componentă_și_indicii_derivați() -> None:
    """`wear.jsonl` poartă traiectoria fiecărei componente, plus indicii din contract."""
    model = wear.WearModel(seed=22)
    model.advance(300.0, wear.Work(cutting=True))
    snapshot = model.snapshot()
    assert set(snapshot) == {f"{name}_health" for name in ALL} | {
        "tool_wear_index", "joint_friction_index", "surface_quality", "belt_speed_scale",
        "temp_c"}
    assert snapshot["cnc_tool_health"] < 1.0
    assert snapshot["arm1_health"] == 1.0
    row = telemetry.wear_row(ts_ms=telemetry.SESSION_EPOCH_MS, sim_time_s=300.0,
                             cycle=telemetry.cycle_id(1), machine_state=wear.PHASE_CUTTING,
                             snapshot=snapshot)
    assert row["cycle_id"] == telemetry.cycle_id(1)
    assert row["machine_state"] == "CUTTING"
    assert len(row) == len(snapshot) + 4


def test_eșecurile_și_acțiunile_se_scriu_ca_etichete() -> None:
    """Un eșec și o intervenție ajung în `events.jsonl` cu câmpurile care le leagă de eșantioane."""
    failure = wear.Failure(12.5, "belt_in", "belt_slip", 0.22, 2.5)
    row = telemetry.event_row(ts_ms=telemetry.SESSION_EPOCH_MS, sim_time_s=failure.sim_time_s,
                              cycle=telemetry.cycle_id(1), event="failure", kind=failure.kind,
                              component=failure.component, health=failure.health,
                              severity="critical", duration_s=failure.duration_s)
    assert row["event"] == "failure"
    assert row["kind"] == "belt_slip"
    assert row["duration_s"] == 2.5
    assert row["health"] == 0.22
    action = wear.WearModel(seed=23).apply_action("replace_tool")
    row = telemetry.event_row(ts_ms=telemetry.SESSION_EPOCH_MS, sim_time_s=action.sim_time_s,
                              cycle=telemetry.cycle_id(1), event="action", kind=action.command,
                              component=action.component, detail=action.detail)
    assert row["event"] == "action"
    assert row["kind"] == "replace_tool"
    assert "health" not in row and "severity" not in row


def test_severitatea_urmează_pragurile_documentate() -> None:
    """Severitatea este a pragului cel mai jos trecut: la `h = 0.1` componenta e critică."""
    model = wear.WearModel(seed=24)
    assert model.severity("arm1") == "ok"
    for amount, expected in ((0.2, "ok"), (0.4, "worn"), (0.6, "degraded"), (0.8, "critical")):
        model.repair("arm1")
        model.inject("arm1", amount)
        assert model.severity("arm1") == expected, amount
