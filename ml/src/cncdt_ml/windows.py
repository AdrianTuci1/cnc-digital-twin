"""Gruparea eșantioanelor brute în ferestre.

Simularea publică eșantioane la ~10 Hz. Lambda-ul primește de la Firehose un lot tamponat
la 60 de secunde și trebuie să îl taie în ferestre fixe, aliniate la ceasul UTC, ca ferestrele
să fie comparabile între dispozitive și între rulări.

Alinierea folosește împărțirea întreagă la ``window_s * 1000``, deci fiecare fereastră
începe la un multiplu al duratei, măsurat de la epoch.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, MutableMapping, Sequence


def window_key(ts_ms: int, window_s: int = 60) -> int:
    """Identificatorul ferestrei, stabil și aliniat la epoch."""
    if window_s <= 0:
        raise ValueError("window_s trebuie să fie pozitiv")
    return int(ts_ms) // (int(window_s) * 1000)


def window_bounds(ts_ms: int, window_s: int = 60) -> tuple[int, int]:
    """Intervalul ``[start, end)`` al ferestrei, în milisecunde epoch."""
    key = window_key(ts_ms, window_s)
    start = key * int(window_s) * 1000
    return start, start + int(window_s) * 1000


def window_start_ms(key: int, window_s: int = 60) -> int:
    """Momentul de început al unei ferestre, pornind de la cheia ei."""
    return int(key) * int(window_s) * 1000


def group_windows(
    records: Iterable[Mapping[str, Any]],
    window_s: int = 60,
    *,
    timestamp_key: str = "ts_ms",
) -> dict[int, list[Mapping[str, Any]]]:
    """Grupează înregistrările pe ferestre, păstrând ordinea de sosire în fiecare grupă.

    Înregistrările fără marcaj de timp numeric sunt ignorate: fără ele nu se poate decide
    fereastra, iar o fereastră greșită produce caracteristici înșelătoare.
    """
    groups: dict[int, list[Mapping[str, Any]]] = {}
    for record in records:
        raw = record.get(timestamp_key)
        try:
            ts_ms = int(raw)
        except (TypeError, ValueError):
            continue
        groups.setdefault(window_key(ts_ms, window_s), []).append(record)
    return groups


def sort_by_timestamp(
    records: Sequence[MutableMapping[str, Any]],
    *,
    timestamp_key: str = "ts_ms",
) -> list[MutableMapping[str, Any]]:
    """Copie sortată după marcajul de timp, pentru calculul de ferestre glisante."""
    def _key(record: MutableMapping[str, Any]) -> int:
        try:
            return int(record.get(timestamp_key, 0))
        except (TypeError, ValueError):
            return 0

    return sorted(records, key=_key)
