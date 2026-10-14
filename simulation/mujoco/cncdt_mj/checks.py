"""Regulile verificării care nu ating motorul de fizică.

`view.py --check` are nevoie de ele, dar locul lor nu este `scene.py` — singurul modul care
importă `mujoco` (ADR 0012). Aici se lucrează doar pe nume de corpuri, deci regulile se pot
testa din venv-ul proiectului, unde `mujoco` nu există.
"""

from __future__ import annotations

from typing import Iterable

#: Separatorul dintre numele corpurilor într-o pereche de contact (`scene.contact_pairs`).
PAIR_SEPARATOR = "↔"


def _arm_of(name: str, prefixes: tuple[str, ...]) -> str | None:
    """Prefixul brațului căruia îi aparține un corp, ori ``None`` dacă nu e al niciunui braț."""
    for prefix in prefixes:
        if name.startswith(prefix):
            return prefix
    return None


def arm_foreign_contacts(pairs: Iterable[str], arm_prefixes: Iterable[str]) -> list[str]:
    """Perechile în care un braț atinge altceva decât propriile verigi.

    Autocontactele unui braț (``arm1_hand↔arm1_link5``) sunt normale: brațul are geome care se
    ating la capetele articulațiilor. Ce se caută este un braț care atinge un element al celulei,
    piesa ori celălalt braț — acolo verificarea are ceva de spus.
    """
    prefixes = tuple(arm_prefixes)
    foreign: list[str] = []
    for pair in pairs:
        left, _, right = pair.partition(PAIR_SEPARATOR)
        left_arm = _arm_of(left, prefixes)
        right_arm = _arm_of(right, prefixes)
        if left_arm is None and right_arm is None:
            continue
        if left_arm is not None and left_arm == right_arm:
            continue
        foreign.append(pair)
    return foreign
