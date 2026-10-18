"""Randarea offscreen a geamănului: scrierea PNG, cadrele știute și punctele de vedere.

Aici stă partea care atinge motorul pentru imagini: scrierea unui PNG fără nicio dependență
externă, așezarea camerei libere pe un cadru știut și ținerea minte a randatoarelor pe rezoluție.
Ce anume se fotografiază — cele patru puncte de vedere și momentele lor — este în
`cncdt_mj/povs.py`, care este pur.

Mutat din `view.py` când a apărut sesiunea de colectare (`run.py`), ca randarea să aibă un singur
loc: `view.py` randează în continuare cadrele de verificare, iar `run.py` scrie cadrele datasetului.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Mapping

import mujoco
import numpy as np

from . import povs

#: Cadrul general al celulei, din camera liberă: centrul celulei, de sus și dinspre sud-est.
FREE_CAMERA = {"lookat": (3.0, 2.0, 0.8), "distance": 8.5, "azimuth": 135.0, "elevation": -28.0}

#: Cadrul apropiat al brațelor, pentru cadrele din `--demo --shots`: celula citită ca în plan —
#: banda 1 sus, mașina în dreapta —, dar de aproape, ca să se vadă și falcile la piesa apucată.
DEMO_CAMERA = {"lookat": (2.85, 2.55, 0.9), "distance": 4.6, "azimuth": 290.0,
               "elevation": -24.0}

#: Cadrele camerei libere, pe puncte de vedere: punctul de vedere `cell` este cadrul general.
FREE_PRESETS: Mapping[str, Mapping[str, Any]] = {povs.pov("cell").name: FREE_CAMERA}


def write_png(path: Any, image: np.ndarray) -> None:
    """Scrie un PNG RGB fără nicio dependență externă (nici PIL, nici imageio nu sunt instalate)."""
    height, width, channels = image.shape
    if channels != 3:
        raise ValueError(f"imaginea are {channels} canale, nu 3")
    raw = b"".join(b"\x00" + image[row].tobytes() for row in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def configure_camera(camera: mujoco.MjvCamera, preset: Mapping[str, Any] = FREE_CAMERA) -> None:
    """Așază camera liberă pe unul din cadrele știute ale celulei.

    Se folosește și la randare, și la deschiderea viewer-ului, ca fereastra să pornească pe aceeași
    priveliște ca `renders/cell.png`, nu pe încadrarea implicită a MuJoCo.
    """
    camera.lookat[:] = preset["lookat"]
    camera.distance = preset["distance"]
    camera.azimuth = preset["azimuth"]
    camera.elevation = preset["elevation"]


def render(model: mujoco.MjModel, data: mujoco.MjData, path: Any, camera: str | None,
           width: int, height: int) -> None:
    """Randează un cadru offscreen și îl scrie ca PNG."""
    with mujoco.Renderer(model, height=height, width=width) as renderer:
        if camera is None:
            free = mujoco.MjvCamera()
            mujoco.mjv_defaultFreeCamera(model, free)
            configure_camera(free)
            renderer.update_scene(data, camera=free)
        else:
            renderer.update_scene(data, camera=camera)
        write_png(path, renderer.render())


@dataclass
class Frames:
    """Cadre scrise pe drum, cu randatoarele ținute minte pe rezoluție.

    Randatorul se deschide o dată pe rezoluție, nu la fiecare cadru: sesiunea de colectare scrie
    mii de cadre, iar deschiderea unui `mujoco.Renderer` pe fiecare ar domina timpul de generare.
    Fără cameră dată, cadrul iese din camera liberă, așezată pe presetul cerut.
    """

    model: mujoco.MjModel

    #: Rezoluția și cadrul camerei libere folosite când nu se cere altceva (`--demo --shots`).
    width: int = 1280
    height: int = 960
    preset: Mapping[str, Any] = field(default_factory=lambda: DEMO_CAMERA)

    def __post_init__(self) -> None:
        self._renderers: dict[tuple[int, int], mujoco.Renderer] = {}

    def renderer(self, width: int | None = None, height: int | None = None) -> mujoco.Renderer:
        """Randatorul pentru rezoluția dată, deschis la nevoie."""
        size = (width or self.width, height or self.height)
        if size not in self._renderers:
            self._renderers[size] = mujoco.Renderer(self.model, height=size[1], width=size[0])
        return self._renderers[size]

    def write(self, data: mujoco.MjData, path: Any, camera: str | None = None,
              width: int | None = None, height: int | None = None,
              preset: Mapping[str, Any] | None = None, shadows: bool = True) -> None:
        """Scrie un cadru, din camera dată ori din camera liberă.

        ``shadows=False`` scoate umbrele aruncate după ce scena a fost actualizată: steagul din
        ``MjvScene`` se resetează la fiecare ``update_scene``, deci nu se poate pune o singură dată.
        """
        renderer = self.renderer(width, height)
        if camera is None:
            free = mujoco.MjvCamera()
            mujoco.mjv_defaultFreeCamera(self.model, free)
            configure_camera(free, preset or self.preset)
            renderer.update_scene(data, camera=free)
        else:
            renderer.update_scene(data, camera=camera)
        renderer.scene.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = 1 if shadows else 0
        write_png(path, renderer.render())

    def write_pov(self, data: mujoco.MjData, pov: povs.Pov, path: Any) -> None:
        """Scrie cadrul unui punct de vedere, la rezoluția lui."""
        if pov.camera is None and pov.name not in FREE_PRESETS:
            raise ValueError(f"punctul de vedere {pov.name} nu are nici cameră, nici cadru liber")
        self.write(data, path, camera=pov.camera, width=pov.width, height=pov.height,
                   preset=FREE_PRESETS.get(pov.name), shadows=pov.shadows)

    def close(self) -> None:
        """Închide randatoarele."""
        for renderer in self._renderers.values():
            renderer.close()
        self._renderers.clear()
