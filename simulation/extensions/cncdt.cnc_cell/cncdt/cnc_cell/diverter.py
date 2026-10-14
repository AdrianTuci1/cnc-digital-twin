"""Poarta de sortare de la zona lamei/piston.

Piesa ajunge la capătul benzii de inspecție, în dreptul lamei. Aici se oprește și așteaptă
un verdict, care poate veni din două locuri:

1. **cloud** — cadrul trimis la endpoint-ul de viziune se întoarce ca verdict pe topicul de
   control; este sursa autoritară;
2. **simulare** — previziunea locală a modelului de degradare, disponibilă la sfârșitul
   ciclului, când cloudul nu a răspuns.

Când niciuna nu sosește, piesa urmează :attr:`DiverterGate.fail_safe`, ruta de rezervă.
Implicit aceasta este rebutul: o piesă fără verdict nu are cum să fie declarată bună, iar
lada de piese bune este cea care ajunge la client.

Modulul este matematică pură și callback-uri. Nu importă Isaac Sim, deci se testează în
``tests/unit/test_sim_modules.py``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .sorting import (
    Route,
    SortDecision,
    decision_for_route,
    decision_for_verdict,
    normalize_verdict,
)

FAIL_SAFE_REASON = "timeout"
LOCAL_REASON = "local"


class GateState(str, Enum):
    """Starea porții de sortare."""

    IDLE = "IDLE"  # nicio piesă în poartă
    WAITING = "WAITING"  # piesa este oprită, se așteaptă verdictul


@dataclass(frozen=True)
class GateTicket:
    """Piesa oprită în poartă, cu cadrul care a fost trimis la inspecție."""

    part_id: str
    cycle_id: str
    arrived_at: float
    frame_key: str = ""
    image_captured: bool = False
    camera: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "cycle_id": self.cycle_id,
            "arrived_at": round(self.arrived_at, 3),
            "frame_key": self.frame_key,
            "image_captured": self.image_captured,
            "camera": self.camera,
        }


@dataclass
class DiverterGate:
    """Oprește piesa la lamă, așteaptă verdictul și aplică ruta.

    ``route`` execută decizia în scenă (opritorul și, dacă există, traseul de role), iar
    ``hold`` ridică opritorul la sosirea piesei. Ambele sunt opționale: fără ele decizia
    rămâne raportată în jurnal, ca orice altă legătură de scenă lipsă.
    """

    route: Callable[[SortDecision], None] | None = None
    hold: Callable[[bool], None] | None = None
    note: Callable[[str], None] | None = None
    # Cu previziunea locală oprită, o piesă fără verdict cloud merge direct pe ruta de
    # rezervă, fără să aștepte sfârșitul ciclului.
    use_local_prediction: bool = True
    fail_safe: Route = Route.SCRAP
    clock: Callable[[], float] = time.monotonic

    state: GateState = GateState.IDLE
    ticket: GateTicket | None = None
    last_decision: SortDecision | None = None

    waits: int = 0
    cloud_routes: int = 0
    local_routes: int = 0
    fail_safe_routes: int = 0
    stale_verdicts: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------ sosirea piesei
    def arrived(
        self,
        part_id: str,
        cycle_id: str,
        now: float | None = None,
        frame_key: str = "",
        image_captured: bool = False,
        camera: str = "",
    ) -> GateTicket | None:
        """Piesa intră în poartă: opritorul urcă și fereastra de verdict se deschide.

        Întoarce biletul creat, ori ``None`` dacă poarta este deja ocupată. O a doua piesă
        nu poate fi oprită peste prima: banda ar împinge-o pe cea deja oprită.
        """
        if self.state is GateState.WAITING and self.ticket is not None:
            self._note(
                f"poarta este ocupată de {self.ticket.part_id}; {part_id} nu a fost oprită"
            )
            return None

        self.ticket = GateTicket(
            part_id=part_id,
            cycle_id=cycle_id,
            arrived_at=self._now(now),
            frame_key=frame_key,
            image_captured=image_captured,
            camera=camera,
        )
        self.state = GateState.WAITING
        self.waits += 1
        self._hold(True)
        frame_state = "cu imagine" if image_captured else "fără imagine"
        self._note(f"piesa {part_id} oprită la lamă ({frame_state}); se așteaptă verdictul")
        return self.ticket

    # ------------------------------------------------------------ rezolvarea
    def verdict(
        self,
        part_id: str,
        verdict: str,
        reason: str = "cloud",
        source: str = "cloud",
    ) -> SortDecision | None:
        """Aplică verdictul de inspecție.

        Un verdict pentru piesa oprită încheie așteptarea. Un verdict pentru o altă piesă
        este socotit întârziat: opritorul rămâne pe seama piesei din poartă, iar verdictul
        este numărat și jurnalizat, fără să miște lama sub piesa greșită.
        """
        decision = decision_for_verdict(part_id, verdict, reason=reason, source=source)

        if self.ticket is None:
            # Fără nicio piesă în poartă, verdictul se aplică pe loc. Este calea folosită
            # de comenzile de operator și de testele care nu ridică poarta.
            self._apply(decision)
            self._count_source(decision)
            return decision

        if self.ticket.part_id != part_id:
            self.stale_verdicts += 1
            self._note(
                f"verdict întârziat pentru {part_id} "
                f"(verdict {normalize_verdict(verdict)}); în poartă este {self.ticket.part_id}"
            )
            return None

        self._apply(decision)
        self.cloud_routes += 1
        return decision

    def supply_local(
        self,
        part_id: str,
        rejected: bool,
        reason: str = "",
        now: float | None = None,
    ) -> SortDecision | None:
        """Închide fereastra cu previziunea locală ori cu ruta de rezervă.

        Se apelează la sfârșitul ciclului, când piesa trebuie eliberată. Dacă verdictul
        cloud a sosit deja, piesa nu mai este în poartă și nu se întoarce nimic.
        """
        if self.ticket is None or self.ticket.part_id != part_id:
            return None

        if self.use_local_prediction:
            decision = decision_for_verdict(
                part_id,
                "REJECT" if rejected else "PASS",
                reason=reason or LOCAL_REASON,
                source="simulation",
            )
            self.local_routes += 1
        else:
            decision = decision_for_route(part_id, self.fail_safe, reason=FAIL_SAFE_REASON)
            self.fail_safe_routes += 1
            self._note(
                f"verdictul nu a sosit pentru {part_id}; "
                f"piesa urmează ruta de rezervă ({decision.route.value})"
            )

        self._apply(decision, now=now)
        return decision

    def reset(self) -> None:
        """Golește poarta fără să aplice nicio rută. Folosit la oprirea ciclului."""
        if self.ticket is not None:
            self._note(f"poarta eliberată fără decizie (piesa {self.ticket.part_id})")
        self.ticket = None
        self.state = GateState.IDLE

    # ------------------------------------------------------------ ajutoare
    def waited(self, now: float | None = None) -> float:
        """Cât a stat piesa în poartă, în secunde."""
        if self.ticket is None:
            return 0.0
        return max(0.0, self._now(now) - self.ticket.arrived_at)

    def _now(self, now: float | None) -> float:
        return self.clock() if now is None else now

    def _hold(self, stop: bool) -> None:
        if self.hold is None:
            return
        try:
            self.hold(stop)
        except Exception as exc:  # pragma: no cover - depinde de scenă
            self._note(f"oprirea piesei la lamă a eșuat: {exc}")

    def _apply(self, decision: SortDecision, now: float | None = None) -> None:
        waited = self.waited(now)
        if self.route is not None:
            self.route(decision)
        self.last_decision = decision
        self.history.append({**decision.as_dict(), "waited_s": round(waited, 3)})
        if len(self.history) > 50:
            del self.history[:-50]
        self.ticket = None
        self.state = GateState.IDLE

    def _count_source(self, decision: SortDecision) -> None:
        if decision.source == "cloud":
            self.cloud_routes += 1
        elif decision.source == "simulation":
            self.local_routes += 1
        else:
            self.fail_safe_routes += 1

    def _note(self, message: str) -> None:
        if self.note is not None:
            self.note(message)

    @property
    def waiting_for(self) -> str:
        return self.ticket.part_id if self.ticket is not None else "-"

    @property
    def status_line(self) -> str:
        return (
            f"așteptări {self.waits} | cloud {self.cloud_routes} | "
            f"local {self.local_routes} | rezervă {self.fail_safe_routes}"
        )
