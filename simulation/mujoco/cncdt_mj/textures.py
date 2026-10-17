"""Texturi procedurale pentru scenă și pentru randarea pieselor.

Texturile se generează local, din numpy, ca să nu intre fișiere binare în repo și ca fiecare
rulare să pornească de la aceleași imagini. Sunt puține și simple — un metal periat, un beton
închis, o cauciucă de bandă —, destul cît să nu mai arate scena ca un desen cu materiale plate.

Modulul este **pur**: scrie PNG cu `zlib`, fără `mujoco` (ADR 0012), deci se testează în
`.venv`-ul proiectului.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

#: Rezoluția implicită a texturilor. Sunt mici fiindcă se repetă pe suprafețe și fiindcă
#: randarea offscreen nu are nevoie de mai mult.
DEFAULT_SIZE = 256

#: Numele texturilor pe care le scrie :func:`ensure_textures`.
TEXTURE_NAMES = ("brushed_metal", "concrete", "belt_rubber")


def write_png(path: str | Path, image: np.ndarray) -> Path:
    """Scrie o imagine RGB (uint8) ca PNG, fără dependențe externe."""
    target = Path(path)
    array = np.asarray(image)
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("imaginea trebuie să fie uint8, de forma (înălțime, lățime, 3)")
    height, width, _ = array.shape
    raw = b"".join(b"\x00" + array[row].tobytes() for row in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                       + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    return target


def _to_rgb(mono: np.ndarray, tint: tuple[float, float, float]) -> np.ndarray:
    """O imagine monocromă în [0, 1], colorată cu un ton și codificată pe 8 biți."""
    tinted = np.clip(mono[..., None] * np.asarray(tint, dtype=float)[None, None, :], 0.0, 1.0)
    return (tinted * 255.0).round().astype(np.uint8)


def _periodic_noise(rng: np.random.Generator, size: int, cycles: int, octaves: int = 3) -> np.ndarray:
    """Zgomot neted, periodic pe ambele axe, ca textura să se repete fără cusătură."""
    y, x = np.mgrid[0:size, 0:size] / float(size)
    field = np.zeros((size, size), dtype=float)
    amplitude = 1.0
    total = 0.0
    for octave in range(octaves):
        frequency = cycles * (2**octave)
        phase_x = rng.uniform(0.0, 2 * np.pi)
        phase_y = rng.uniform(0.0, 2 * np.pi)
        field += amplitude * (np.sin(2 * np.pi * frequency * x + phase_x)
                              * np.cos(2 * np.pi * frequency * y + phase_y))
        total += amplitude
        amplitude *= 0.55
    return field / total


def brushed_metal(size: int = DEFAULT_SIZE, seed: int = 7) -> np.ndarray:
    """Metal periat: dungi fine orizontale, cu variație lentă a luciului."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size]
    # Dungi dese, plus o modulație pe verticală care imită trecerea periei.
    streaks = rng.normal(0.0, 1.0, (size, 1))
    streaks = 0.55 * streaks + 0.45 * np.roll(streaks, 1, axis=0)
    brush = np.sin(2 * np.pi * (y / size) * 42.0 + streaks) * 0.012
    broad = _periodic_noise(rng, size, cycles=1, octaves=2) * 0.028
    fine = rng.normal(0.0, 0.004, (size, size))
    mono = 0.74 + brush + broad + fine
    return _to_rgb(np.clip(mono, 0.0, 1.0), (0.90, 0.92, 0.95))


def concrete(size: int = DEFAULT_SIZE, seed: int = 11) -> np.ndarray:
    """Beton închis: pete mari, granulație fină și cîteva puncte mai deschise.

    Zgomotul are puține cicluri pe textură: unul cu multe cicluri, repetat pe podea, se vede ca
    un caroiaj regulat, nu ca beton.
    """
    rng = np.random.default_rng(seed)
    broad = _periodic_noise(rng, size, cycles=1, octaves=3) * 0.09
    medium = _periodic_noise(rng, size, cycles=3, octaves=1) * 0.025
    fine = rng.normal(0.0, 0.010, (size, size))
    mono = 0.30 + broad + medium + fine
    # Cîteva pietre mici, ca suprafața să nu fie uniformă.
    for _ in range(60):
        row = int(rng.integers(0, size))
        column = int(rng.integers(0, size))
        radius = int(rng.integers(1, 3))
        mono[max(0, row - radius):row + radius + 1,
             max(0, column - radius):column + radius + 1] += rng.uniform(0.02, 0.05)
    return _to_rgb(np.clip(mono, 0.0, 1.0), (0.92, 0.93, 0.95))


def belt_rubber(size: int = DEFAULT_SIZE, seed: int = 17) -> np.ndarray:
    """Cauciuc de bandă: șiruri fine, în lungul benzii, și granulație închisă."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size]
    ribs = np.sin(2 * np.pi * (y / size) * 16.0) * 0.035
    broad = _periodic_noise(rng, size, cycles=3, octaves=2) * 0.025
    fine = rng.normal(0.0, 0.012, (size, size))
    mono = 0.19 + ribs + broad + fine + 0.02 * np.cos(2 * np.pi * x / size)
    return _to_rgb(np.clip(mono, 0.0, 1.0), (0.82, 0.84, 0.88))


#: Funcțiile care produc fiecare textură, în ordinea din :data:`TEXTURE_NAMES`.
GENERATORS = {
    "brushed_metal": brushed_metal,
    "concrete": concrete,
    "belt_rubber": belt_rubber,
}


def ensure_textures(directory: str | Path, size: int = DEFAULT_SIZE) -> dict[str, Path]:
    """Scrie texturile lipsă într-un director și întoarce căile lor, pe nume.

    Fișierele existente nu se rescriu: randarea aceluiași cadru de două ori nu pierde timp
    regenerînd aceleași imagini.
    """
    root = Path(directory)
    paths = {name: root / f"{name}.png" for name in TEXTURE_NAMES}
    root.mkdir(parents=True, exist_ok=True)
    for name, path in paths.items():
        if not path.is_file():
            write_png(path, GENERATORS[name](size=size, seed=7 + 10 * TEXTURE_NAMES.index(name)))
    return paths
