"""Teste pentru `scripts/nb-strip.py`, curățenia notebook-urilor dinainte de commit.

Scriptul nu e pe `sys.path` (numele are cratimă și stă în `scripts/`), deci se încarcă prin cale,
ca modulele Lambda din alte teste. Ce apără testele: output-urile chiar ies, sursa rămâne neatinsă,
iar `--check` nu scrie nimic.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


nb_strip = load_module("nb_strip", "scripts/nb-strip.py")


def notebook_with_outputs() -> dict:
    """Un notebook cu o celulă de cod rulată, una nerulată și una de markdown."""
    return {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Explorare"],
            },
            {
                "cell_type": "code",
                "execution_count": 7,
                "metadata": {"execution": {"iopub.status.busy": "2026-01-01T00:00:00Z"}},
                "outputs": [{"output_type": "stream", "name": "stdout", "text": ["gata\n"]}],
                "source": ["print('gata')"],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["import cncdt_ml"],
            },
        ],
        "metadata": {"kernelspec": {"name": "python3"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def test_output_urile_si_numarul_de_executie_ies_din_celule() -> None:
    """O celulă rulată rămâne cu sursa, fără rezultat și fără număr de execuție."""
    notebook = notebook_with_outputs()

    assert nb_strip.strip_notebook(notebook) == 1

    cell = notebook["cells"][1]
    assert cell["outputs"] == []
    assert cell["execution_count"] is None
    assert cell["source"] == ["print('gata')"]
    assert "execution" not in cell["metadata"], "metadatele rulării descriu execuția, nu sursa"


def test_celulele_de_markdown_ramân_neatinse() -> None:
    """Curățarea nu atinge textul notebook-ului."""
    notebook = notebook_with_outputs()
    before = json.dumps(notebook["cells"][0], sort_keys=True)

    nb_strip.strip_notebook(notebook)

    assert json.dumps(notebook["cells"][0], sort_keys=True) == before


def test_a_doua_curatare_nu_mai_are_ce_sa_goelasca() -> None:
    """Scriptul e idempotent: a doua rulare nu mai atinge nicio celulă."""
    notebook = notebook_with_outputs()
    nb_strip.strip_notebook(notebook)

    assert nb_strip.strip_notebook(notebook) == 0


def test_check_raporteaza_fara_sa_scrie(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--check` întoarce 1 când există output-uri și lasă fișierul așa cum era."""
    path = tmp_path / "explorare.ipynb"
    original = json.dumps(notebook_with_outputs(), indent=1) + "\n"
    path.write_text(original, encoding="utf-8")

    assert nb_strip.main(["--check", str(tmp_path)]) == 1

    assert path.read_text(encoding="utf-8") == original, "check nu are voie să scrie"
    assert "de golit" in capsys.readouterr().out


def test_check_intoarce_zero_pe_un_notebook_curat(tmp_path: Path) -> None:
    """Un notebook fără output-uri trece verificarea."""
    notebook = notebook_with_outputs()
    nb_strip.strip_notebook(notebook)
    (tmp_path / "curat.ipynb").write_text(json.dumps(notebook), encoding="utf-8")

    assert nb_strip.main(["--check", str(tmp_path)]) == 0


def test_curatarea_scrie_notebook_ul_in_apostrofuri_stabile(tmp_path: Path) -> None:
    """Fișierul scris redevine un notebook valid, cu sursa neatinsă."""
    path = tmp_path / "explorare.ipynb"
    path.write_text(json.dumps(notebook_with_outputs()), encoding="utf-8")

    assert nb_strip.main([str(tmp_path)]) == 0

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["cells"][1]["outputs"] == []
    assert written["cells"][2]["source"] == ["import cncdt_ml"]
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_punctele_de_control_ale_jupyter_sunt_sarite(tmp_path: Path) -> None:
    """Copiile automate din `.ipynb_checkpoints` nu se numără ca notebook-uri."""
    checkpoint = tmp_path / ".ipynb_checkpoints"
    checkpoint.mkdir()
    (checkpoint / "explorare-checkpoint.ipynb").write_text("{}", encoding="utf-8")
    (tmp_path / "explorare.ipynb").write_text("{}", encoding="utf-8")

    found = nb_strip.notebooks_under([tmp_path])

    assert [p.name for p in found] == ["explorare.ipynb"]


def test_un_fisier_care_nu_e_json_este_raportat(tmp_path: Path) -> None:
    """Un `.ipynb` stricat nu trece tăcut: scriptul iese cu 2."""
    (tmp_path / "stricat.ipynb").write_text("{nu e json", encoding="utf-8")

    assert nb_strip.main([str(tmp_path)]) == 2


def test_notebook_urile_comise_nu_au_output_uri() -> None:
    """Poarta de dinainte de commit: ce a ajuns în `ml/experiments/notebooks/` e deja curățat."""
    murdare = []
    for path in nb_strip.notebooks_under([ROOT / "ml" / "experiments" / "notebooks"]):
        notebook = json.loads(path.read_text(encoding="utf-8"))
        if nb_strip.strip_notebook(notebook):
            murdare.append(str(path.relative_to(ROOT)))

    assert not murdare, f"rulează `make nb-strip` înainte de commit: {', '.join(murdare)}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
