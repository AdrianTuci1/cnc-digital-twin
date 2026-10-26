"""Teste pentru jurnalul scurt al demonstrației și pentru ritmul de redesenare.

Ambele module sînt pure (stdlib) și stau sub ferestre: jurnalul adună evenimentele pe care le
văd operatorul în viewer și în panou, iar pacer-ul decide cîte cadre ajung pe ecran. Ce se apără
aici: inelul ține doar ultimele evenimente, formatul liniei rămîne citibil, iar pacer-ul lasă
desenul la ritmul cerut fără să încetinească simularea.
"""

from __future__ import annotations

import re

import pytest

from cncdt_mj import journal, pacing


def test_jurnalul_pastreaza_ultimele_evenimente() -> None:
    """Capacitatea este o limită: evenimentele vechi ies, ordinea rămîne."""
    log = journal.Journal(capacity=3, clock=lambda: 0.0)
    for index in range(5):
        log.add(f"linia {index}")

    assert len(log) == 3
    assert [entry.text for entry in log.entries()] == ["linia 2", "linia 3", "linia 4"]


def test_linia_are_ceasul_in_fata_textului() -> None:
    """Fiecare linie începe cu ora locală, ca operatorul să vadă cînd a plecat eșantionul."""
    log = journal.Journal(clock=lambda: 1_700_000_000.0)
    log.add("IoT: 100 eșantioane trimise")

    line = log.lines()[-1]

    assert re.match(r"^\d{2}:\d{2}:\d{2}  IoT: 100 eșantioane trimise$", line)


def test_lines_taie_doar_coada() -> None:
    """Viewer-ul cere cîteva linii, panoul tot jurnalul; amîndouă pornesc de la coadă."""
    log = journal.Journal(clock=lambda: 0.0)
    for index in range(6):
        log.add(f"e{index}")

    tail = log.lines(2)

    assert [line.split("  ")[1] for line in tail] == ["e4", "e5"]
    assert len(log.lines()) == 6
    assert log.lines(0) == []


def test_capacitatea_invalida_este_refuzata() -> None:
    """Un jurnal fără nicio linie ar ascunde exact ce trebuie să arate."""
    with pytest.raises(ValueError):
        journal.Journal(capacity=0)


def test_pacerul_limiteaza_cadrele_fara_sa_piarda_primul() -> None:
    """Primul cadru iese imediat, apoi doar la intervalul cerut."""
    now = [100.0]
    pacer = pacing.FramePacer(fps=10.0, clock=lambda: now[0])

    assert pacer.due() is True
    now[0] += 0.05
    assert pacer.due() is False
    now[0] += 0.05
    assert pacer.due() is True
    assert pacer.frames_per_second() == pytest.approx(10.0)


def test_pacerul_fara_limita_redeseneaza_fiecare_pas() -> None:
    """`--view-fps 0` readuce comportamentul de dinainte: un cadru la fiecare pas."""
    pacer = pacing.FramePacer(fps=0.0, clock=lambda: 0.0)

    assert all(pacer.due() for _ in range(10))
    assert pacer.frames_per_second() == 0.0
