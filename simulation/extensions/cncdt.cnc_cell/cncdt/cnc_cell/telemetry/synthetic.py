"""Generator de semnal sintetic pentru senzorii celulei.

Are două roluri:

1. **Rezervă în simulare.** Când scena nu are încă primii de senzori autorizați, extensia
   folosește acest generator, astfel încât pipeline-ul de date să poată fi testat complet
   înainte de a exista modelul 3D final.
2. **Sursă pentru testare.** Publicatorul de dezvoltare din ``telemetry/dev_publisher``
   folosește aceleași funcții, deci datele de test și cele din simulare au aceeași formă.

Semnalele sunt plauzibile fizic, nu măsurători reale: zgomot de bandă largă pentru vibrații,
sarcină corelată cu adâncimea de așchiere, temperatură cu inerție termică.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Any

from ..degradation.model import DegradationState

# Stările mașinii, în ordinea ciclului de producție.
MACHINE_STATES = ("IDLE", "LOADING", "CUTTING", "UNLOADING", "INSPECTING")


@dataclass
class SyntheticSignals:
    """Parametrii de referință ai semnalelor, în unități arbitrare consistente."""

    # Sarcina axului, 0..1 din puterea nominală.
    spindle_load_idle: float = 0.04
    spindle_load_cutting: float = 0.58
    spindle_load_spread: float = 0.045

    # Vibrații, în mm/s (viteză RMS pe carcasa axului).
    vibration_idle: float = 0.22
    vibration_cutting: float = 1.35
    vibration_spread: float = 0.16

    # Temperatura axului, în grade Celsius.
    temp_ambient: float = 24.0
    temp_cutting_offset: float = 16.0
    temp_time_constant_s: float = 90.0

    # Debit de răcire, litri pe minut.
    coolant_nominal: float = 8.5
    coolant_spread: float = 0.35
    coolant_fault_probability: float = 0.002

    # Efortul articulațiilor, în Nm.
    joint_effort_idle: float = 2.5
    joint_effort_cutting: float = 14.0

    # Avansul axelor, în mm/min.
    axis_feed_cutting: float = 1200.0

    seed: int | None = None


@dataclass
class SyntheticCell:
    """Produce eșantioane de senzor pentru starea curentă a celulei."""

    degradation: DegradationState = field(default_factory=DegradationState)
    signals: SyntheticSignals = field(default_factory=SyntheticSignals)

    state: str = "IDLE"
    _rng: random.Random = field(init=False, repr=False)
    _sequence: int = field(default=0, init=False)
    _temperature: float = field(default=0.0, init=False)
    _coolant_fault: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.signals.seed)
        self._temperature = self.signals.temp_ambient

    # ------------------------------------------------------------ stare
    def set_state(self, state: str) -> None:
        if state not in MACHINE_STATES:
            raise ValueError(f"stare necunoscută: {state}")
        self.state = state

    @property
    def is_cutting(self) -> bool:
        return self.state == "CUTTING"

    # ------------------------------------------------------------ eșantion
    def sample(self, dt_seconds: float = 0.1) -> dict[str, Any]:
        """Produce un eșantion de senzor și avansează degradarea.

        ``dt_seconds`` este pasul de simulare dintre două eșantioane.
        """
        self._sequence += 1
        self.degradation.advance(
            dt_seconds,
            cutting=self.is_cutting,
            moving=self.state in {"LOADING", "UNLOADING", "CUTTING"},
        )

        cutting = self.is_cutting
        tool = self.degradation.tool
        joint = self.degradation.joint

        # Sarcina axului: valoarea de referință, amplificată de uzură, plus zgomot.
        load_base = (
            self.signals.spindle_load_cutting if cutting else self.signals.spindle_load_idle
        )
        load = load_base * tool.load_multiplier
        load += self._rng.gauss(0.0, self.signals.spindle_load_spread)
        load = _clamp01(load)

        # Vibrațiile: cresc cu sarcina și cu uzura sculei.
        vibration_base = (
            self.signals.vibration_cutting if cutting else self.signals.vibration_idle
        )
        vibration = vibration_base * tool.vibration_multiplier
        vibration *= 1.0 + 0.35 * load
        vibration += abs(self._rng.gauss(0.0, self.signals.vibration_spread))
        # Uzura avansată produce șocuri ocazionale, care ridică factorul de vârf.
        if tool.index > 0.5 and self._rng.random() < 0.02 * tool.index:
            vibration *= self._rng.uniform(1.8, 3.2)

        # Temperatura are inerție: se apropie exponențial de țintă.
        target = self.signals.temp_ambient + (
            self.signals.temp_cutting_offset * (0.6 + 0.4 * load) if cutting else 0.0
        )
        alpha = min(1.0, dt_seconds / self.signals.temp_time_constant_s)
        self._temperature += (target - self._temperature) * alpha
        temperature = self._temperature + self._rng.gauss(0.0, 0.12)

        # Răcirea: poate cădea brusc, ceea ce este un defect de proces.
        if self._rng.random() < self.signals.coolant_fault_probability:
            self._coolant_fault = not self._coolant_fault
        coolant = 0.0 if self._coolant_fault else max(
            0.0, self._rng.gauss(self.signals.coolant_nominal, self.signals.coolant_spread)
        )

        # Efortul articulațiilor: crește cu frecarea.
        effort_base = (
            self.signals.joint_effort_cutting if cutting else self.signals.joint_effort_idle
        )
        effort = effort_base * joint.effort_multiplier
        effort += self._rng.gauss(0.0, 0.6)

        feed = (
            self.signals.axis_feed_cutting * joint.speed_penalty
            if cutting
            else 0.0
        )
        feed += self._rng.gauss(0.0, 8.0) if cutting else 0.0

        return {
            "sample_seq": self._sequence,
            "machine_state": self.state,
            "spindle_load": round(load, 5),
            "vibration": round(max(0.0, vibration), 5),
            "temp_c": round(temperature, 3),
            "coolant_flow_lpm": round(coolant, 3),
            "joint_effort": round(max(0.0, effort), 4),
            "axis_feed": round(max(0.0, feed), 2),
            "tool_wear_index": round(tool.index, 5),
            "joint_friction_index": round(joint.index, 5),
            "joint_friction_index_arm1": round(joint.index, 5),
            "joint_friction_index_arm2": round(joint.index, 5),
            "surface_quality": round(tool.surface_quality, 5),
            "coolant_fault": self._coolant_fault,
        }

    def sample_window(self, count: int, dt_seconds: float = 0.1) -> list[dict[str, Any]]:
        """Produce un lot de eșantioane, folosit de publicatorul de dezvoltare."""
        return [self.sample(dt_seconds) for _ in range(count)]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def deterministic_seed(*parts: str) -> int:
    """Seed reproductibil dintr-un identificator, pentru teste și pentru rejucare.

    Se folosește SHA-256, nu ``hash()``: hash-ul șirurilor este randomizat per proces.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def phase_progress(elapsed_seconds: float, cycle_seconds: float) -> float:
    """Fracțiunea din ciclu parcursă, în intervalul [0, 1)."""
    if cycle_seconds <= 0:
        return 0.0
    return (elapsed_seconds % cycle_seconds) / cycle_seconds


def state_for_phase(progress: float) -> str:
    """Mapează progresul ciclului pe starea mașinii.

    Împărțirea reflectă ciclul real: încărcare, așchiere, descărcare, inspecție.
    """
    if progress < 0.05:
        return "IDLE"
    if progress < 0.15:
        return "LOADING"
    if progress < 0.75:
        return "CUTTING"
    if progress < 0.85:
        return "UNLOADING"
    if progress < 0.97:
        return "INSPECTING"
    return "IDLE"


def sine_noise(t: float, frequency_hz: float, amplitude: float, phase: float = 0.0) -> float:
    """Componentă periodică, utilă pentru a imita rotația axului."""
    return amplitude * math.sin(2.0 * math.pi * frequency_hz * t + phase)
