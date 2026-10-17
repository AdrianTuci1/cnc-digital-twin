"""Randarea offscreen a unei piese, pentru datasetul de calitate.

Modulul atinge motorul: compune o scenă minimă (plan, piesă, două lumini), așază camera liberă
deasupra piesei și scrie un PNG. Partea pură — unghiuri, distanțe, nume de fișiere, index — stă în
`cncdt_mj/part_images.py`, ca să se poată testa fără `mujoco` (ADR 0012).

Piesa se scrie într-un OBJ temporar, fiindcă MuJoCo încarcă meshurile din fișier. Geometria vine
din generatorul procedural, deci nu se copiază nimic în repo: se reconstruiește din manifestul de
etichete.

Pe Linux fără afișaj (containerele Modal), randatorul are nevoie de `MUJOCO_GL=egl`; pe macOS
folosește CGL. Ambele căi sînt offscreen, deci nu deschid fereastră.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import mujoco
import numpy as np

from . import camera, part_images

#: Cât de sus stă planul de sub piesă, ca să nu se intersecteze cu fața de jos.
FLOOR_GAP_M = 0.0005


def write_obj(path: Path, vertices_mm: np.ndarray, faces: np.ndarray) -> Path:
    """Scrie meshul variantei într-un OBJ, în metri, cu fețe de la 1.

    Generatorul dă vîrfurile în milimetri și fețele cu indici de la 0; MuJoCo citește OBJ cu
    indici de la 1. Conversia se face o singură dată, aici.
    """
    vertices = np.asarray(vertices_mm, dtype=np.float64) / 1000.0
    faces = np.asarray(faces, dtype=np.int64)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for x, y, z in vertices:
            handle.write(f"v {x:.9f} {y:.9f} {z:.9f}\n")
        for a, b, c in faces + 1:
            handle.write(f"f {a} {b} {c}\n")
    return path


def light_direction(azimuth_deg: float, elevation_deg: float) -> tuple[float, float, float]:
    """Direcția în care bate lumina: dinspre unghiul dat spre origine."""
    azimuth = math.radians(float(azimuth_deg))
    elevation = math.radians(float(elevation_deg))
    horizontal = math.cos(elevation)
    return (-horizontal * math.cos(azimuth),
            -horizontal * math.sin(azimuth),
            -math.sin(elevation))


def scene_xml(mesh_path: Path, part_z_m: float, floor_z_m: float, view: part_images.View,
              width: int, height: int) -> str:
    """Scena minimă de randare: plan, piesă, două lumini și camera liberă.

    Fundalul este planul însuși, văzut de sus: un gri neutru, fără textură și fără skybox, ca
    clasificatorul să nu învețe decorul. Lumina principală se rotește cu fiecare cadru, iar cea de
    umplere vine drept de sus, ca golurile să nu rămînă negre.
    """
    dx, dy, dz = light_direction(view.light_azimuth_deg, view.light_elevation_deg)
    distance = max(0.2, 2.0 * abs(part_z_m) + 0.2)
    lx, ly, lz = -dx * distance, -dy * distance, part_z_m - dz * distance
    return f"""<mujoco model="cncdt-part">
  <option gravity="0 0 0"/>
  <visual>
    <global offwidth="{int(width)}" offheight="{int(height)}" fovy="{part_images.FOVY_DEG}"/>
    <headlight ambient="0.32 0.32 0.32" diffuse="0.45 0.45 0.45" specular="0.12 0.12 0.12"/>
  </visual>
  <asset>
    <material name="part" rgba="0.82 0.84 0.87 1" specular="0.25" shininess="0.45"/>
    <material name="floor" rgba="0.16 0.17 0.19 1" specular="0.0" shininess="0.0"/>
    <mesh name="part" file="{mesh_path.as_posix()}"/>
  </asset>
  <worldbody>
    <geom name="floor" type="plane" size="4 4 0.01" pos="0 0 {floor_z_m:.9f}" material="floor"/>
    <body name="part" pos="0 0 {part_z_m:.9f}">
      <freejoint/>
      <geom name="part" type="mesh" mesh="part" material="part"/>
    </body>
    <light name="key" pos="{lx:.6f} {ly:.6f} {lz:.6f}" dir="{dx:.6f} {dy:.6f} {dz:.6f}"
           directional="true" diffuse="0.85 0.85 0.85" specular="0.2 0.2 0.2"/>
    <light name="fill" pos="0 0 1" dir="0 0 -1" directional="true" diffuse="0.22 0.22 0.22"/>
  </worldbody>
