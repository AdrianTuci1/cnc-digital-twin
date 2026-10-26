"""Jurnalul scurt al demonstrației, citit în viewer și în panoul de uzură.

Cînd viewer-ul rulează, linia din terminal nu se vede: evenimentele care contează pentru operator
— conexiunea IoT, eșantioanele trimise, verdictul endpoint-ului, comanda din panou, starea
ciclului — se țin într-un inel cu ceasul lor și se afișează în ambele ferestre. Modulul este pur
(stdlib), deci testele nu cer `mujoco` și nu deschid nicio fereastră.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

#: Cîte evenimente se păstrează; panoul arată ultimele, viewer-ul doar cîteva.
DEFAULT_CAPACITY = 40

#: Formatul ceasului din fața fiecărei linii.
CLOCK_FORMAT = "%H:%M:%S"


@dataclass(frozen=True)
class Entry:
    """Un eveniment: cînd s-a întîmplat și ce s-a întîmplat."""

    at_s: float
    text: str

    def line(self) -> str:
        """Linia afișată: ora locală, apoi textul."""
        return f"{time.strftime(CLOCK_FORMAT, time.localtime(self.at_s))}  {self.text}"


class Journal:
    """Inelul de evenimente; cel mai vechi iese cînd se umple."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY,
                 clock: Callable[[], float] = time.time) -> None:
        if capacity < 1:
            raise ValueError("jurnalul are nevoie de cel puțin o linie")
        self.capacity = int(capacity)
        self._clock = clock
        self._entries: deque[Entry] = deque(maxlen=self.capacity)

    def add(self, text: str) -> Entry:
        """Adaugă un eveniment cu ceasul de acum și îl întoarce."""
        entry = Entry(float(self._clock()), str(text))
        self._entries.append(entry)
        return entry

    def entries(self) -> tuple[Entry, ...]:
        """Toate evenimentele păstrate, de la cel mai vechi la cel mai nou."""
        return tuple(self._entries)

    def lines(self, count: int | None = None) -> list[str]:
        """Ultimele ``count`` linii formatate; fără ``count``, tot jurnalul."""
        entries = list(self._entries)
        if count is not None:
            entries = entries[-int(count):] if int(count) > 0 else []
        return [entry.line() for entry in entries]

    def __len__(self) -> int:
        return len(self._entries)
