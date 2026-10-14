"""Adaptorul USD: scrie o variantă generată într-un prim ``UsdGeom.Mesh``.

Importul lui ``pxr`` se face lazy, ca modulul să poată fi importat (și testat) fără USD
instalat. În Isaac Sim, ``pxr`` vine cu aplicația.

Meshul se rescrie integral la fiecare variantă: un mesh de câteva mii de fețe se construiește
în milisecunde, deci nu are rost să păstrăm o topologie și să o edităm parțial.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from cncdt.cnc_cell.parts.gear import PartVariant

# Numele primului pe care extensia îl folosește ca piesă, sub rădăcina celulei.
DEFAULT_PART_PATH = "/World/Part/Mesh"


def _usd_modules() -> tuple[Any, Any, Any, Any, Any]:
    try:
        from pxr import Gf, Sdf, Usd, UsdGeom, Vt  # type: ignore
    except ImportError as exc:  # pragma: no cover - depinde de Isaac Sim
        raise RuntimeError(
            "pxr (USD) lipsește; adaptorul rulează doar în Isaac Sim sau cu usd-core instalat."
        ) from exc
    return Gf, Sdf, Usd, UsdGeom, Vt


def extent_of(vertices: np.ndarray) -> tuple[list[float], list[float]]:
    """Cutia de încadrare, în formatul cerut de ``UsdGeom``."""
    low = vertices.min(axis=0).astype(float)
    high = vertices.max(axis=0).astype(float)
    return [float(value) for value in low], [float(value) for value in high]


def mesh_arrays(variant: PartVariant) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pregătește tablourile în tipurile pe care le acceptă ``Vt``."""
    points = np.ascontiguousarray(variant.vertices, dtype=np.float32)
    indices = np.ascontiguousarray(variant.faces.reshape(-1), dtype=np.int32)
    counts = np.full(len(variant.faces), 3, dtype=np.int32)
    return points, indices, counts


def write_variant(
    stage: Any,
    variant: PartVariant,
    prim_path: str = DEFAULT_PART_PATH,
    material_path: str | None = None,
) -> Any:
    """Scrie varianta în scenă, la ``prim_path``. Întoarce primul mesh."""
    _gf, _sdf, _usd, usd_geom, vt = _usd_modules()

    mesh = usd_geom.Mesh.Define(stage, prim_path)
    points, indices, counts = mesh_arrays(variant)

    mesh.CreatePointsAttr(vt.Vec3fArray.FromNumpy(points))
    mesh.CreateFaceVertexIndicesAttr(vt.IntArray.FromNumpy(indices))
    mesh.CreateFaceVertexCountsAttr(vt.IntArray.FromNumpy(counts))
    mesh.CreateSubdivisionSchemeAttr(usd_geom.Tokens.none)
    low, high = extent_of(variant.vertices)
    mesh.CreateExtentAttr(vt.Vec3fArray.FromNumpy(np.array([low, high], dtype=np.float32)))

    if material_path:
        from pxr import UsdShade  # type: ignore

        material = UsdShade.Material(stage.GetPrimAtPath(material_path))
        if material.GetPrim().IsValid():
            UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    return mesh


def write_variant_to_file(
    variant: PartVariant,
    usd_path: str,
    prim_path: str = DEFAULT_PART_PATH,
) -> str:
    """Scrie varianta într-un fișier USD nou. Folosit la generarea în lot."""
    _gf, _sdf, usd, _usd_geom, _vt = _usd_modules()
    stage = usd.Stage.CreateNew(usd_path)
    try:
        write_variant(stage, variant, prim_path=prim_path)
        stage.GetRootLayer().Save()
    finally:
        stage = None
    return usd_path
