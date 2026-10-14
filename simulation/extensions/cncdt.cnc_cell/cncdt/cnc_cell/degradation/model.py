"""Modelul de degradare al celulei CNC.

Aici stă răspunsul la „cum producem degradare controlată": două procese lente, plus un
injector de defecte. Totul este matematică pură, fără dependențe de Isaac Sim, ca să poată
fi testat și reproductibil.

Relația cu fizica din scenă:

- uzura sculei crește amplitudinea vibrațiilor și sarcina axului, și degradează calitatea
  suprafeței — deci crește probabilitatea unui verdict REJECT la inspecție;
- frecarea articulațiilor crește efortul necesar și întârzie ciclurile de mișcare;
- injectorul de defecte marchează anumite piese ca defecte, independent de uzură, pentru
  testarea rezistenței pipeline-ului.

Valorile nu sunt calibrate pe o mașină reală. Sunt parametri de simulare, ale căror
constante se reglează prin variabilele de mediu din :mod:`cncdt.cnc_cell.config`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

# Intervalul de valori al indicilor de degradare. 1.0 este limita de avarie.
MAX_DEGRADATION = 1.0


def _clamp(value: float, low: float = 0.0, high: float = MAX_DEGRADATION) -> float:
    return max(low, min(high, value))


@dataclass
class ToolWear:
    """Uzura sculei de așchiere.

    Crește monoton cu timpul de așchiere. La valori mari, vibrațiile și sarcina axului cresc
    vizibil, iar calitatea suprafeței scade.
    """

    rate_per_second: float = 0.0015
    index: float = 0.0

    def advance(self, seconds: float, cutting: bool = True) -> float:
        """Avansează uzura doar în timpul așchierii."""
        if cutting and seconds > 0:
            self.index = _clamp(self.index + self.rate_per_second * seconds)
        return self.index

    def reset(self, partial: bool = False) -> None:
        """Schimb de sculă. ``partial=True`` modelează o sculă deja uzată parțial."""
        self.index = 0.12 if partial else 0.0

    @property
    def vibration_multiplier(self) -> float:
        """Amplificarea vibrațiilor. Uzura medie dublează amplitudinea."""
        return 1.0 + 1.0 * self.index ** 1.5

    @property
    def load_multiplier(self) -> float:
        """Amplificarea sarcinii axului. Uzura avansată o crește cu 70%."""
        return 1.0 + 0.7 * self.index

    @property
    def surface_quality(self) -> float:
        """Calitatea suprafeței, de la 1.0 (perfectă) la 0.0 (inacceptabilă)."""
        return _clamp(1.0 - self.index ** 1.2)

    @property
    def severity(self) -> str:
        if self.index < 0.3:
            return "ok"
        if self.index < 0.6:
            return "worn"
        if self.index < 0.85:
            return "degraded"
        return "critical"


@dataclass
class JointFriction:
    """Frecarea articulațiilor robotului.

    Crește cuplul necesar și reduce viteza de execuție, ceea ce se vede în timpul de ciclu.
    """

    rate_per_second: float = 0.0008
    index: float = 0.0

    def advance(self, seconds: float, moving: bool = True) -> float:
        if moving and seconds > 0:
            self.index = _clamp(self.index + self.rate_per_second * seconds)
        return self.index

    def reset(self) -> None:
        self.index = 0.0

    @property
    def effort_multiplier(self) -> float:
        """Cuplul necesar crește cu până la 80% la frecare maximă."""
        return 1.0 + 0.8 * self.index

    @property
    def speed_penalty(self) -> float:
        """Factor de încetinire, între 1.0 (fără penalizare) și 0.6."""
        return 1.0 - 0.4 * self.index

    @property
    def severity(self) -> str:
        if self.index < 0.3:
            return "ok"
        if self.index < 0.7:
            return "elevated"
        return "high"


@dataclass
class DefectInjector:
    """Injectează defecte aleatoare, independent de uzură.

    Folosit pentru testarea rezistenței pipeline-ului: piese defecte apar la întâmplare,
    iar sistemul trebuie să le detecteze și să acționeze pistonul de rejecție.
    """

    probability: float = 0.05
    seed: int | None = None
    forced_next: bool = False
    injected_count: int = 0
    produced_count: int = 0

    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def should_reject(self, surface_quality: float) -> tuple[bool, str]:
        """Decide soarta piesei curente.

        Întoarce ``(defect, motiv)``. Motivul ajunge în telemetrie și în verdictul de
        inspecție, ca să se poată distinge cauza.
        """
        self.produced_count += 1

        if self.forced_next:
            self.forced_next = False
            self.injected_count += 1
            return True, "injected"

        # O suprafață degradată sub prag produce defecte fără intervenție aleatoare.
        if surface_quality < 0.55:
            return True, "tool_wear"

        if self._rng.random() < self.probability:
            self.injected_count += 1
            return True, "random"

        return False, "in_spec"

    def force_next(self) -> None:
        """Marchează următoarea piesă ca defectă, la cerere din panoul de control."""
        self.forced_next = True

    def set_probability(self, probability: float) -> None:
        self.probability = _clamp(probability, 0.0, 1.0)


@dataclass
class DegradationState:
    """Starea completă de degradare a celulei, plus statistica ciclului."""

    tool: ToolWear = field(default_factory=ToolWear)
    joint: JointFriction = field(default_factory=JointFriction)
    defects: DefectInjector = field(default_factory=DefectInjector)

    cycle_index: int = 0
    part_index: int = 0
    rejected_total: int = 0
    accepted_total: int = 0

    # ------------------------------------------------------------ avansare
    def advance(self, seconds: float, cutting: bool = True, moving: bool = True) -> None:
        """Avansează ambele procese de degradare cu ``seconds`` secunde."""
        self.tool.advance(seconds, cutting=cutting)
        self.joint.advance(seconds, moving=moving)

    # ------------------------------------------------------------ ciclu
    def begin_cycle(self) -> str:
        """Începe un ciclu nou și întoarce identificatorii generați."""
        self.cycle_index += 1
        self.part_index += 1
        return self.part_id

    @property
    def cycle_id(self) -> str:
        return f"cyc-{self.cycle_index:06d}"

    @property
    def part_id(self) -> str:
        return f"part-{self.part_index:06d}"

    def finish_part(self) -> tuple[bool, str]:
        """Închide piesa curentă și decide dacă este defectă."""
        rejected, reason = self.defects.should_reject(self.tool.surface_quality)
        if rejected:
            self.rejected_total += 1
        else:
            self.accepted_total += 1
        return rejected, reason

    # ------------------------------------------------------------ acțiuni de operator
    def inject_tool_wear(self, amount: float = 0.25) -> None:
        self.tool.index = _clamp(self.tool.index + amount)

    def inject_joint_friction(self, amount: float = 0.25) -> None:
        self.joint.index = _clamp(self.joint.index + amount)

    def set_tool_wear(self, value: float) -> None:
        """Setează direct uzura sculei, pentru control progresiv din panou sau din cloud."""
        self.tool.index = _clamp(value)

    def set_joint_friction(self, value: float) -> None:
        """Setează direct frecarea articulațiilor."""
        self.joint.index = _clamp(value)

    def replace_tool(self) -> None:
        self.tool.reset()

    def repair_joints(self) -> None:
        self.joint.reset()

    def reset_all(self) -> None:
        self.tool.reset()
        self.joint.reset()
        self.defects.forced_next = False

    # ------------------------------------------------------------ raportare
    @property
    def reject_rate(self) -> float:
        total = self.rejected_total + self.accepted_total
        return self.rejected_total / total if total else 0.0

    def snapshot(self) -> dict[str, Any]:
        """Starea curentă, gata de publicat în telemetrie."""
        return {
            "tool_wear_index": round(self.tool.index, 4),
            "tool_wear_severity": self.tool.severity,
            "joint_friction_index": round(self.joint.index, 4),
            "joint_friction_severity": self.joint.severity,
            "surface_quality": round(self.tool.surface_quality, 4),
            "cycle_index": self.cycle_index,
            "part_index": self.part_index,
            "rejected_total": self.rejected_total,
            "accepted_total": self.accepted_total,
            "reject_rate": round(self.reject_rate, 4),
            "random_defect_probability": round(self.defects.probability, 4),
        }