</mujoco>
"""


def render_variant(variant: Any, path: str | Path, view: part_images.View,
                   width: int = part_images.DEFAULT_SIZE,
                   height: int = part_images.DEFAULT_SIZE,
                   overwrite: bool = False) -> Path:
    """Randează o variantă dintr-un unghi și scrie PNG-ul.

    ``variant`` este obiectul generatorului (`PartVariant`): are ``vertices`` în milimetri,
    ``faces`` și ``params.outer_radius``. Nu se importă tipul aici, ca modulul să rămînă folosibil
    și pentru o variantă reconstruită din manifest.
    """
    target = Path(path)
    if target.exists() and not overwrite:
        return target

    vertices = np.asarray(variant.vertices, dtype=np.float64)
    faces = np.asarray(variant.faces, dtype=np.int64)
    low_z = float(vertices[:, 2].min()) / 1000.0
    high_z = float(vertices[:, 2].max()) / 1000.0
    center_z = 0.5 * (low_z + high_z)

    with tempfile.TemporaryDirectory(prefix="cncdt-part-") as scratch:
        mesh_path = write_obj(Path(scratch) / "part.obj", vertices, faces)
        xml = scene_xml(mesh_path, center_z, low_z - FLOOR_GAP_M, view, width, height)
        model = mujoco.MjModel.from_xml_string(xml)
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)

        free_camera = mujoco.MjvCamera()
        mujoco.mjv_defaultFreeCamera(model, free_camera)
        free_camera.lookat[:] = [0.0, 0.0, center_z]
        free_camera.distance = part_images.camera_distance_m(
            float(variant.params.outer_radius))
        free_camera.azimuth = float(view.azimuth_deg)
        free_camera.elevation = float(view.elevation_deg)

        with mujoco.Renderer(model, height=int(height), width=int(width)) as renderer:
            renderer.update_scene(data, camera=free_camera)
            image = renderer.render()
        camera.write_png(target, image)
    return target


def render_records(records: Sequence[Any], out_dir: str | Path,
                   builder: Callable[[Any], Any],
                   views_per_record: int = part_images.DEFAULT_VIEWS,
                   render_seed: int = 0,
                   width: int = part_images.DEFAULT_SIZE,
                   height: int = part_images.DEFAULT_SIZE,
                   overwrite: bool = False,
                   start: int = 0,
                   count: int | None = None,
                   splits: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Randează un interval din manifest și întoarce rîndurile de index.

    ``builder`` reconstruiește varianta din înregistrarea de manifest; scriptul îl leagă de
    generatorul procedural, iar testele pot da o variantă simplă. ``start`` și ``count`` sînt
    folosite la împărțirea pe containere: fiecare proces randează o felie contiguă.
    """
    root = Path(out_dir)
    upper = len(records) if count is None else min(len(records), start + count)
    split_of = splits or {}
    rows: list[dict[str, Any]] = []
    for ordinal in range(start, upper):
        record = records[ordinal]
        variant = builder(record)
        for view in part_images.plan_views(ordinal, views_per_record, render_seed):
            relative = part_images.image_relpath(record, view)
            render_variant(variant, root / relative, view, width, height, overwrite)
            rows.append(part_images.index_row(
                record, view, relative, part_images.split_of(record, split_of),
                width=width, height=height))
    return rows
