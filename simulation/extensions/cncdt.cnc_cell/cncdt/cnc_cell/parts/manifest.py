"""Manifestul de etichete al pieselor generate.

Un rând JSON per variantă, în ordinea generării. Manifestul este sursa de adevăr pentru
antrenare: el leagă ``part_id`` de clasa reală. Calea de inferență nu are acces la el.

Formatul unui rând (schema 1.1.0)::

    {"part_id": "part-00000007", "seed": 7, "family": "wheel_100", "verdict": "REJECT",
     "defect": "TOOTH_MISSING", "params": {...}}

Față de 1.0.0, rândul are și ``family``: modelul de inspecție are două capete, unul care
spune *ce piesă* este (familia) și unul care spune *dacă* e conformă. Un manifest vechi, de
dinainte de familii, se citește în continuare: el descrie exact familia de referință.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

# Numele familiei de referință stă în generator, lângă tabelul de familii, ca să nu fie
# scris de două ori. Importul nu întoarce dependența: generatorul nu știe de manifest.
from cncdt.cnc_cell.parts.gear import DEFAULT_FAMILY

SCHEMA_VERSION = "1.1.0"


@dataclass(frozen=True)
class LabelRecord:
    """Eticheta unei variante, independentă de mesh."""

    part_id: str
    seed: int
    family: str
    verdict: str
    defect: str
    params: Mapping[str, Any]

    def to_json(self) -> str:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "part_id": self.part_id,
            "seed": self.seed,
            "family": self.family,
            "verdict": self.verdict,
            "defect": self.defect,
            "params": dict(self.params),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "LabelRecord":
        payload = json.loads(line)
        return cls(
            part_id=str(payload["part_id"]),
            seed=int(payload["seed"]),
            family=str(payload.get("family", DEFAULT_FAMILY)),
            verdict=str(payload["verdict"]),
            defect=str(payload["defect"]),
            params=payload.get("params", {}),
        )


def write_manifest(path: str | Path, records: Iterable[LabelRecord]) -> int:
    """Scrie manifestul, un rând per etichetă. Întoarce numărul de rânduri scrise."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.to_json())
            handle.write("\n")
            count += 1
    return count


def read_manifest(path: str | Path) -> list[LabelRecord]:
    """Citește manifestul, sărind liniile goale."""
    source = Path(path)
    records: list[LabelRecord] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(LabelRecord.from_json(line))
    return records
