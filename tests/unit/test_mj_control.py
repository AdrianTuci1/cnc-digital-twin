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
    """Panoul are cele șapte butoane cerute, cu comenzile din schema de control."""
    labels = {label for label, _, _ in control.BUTTONS}
    assert labels == {
        "Uzură +0.1",
        "Uzură +0.25",
        "Frecare +0.25",
        "Sculă nouă",
        "Repară articulații",
        "Repară tot",
        "Reset degradare",
    }
    commands = {command for _, command, _ in control.BUTTONS}
    assert {"inject_tool_wear", "inject_joint_friction", "replace_tool", "repair_joints"} <= commands
    assert {"repair_all", "reset_wear"} <= commands


def test_inject_tool_wear_creste_indicele_si_scade_calitatea() -> None:
    """Butonul de uzură schimbă exact indicele sculei, iar calitatea suprafeței scade."""
    session = control.ControlSession()
    before = session.status()["surface_quality"]

    detail = session.apply({"command": "inject_tool_wear", "amount": 0.25})

    assert "sănătate" in detail
    assert session.status()["tool_wear_index"] == pytest.approx(0.25, abs=1e-9)
    assert session.status()["surface_quality"] < before


def test_inject_joint_friction_atinge_ambele_brate() -> None:
    """Frecarea este o singură coloană în contract, deci se injectează în ambii brați."""
    session = control.ControlSession()
    session.apply({"command": "inject_joint_friction", "amount": 0.2})

    assert session.status()["joint_friction_index"] == pytest.approx(0.2, abs=1e-9)
    assert session.model.health_of("arm1") == pytest.approx(0.8, abs=1e-9)
    assert session.model.health_of("arm2") == pytest.approx(0.8, abs=1e-9)


def test_repair_all_readuce_scula_si_articulatiile() -> None:
    """Butonul „Repară tot” repară scula și brațele, fără să atingă benzile."""
    session = control.ControlSession()
    session.apply({"command": "inject_tool_wear", "amount": 0.4})
    session.apply({"command": "inject_joint_friction", "amount": 0.3})
    session.model.inject("belt_in", 0.2)

    detail = session.apply({"command": "repair_all"})

    assert detail == "sculă și articulații reparate"
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
        "surface_quality",
        "tool_health",
        "joint_health",
        "belt_speed_scale",
        "active_failures",
        "last_action",
    }
    assert status["tool_wear_index"] == 0.0
    assert status["joint_friction_index"] == 0.0
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
