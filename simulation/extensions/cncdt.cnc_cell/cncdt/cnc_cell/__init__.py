"""Extensia Kit a celulei CNC.

Punctul de intrare este :class:`cncdt.cnc_cell.extension.CncCellExtension`, care este
instanțiat de Kit la pornire (vezi ``config/extension.toml``).

Modulele sunt împărțite intenționat:

- :mod:`cncdt.cnc_cell.config` — configurație din variabile de mediu;
- :mod:`cncdt.cnc_cell.degradation.model` — matematică pură, testabilă fără Isaac Sim;
- :mod:`cncdt.cnc_cell.telemetry.features` — calcul de caracteristici, pur;
- :mod:`cncdt.cnc_cell.telemetry.synthetic` — semnale sintetice, pure;
- :mod:`cncdt.cnc_cell.telemetry.sensors` — citirea senzorilor din scenă;
- :mod:`cncdt.cnc_cell.telemetry.publisher` — publicare MQTT;
- :mod:`cncdt.cnc_cell.sim_control` — orchestratorul ciclului;
- :mod:`cncdt.cnc_cell.ui.panel` — panoul de control din ``omni.ui``.

Modulele pure pot fi importate și rulate în afara Isaac Sim (teste unitare, publicatorul de
dezvoltare). Doar :mod:`cncdt.cnc_cell.extension` și :mod:`cncdt.cnc_cell.ui.panel` cer Kit;
importul lor este izolat în ``try/except``.
"""

from __future__ import annotations

__all__ = ["CncCellExtension", "HAS_KIT"]

try:
    import omni.ext  # noqa: F401

    HAS_KIT = True
except ImportError:  # pragma: no cover - se întâmplă doar în afara Isaac Sim.
    HAS_KIT = False

try:
    from .extension import CncCellExtension
except ImportError:  # pragma: no cover - doar dacă lipsește un modul intern.
    CncCellExtension = None  # type: ignore[assignment]
