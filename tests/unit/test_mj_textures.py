"""Teste pentru texturile procedurale ale geamănului: PNG valid, determinist, stabil pe disc.

Texturile nu ating motorul (numpy + zlib), deci se testează din `.venv`-ul proiectului, ca orice
modul pur al geamănului (ADR 0012). Ce contează aici este că fișierul scris este un PNG citibil,
că aceeași sămânță dă aceeași imagine și că `ensure_textures` nu rescrie ce există deja: scena le
încarcă la fiecare compilare, iar o textură care se schimbă între rulări ar face cadrele
nereproductibile.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from cncdt_mj import textures


def _read_png(path: Path) -> tuple[int, int, bytes]:
    """Dimensiunile din IHDR și începutul datelor, fără o bibliotecă de imagini."""
    payload = path.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", payload[16:24])
    return width, height, payload


@pytest.mark.parametrize("name", textures.TEXTURE_NAMES)
def test_fiecare_textura_este_un_png_cu_contrast(name: str) -> None:
    image = textures.GENERATORS[name](size=64)
    assert image.shape == (64, 64, 3)
    assert image.dtype == np.uint8
    # O textură uniformă nu are ce căuta: materialul ar arăta tot plat.
    assert image.std() > 1.5
    assert 20 < image.mean() < 235


@pytest.mark.parametrize("name", textures.TEXTURE_NAMES)
def test_textura_este_determinista(name: str) -> None:
    first = textures.GENERATORS[name](size=48, seed=5)
    second = textures.GENERATORS[name](size=48, seed=5)
    other = textures.GENERATORS[name](size=48, seed=6)
    assert np.array_equal(first, second)
    assert not np.array_equal(first, other)


def test_ensure_textures_scrie_fiecare_fisier_o_singura_data(tmp_path: Path) -> None:
    """A doua chemare nu rescrie: randarea aceluiași cadru rămîne identică."""
    paths = textures.ensure_textures(tmp_path, size=32)
    assert set(paths) == set(textures.TEXTURE_NAMES)
    for path in paths.values():
        width, height, _ = _read_png(path)
        assert (width, height) == (32, 32)

    marker = b"nu se rescrie"
    first = paths["concrete"]
    first.write_bytes(marker)
    textures.ensure_textures(tmp_path, size=32)
    assert first.read_bytes() == marker
