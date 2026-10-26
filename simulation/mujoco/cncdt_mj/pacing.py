"""Ritmul de redesenare a viewer-ului.

Bucla de simulare face 500 de pași pe secundă (`timestep` 0,002 s), dar fereastra nu are nevoie
de 500 de cadre: fiecare `viewer.sync()` desenează scena, iar la 500 fps GPU-ul ține laptopul
fierbinte degeaba — simulatul merge oricum în ritm real. Pacer-ul lasă desenul la un număr fix de
cadre pe secundă și sare peste restul pașilor. Modulul este pur, fără `mujoco`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

#: Cîte cadre pe secundă se desenează implicit. 30 ajunge pentru o demonstrație și înjumătățește
#: munca GPU față de 60; `--view-fps 0` redesenează la fiecare pas, ca înainte.
DEFAULT_VIEW_FPS = 30.0


@dataclass
class FramePacer:
    """Decide dacă pasul curent ajunge și pe ecran."""

    fps: float = DEFAULT_VIEW_FPS
    clock: Callable[[], float] = field(default=time.monotonic)
    interval_s: float = field(init=False)
    next_s: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        fps = float(self.fps)
        self.interval_s = 0.0 if fps <= 0.0 else 1.0 / fps

    def due(self) -> bool:
        """A venit momentul unui cadru nou? Fără limită, răspunsul este mereu da."""
        now = float(self.clock())
        if now + 1e-9 < self.next_s:
            return False
        self.next_s = now + self.interval_s
        return True

    def frames_per_second(self) -> float:
        """Ritmul efectiv, pentru mesajul de pornire (0 însemnînd „la fiecare pas")."""
        return 0.0 if self.interval_s <= 0.0 else 1.0 / self.interval_s
