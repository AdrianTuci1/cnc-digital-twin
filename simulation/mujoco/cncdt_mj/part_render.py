"""Randarea offscreen a unei piese, pentru datasetul de calitate.

Modulul atinge motorul: compune o scenă minimă (plan texturat, piesă cu metal periat, trei
lumini), așază camera liberă deasupra piesei și scrie un PNG. Partea pură — unghiuri, distanțe,
nume de fișiere, index — stă în `cncdt_mj/part_images.py`, ca să se poată testa fără `mujoco`
(ADR 0012).

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

from . import camera, part_images, textures

#: Cât de sus stă planul de sub piesă, ca să nu se intersecteze cu fața de jos.
FLOOR_GAP_M = 0.0005

#: Texturile generate o singură dată, în același loc ca ale celulei: XML-ul le încarcă la parsare,
#: deci drumul trebuie să fie stabil între apeluri.
TEXTURES_DIR = Path(__file__).resolve().parent.parent / "runs" / "textures"


def write_obj(path: Path, vertices_mm: np.ndarray, faces: np.ndarray) -> Path:
    """Scrie meshul variantei într-un OBJ, în metri, cu fețe de la 1 și coordonate de textură.

    Generatorul dă vîrfurile în milimetri și fețele cu indici de la 0; MuJoCo citește OBJ cu
    indici de la 1. Coordonatele de textură sînt o proiecție plană pe cutia vîrfurilor: destul ca
    striurile periate să curgă continuu peste fața piesei, fără să întindă textura pe dinți.
    Vîrfurile se repetă pentru fiecare triunghi: normala medie a unui vîrf împărtășit între fața
    de sus și flancul unui dinte ar înclina toată fața, iar discul ar ieși vălurit.
    """
    vertices = np.asarray(vertices_mm, dtype=np.float64) / 1000.0
    faces = np.asarray(faces, dtype=np.int64)
    low = vertices[:, :2].min(axis=0)
    span = np.maximum(vertices[:, :2].max(axis=0) - low, 1e-9)
    uv = (vertices[:, :2] - low) / span
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for ordinal, triangle in enumerate(faces):
            for index in triangle:
                x, y, z = vertices[index]
                u, v = uv[index]
                handle.write(f"v {x:.9f} {y:.9f} {z:.9f}\n")
                handle.write(f"vt {u:.6f} {v:.6f}\n")
            base = 3 * ordinal + 1
            handle.write(f"f {base}/{base} {base + 1}/{base + 1} {base + 2}/{base + 2}\n")
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
              width: int, height: int,
              texture_paths: Mapping[str, Path] | None = None) -> str:
    """Scena minimă de randare: plan, piesă, trei lumini și camera liberă.

    Planul primește o textură de beton, iar piesa una de metal periat: fără ele, clasificatorul
    vedea o suprafață perfect plană, care nu are reflexe la niciun unghi. Lumina principală se
    rotește cu fiecare cadru, cea de umplere vine drept de sus, iar o a treia, laterală, ridică
    marginea piesei — altfel un disc de metal se pierde în fundal.
    """
    dx, dy, dz = light_direction(view.light_azimuth_deg, view.light_elevation_deg)
    distance = max(0.2, 2.0 * abs(part_z_m) + 0.2)
    lx, ly, lz = -dx * distance, -dy * distance, part_z_m - dz * distance
    paths = dict(texture_paths or {})
    brushed = paths.get("brushed_metal")
    concrete = paths.get("concrete")
    part_texture = (f'<texture type="2d" name="part_tex" file="{brushed.as_posix()}"/>'
                    if brushed is not None else "")
    floor_texture = (f'<texture type="2d" name="floor_tex" file="{concrete.as_posix()}"/>'
                     if concrete is not None else "")
    part_map = 'texture="part_tex" texuniform="false" texrepeat="1 1"' if brushed else ""
    floor_map = 'texture="floor_tex" texuniform="false" texrepeat="12 12"' if concrete else ""
    return f"""<mujoco model="cncdt-part">
  <option gravity="0 0 0"/>
  <visual>
    <global offwidth="{int(width)}" offheight="{int(height)}" fovy="{part_images.FOVY_DEG}"/>
    <headlight ambient="0.26 0.26 0.26" diffuse="0.30 0.30 0.30" specular="0.08 0.08 0.08"/>
  </visual>
  <asset>
    {part_texture}
    {floor_texture}
    <material name="part" {part_map} rgba="0.70 0.72 0.76 1" specular="0.28" shininess="0.50"/>
    <material name="floor" {floor_map} rgba="0.20 0.21 0.23 1" specular="0.0" shininess="0.0"/>
    <mesh name="part" file="{mesh_path.as_posix()}" smoothnormal="false"/>
  </asset>
  <worldbody>
    <geom name="floor" type="plane" size="4 4 0.01" pos="0 0 {floor_z_m:.9f}" material="floor"/>
    <body name="part" pos="0 0 {part_z_m:.9f}">
      <freejoint/>
      <geom name="part" type="mesh" mesh="part" material="part"/>
    </body>
    <light name="key" pos="{lx:.6f} {ly:.6f} {lz:.6f}" dir="{dx:.6f} {dy:.6f} {dz:.6f}"
           directional="true" diffuse="0.62 0.62 0.62" specular="0.18 0.18 0.18"/>
    <light name="fill" pos="0 0 1" dir="0 0 -1" directional="true" diffuse="0.20 0.20 0.20"/>
    <light name="rim" pos="0 -0.6 0.4" dir="0 0.6 -0.4" directional="true"
           diffuse="0.26 0.26 0.28" specular="0.30 0.30 0.30"/>
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
        texture_paths = textures.ensure_textures(TEXTURES_DIR)
        xml = scene_xml(mesh_path, center_z, low_z - FLOOR_GAP_M, view, width, height,
                        texture_paths)
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
