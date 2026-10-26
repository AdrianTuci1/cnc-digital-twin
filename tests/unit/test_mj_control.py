"""Teste pentru comenzile panoului de uzură al geamănului MuJoCo.

`cncdt_mj/control.py` este stratul pur dintre butoane și motorul de uzură: aceleași mesaje JSON
ajung din fereastra Tkinter, dintr-un REPL sau din cloud. Testele apără contractul butoanelor
(aceleași nume ca în schema de control), efectul fiecărei comenzi și forma stării trimise înapoi
panoului. Nu cer `mujoco` și nu deschid nicio fereastră.
"""

from __future__ import annotations

import pytest

from cncdt_mj import control, wear


def test_butoanele_acopera_uzura_si_reparatia() -> None:
    """Panoul are butoane pentru sculă și pentru fiecare braț, plus reparația și resetul."""
    labels = {label for label, _, _, _ in control.BUTTONS}
    assert labels == {
        "CNC wear +0.1",
        "CNC wear +0.25",
        "Arm 1 inject +0.25",
        "Arm 2 inject +0.25",
        "Arm 1 repair",
        "Arm 2 repair",
        "New CNC tool",
        "Repair all",
        "Reset wear",
    }
    commands = {command for _, command, _, _ in control.BUTTONS}
    assert {"inject_tool_wear", "inject_wear", "repair", "replace_tool"} <= commands
    assert {"repair_all", "reset_wear"} <= commands
    arms = {component for _, _, _, component in control.BUTTONS if component is not None}
    assert arms == {"arm1", "arm2"}


def test_inject_tool_wear_creste_indicele_si_scade_calitatea() -> None:
    """Butonul de uzură schimbă exact indicele sculei, iar calitatea suprafeței scade."""
    session = control.ControlSession()
    before = session.status()["surface_quality"]

    detail = session.apply({"command": "inject_tool_wear", "amount": 0.25})

    assert "health" in detail
    assert session.status()["tool_wear_index"] == pytest.approx(0.25, abs=1e-9)
    assert session.status()["surface_quality"] < before


def test_inject_joint_friction_atinge_ambele_brate() -> None:
    """Frecarea este o singură coloană în contract, deci se injectează în ambii brați."""
    session = control.ControlSession()
    session.apply({"command": "inject_joint_friction", "amount": 0.2})

    assert session.status()["joint_friction_index"] == pytest.approx(0.2, abs=1e-9)
    assert session.model.health_of("arm1") == pytest.approx(0.8, abs=1e-9)
    assert session.model.health_of("arm2") == pytest.approx(0.8, abs=1e-9)


def test_inject_wear_atinge_doar_bratul_cerut() -> None:
    """Butonul per braț schimbă un singur braț; celălalt rămîne curat, iar agregatul îl urmează."""
    session = control.ControlSession()

    detail = session.apply({"command": "inject_wear", "component": "arm2", "amount": 0.3})

    assert "health" in detail
    assert session.status()["arm1_friction_index"] == 0.0
    assert session.status()["arm2_friction_index"] == pytest.approx(0.3, abs=1e-9)
    assert session.status()["joint_friction_index"] == pytest.approx(0.3, abs=1e-9)
    assert session.model.health_of("arm2") == pytest.approx(0.7, abs=1e-9)


def test_repair_atinge_doar_bratul_cerut() -> None:
    """Reparația per braț readuce brațul atins și lasă uzura celuilalt intactă."""
    session = control.ControlSession()
    session.model.inject("arm1", 0.2)
    session.model.inject("arm2", 0.4)

    session.apply({"command": "repair", "component": "arm2"})

    assert session.status()["arm1_friction_index"] == pytest.approx(0.2, abs=1e-9)
    assert session.status()["arm2_friction_index"] == 0.0
    assert session.status()["joint_friction_index"] == pytest.approx(0.2, abs=1e-9)


def test_repair_all_readuce_scula_si_articulatiile() -> None:
    """Butonul „Repară tot” repară scula și brațele, fără să atingă benzile."""
    session = control.ControlSession()
    session.apply({"command": "inject_tool_wear", "amount": 0.4})
    session.apply({"command": "inject_joint_friction", "amount": 0.3})
    session.model.inject("belt_in", 0.2)

    detail = session.apply({"command": "repair_all"})

    assert detail == "tool and joints repaired"
    assert session.status()["tool_wear_index"] == 0.0
    assert session.status()["joint_friction_index"] == 0.0
    assert session.model.health_of("belt_in") == pytest.approx(0.8, abs=1e-9)


def test_reset_wear_readuce_toate_componentele_si_rata() -> None:
    """„Reset degradare” readuce toate cele șapte componente și rata de uzură."""
    session = control.ControlSession(model=wear.WearModel(rate=20.0))
    session.model.inject("belt_out", 0.3)

    session.apply({"command": "reset_wear"})

    assert session.model.rate == 1.0
    assert all(session.model.health_of(name) == 1.0 for name in wear.component_names())


def test_status_are_cheile_pe_care_le_citeste_panoul() -> None:
    """Starea este un dicționar simplu, cu valori în intervalele din contract."""
    status = control.ControlSession().status()

    assert set(status) == {
        "tool_wear_index",
        "joint_friction_index",
        "arm1_friction_index",
        "arm2_friction_index",
        "surface_quality",
        "tool_health",
        "joint_health",
        "arm1_health",
        "arm2_health",
        "belt_speed_scale",
        "active_failures",
        "last_action",
    }
    assert status["tool_wear_index"] == 0.0
    assert status["joint_friction_index"] == 0.0
    assert status["arm1_friction_index"] == 0.0
    assert status["arm2_friction_index"] == 0.0
    assert status["surface_quality"] == 1.0
    assert status["active_failures"] == []
    assert status["last_action"] == "—"


def test_mesajul_invalid_este_refuzat() -> None:
    """O linie care nu este obiect JSON nu ajunge la motorul de uzură."""
    with pytest.raises(ValueError):
        control.parse_message("nu este JSON")
    with pytest.raises(ValueError):
        control.parse_message("[1, 2, 3]")


def test_comanda_necunoscuta_este_refuzata() -> None:
    """Un nume necunoscut nu este trecut tăcut: panoul primește eroarea în jurnal."""
    with pytest.raises(KeyError):
        control.ControlSession().apply({"command": "comanda_inexistenta"})
