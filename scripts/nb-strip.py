#!/usr/bin/env python3
"""Golește output-urile notebook-urilor, înainte de commit.

Un `.ipynb` cu output-uri are diff-uri ilizibile: fiecare rulare schimbă linii în tot fișierul,
imaginile intră în git ca base64, iar un număr de execuție diferit face zgomot pe fiecare celulă.
Convenția proiectului (vezi `ops/runbooks/notebooks.md`): în repo intră **sursa** notebook-ului,
cu output-urile golite; notebook-ul rulat rămâne artefact, în S3 ori în `ml/artifacts/`, care e
ignorat de git.

Rulează pe fișiere ori pe directoare (recursiv), în loc, și tipărește ce a schimbat.

    python scripts/nb-strip.py ml/experiments/notebooks
    python scripts/nb-strip.py --check ml/experiments/notebooks   # doar raportează; cod 1 dacă e ceva de golit

Scriptul folosește doar biblioteca standard: `nbformat`, `nbstripout` și `jupyter` nu sunt
dependențe ale proiectului, iar un notebook valid este JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ce se golește dintr-o celulă de cod: rezultatul rulării și numărul de execuție.
OUTPUT_KEY = "outputs"
EXECUTION_COUNT_KEY = "execution_count"

# Metadatele lăsate de uneltele care rulează notebook-uri (papermill) descriu rularea, nu sursa.
EXECUTION_METADATA_KEYS = ("execution",)


def strip_notebook(notebook: dict[str, Any]) -> int:
    """Golește output-urile din copia dată și întoarce câte celule au fost atinse."""
    touched = 0

    for cell in notebook.get("cells", []):
        if cell.get("cell_type") != "code":
            continue

        changed = False
        if cell.get(OUTPUT_KEY):
            cell[OUTPUT_KEY] = []
            changed = True
        if cell.get(EXECUTION_COUNT_KEY) is not None:
            cell[EXECUTION_COUNT_KEY] = None
            changed = True

        metadata = cell.get("metadata")
        if isinstance(metadata, dict):
            for key in EXECUTION_METADATA_KEYS:
                if metadata.pop(key, None) is not None:
                    changed = True

        if changed:
            touched += 1

    return touched


def write_notebook(path: Path, notebook: dict[str, Any]) -> None:
    """Scrie notebook-ul în stilul pe care îl scrie și JupyterLab: indentare de un spațiu, chei sortate."""
    text = json.dumps(notebook, indent=1, sort_keys=True, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def notebooks_under(paths: list[Path]) -> list[Path]:
    """Fișierele `.ipynb` din căile date, fără punctele de control ale Jupyter."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*.ipynb") if ".ipynb_checkpoints" not in p.parts))
        elif path.suffix == ".ipynb":
            found.append(path)
    return found


def build_parser() -> argparse.ArgumentParser:
    """Opțiunile: ce se golește și dacă se scrie ori doar se raportează."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path,
                        help="fișiere `.ipynb` ori directoare cu notebook-uri")
    parser.add_argument("--check", action="store_true",
                        help="nu scrie nimic; întoarce 1 dacă vreun notebook are output-uri")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Golește output-urile și raportează ce s-a schimbat."""
    args = build_parser().parse_args(argv)
    paths = notebooks_under(args.paths)

    if not paths:
        print("nu am găsit niciun notebook")
        return 0

    pending = 0
    for path in paths:
        try:
            notebook = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            print(f"[eroare] {path}: nu este JSON valid ({error})", file=sys.stderr)
            return 2

        touched = strip_notebook(notebook)
        if not touched:
            print(f"  curat  {path}")
            continue

        pending += 1
        if args.check:
            print(f"  de golit  {path} ({touched} celule cu output-uri)")
            continue

        write_notebook(path, notebook)
        print(f"  golit  {path} ({touched} celule)")

    if args.check and pending:
        print(f"\n{pending} notebook-uri au output-uri; rulează `make nb-strip` înainte de commit")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
