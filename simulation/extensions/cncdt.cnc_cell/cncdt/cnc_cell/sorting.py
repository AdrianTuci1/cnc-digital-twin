"""Ruta piesei după inspecție: unde o duce transportul.

Verdictul de inspecție este singura intrare a deciziei. Ieșirea este o rută:

- ``GOOD`` — piesa trece de postul de inspecție și ajunge în lada de piese bune;
- ``SCRAP`` — piesa este deviată în jgheabul de rebut.

Decizia este independentă de mecanismul care o execută. Scena are două mecanisme, iar
extensia le tratează la fel, prin callback-uri:

1. **poarta de rejecție** (``/World/Scrap/Piston``) — opritorul urcă 0,12 m în calea piesei,
   o lamă fixă o ghidează spre jgheab, iar piesele bune trec mai departe. Poarta se retrage
   la prima piesă bună de după un rebut;
2. **joncțiunea de benzi** (``ConveyorBelt_A20``, Y_MERGE) — banda are două trasee de role
   independente (``Rollers``, ``Rollers_01``). Ruta pornește exact unul dintre ele, deci
   banda însăși alege ieșirea, fără nicio poartă în calea piesei.

Decizia se calculează fără scenă și fără Kit (:func:`route_for_verdict`,
:class:`SortDecision`), deci este testată în ``tests/unit/test_sim_modules.py``. Legarea de
prim-uri se face o singură dată, în :class:`PartRouter`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Route(str, Enum):
    """Ieșirea piesei din celulă."""

    GOOD = "GOOD"
    SCRAP = "SCRAP"


# Verdicturile de rebut. Restul verdicturilor cunoscute (``PASS``, ``GOOD``, ``OK``) trec
# piesa mai departe; un verdict necunoscut este tratat la fel, iar decizia păstrează
# verdictul brut pentru jurnal.
SCRAP_VERDICTS = frozenset({"REJECT", "REJECTED", "FAIL", "FAILED", "DEFECT", "SCRAP", "NOK"})
GOOD_VERDICTS = frozenset({"PASS", "PASSED", "GOOD", "OK", "ACCEPT", "ACCEPTED"})


def normalize_verdict(verdict: str) -> str:
    """Aduce verdictul la forma canonică: majuscule, fără spații în plus."""
    return str(verdict or "").strip().upper()


def route_for_verdict(verdict: str) -> Route:
    """Întoarce ruta pentru verdictul dat."""
    return Route.SCRAP if normalize_verdict(verdict) in SCRAP_VERDICTS else Route.GOOD


def is_known_verdict(verdict: str) -> bool:
    """Adevărat dacă verdictul este recunoscut explicit, ca rebut sau ca acceptat."""
    normalized = normalize_verdict(verdict)
    return normalized in SCRAP_VERDICTS or normalized in GOOD_VERDICTS


@dataclass(frozen=True)
class SortDecision:
    """Decizia luată pentru o piesă: ce rută urmează și de ce."""

    part_id: str
    verdict: str
    route: Route
    reason: str = "cloud"
    source: str = "cloud"

    @property
    def is_scrap(self) -> bool:
        return self.route is Route.SCRAP

    def as_dict(self) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "verdict": normalize_verdict(self.verdict),
            "route": self.route.value,
            "reason": self.reason,
            "source": self.source,
        }


def decision_for_verdict(
    part_id: str,
    verdict: str,
    reason: str = "cloud",
    source: str = "cloud",
) -> SortDecision:
    """Compune decizia de sortare pentru un verdict primit."""
    return SortDecision(
        part_id=part_id,
        verdict=verdict,
        route=route_for_verdict(verdict),
        reason=reason,
        source=source,
    )


def decision_for_route(
    part_id: str,
    route: Route,
    reason: str = "timeout",
    source: str = "fail-safe",
) -> SortDecision:
    """Compune decizia direct din rută, fără un verdict de inspecție.

    Folosită de poarta de sortare când verdictul nu sosește, iar piesa trebuie totuși
    eliberată: ruta de rezervă este o decizie de siguranță, nu o inspecție.
    """
    return SortDecision(
        part_id=part_id,
        verdict="REJECT" if route is Route.SCRAP else "PASS",
        route=route,
        reason=reason,
        source=source,
    )


@dataclass
class PartRouter:
    """Aplică ruta în scenă, prin callback-uri legate de prim-uri.

    ``gate`` acționează opritorul de rejecție (``True`` îl ridică, ``False`` îl retrage),
    ``track`` selectează traseul de role al joncțiunii. Ambele sunt opționale: fără ele
    decizia rămâne raportată în jurnal, ca orice altă legătură de scenă lipsă.
    """

    gate: Callable[[bool], None] | None = None
    track: Callable[[Route], None] | None = None
    note: Callable[[str], None] | None = None

    good: int = 0
    scrap: int = 0
    last_route: str = "-"
    last_verdict: str = "-"
    last_reason: str = "-"
    last_part_id: str = "-"
    history: list[dict[str, Any]] = field(default_factory=list)

    def apply(self, decision: SortDecision) -> Route:
        """Execută decizia: ridică ori retrage opritorul și selectează traseul."""
        if decision.is_scrap:
            self.scrap += 1
            if self.gate is not None:
                self.gate(True)
        else:
            self.good += 1
            if self.gate is not None:
                self.gate(False)

        if self.track is not None:
            self.track(decision.route)

        self.last_route = decision.route.value
        self.last_verdict = normalize_verdict(decision.verdict)
        self.last_reason = decision.reason
        self.last_part_id = decision.part_id

        self.history.append(decision.as_dict())
        if len(self.history) > 50:
            del self.history[:-50]

        if self.note is not None:
            self.note(
                f"piesa {decision.part_id}: ruta {decision.route.value} "
                f"(verdict {self.last_verdict}, sursă {decision.source})"
            )
        return decision.route

    def route(self, part_id: str, verdict: str, reason: str = "cloud", source: str = "cloud") -> Route:
        """Scurtătură: compune decizia din verdict și o aplică."""
        return self.apply(decision_for_verdict(part_id, verdict, reason=reason, source=source))

    @property
    def scrap_share(self) -> float:
        total = self.good + self.scrap
        return self.scrap / total if total else 0.0

    def status_line(self) -> str:
        return f"bune {self.good} / rebut {self.scrap} (ultima rută: {self.last_route})"
