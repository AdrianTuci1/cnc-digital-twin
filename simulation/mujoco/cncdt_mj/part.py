"""Piesele din scenă: blocul brut de pe banda 1 și roata dințată ieșită din CNC.

Fluxul real al celulei are două stări ale materialului, iar scena le arată pe amândouă:

1. **blocul brut** intră pe banda 1 — o formă simplă, parametrizabilă (cilindru ori cutie);
2. **piesa mașinată** iese din CNC — roata dințată produsă de generatorul procedural existent,
   `simulation/extensions/cncdt.cnc_cell/cncdt/cnc_cell/parts/gear.py`, folosit ca atare, nu
   duplicat. Transformarea dintre cele două stări se petrece în mașină și nu se modelează aici.

Geometria generatorului iese în milimetri, în planul ``XY``, cu grosimea pe ``Z``: ``outer_radius
= 50,0``, ``thickness = 15,0``. Se scalează cu :data:`MESH_SCALE` înainte de a intra în MuJoCo,
care lucrează în metri, iar piesa așezată pe bandă are deci fața în sus, fără înclinare — vezi
:data:`PART_TILT_QUAT`. Singura rotație a piesei de pe bandă este yaw-ul din jurul lui ``Z``,
care este un parametru (fluxul cere rotație aleatoare), nu o constantă.

Modulul nu importă MuJoCo (ADR 0012): se testează în `.venv`-ul proiectului.
"""

from __future__ import annotations

import math
import random
import sys
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from . import REPO_ROOT

EXTENSION_DIR = REPO_ROOT / "simulation" / "extensions" / "cncdt.cnc_cell"
if str(EXTENSION_DIR) not in sys.path:
    sys.path.insert(0, str(EXTENSION_DIR))

from cncdt.cnc_cell.parts.gear import GearParams, PartVariant, build_variant  # noqa: E402

#: Sămânța implicită a piesei din scenă: aceeași sămânță dă mereu aceeași roată.
DEFAULT_SEED = 7

#: Geometria generatorului este în milimetri; MuJoCo lucrează în metri.
MESH_SCALE = 0.001

#: Piesa nominală a generatorului, folosită doar ca referință de dimensionare a blocului brut.
NOMINAL_GEAR = GearParams()

#: Blocul brut: un disc coerent cu roata generată, cu adaos de prelucrare. Semifabricatul are
#: 52 mm rază și 18 mm grosime, adică exact cât se ia în CNC din roata de 50 mm x 15 mm.
BLANK_RADIUS_MM = 52.0
BLANK_THICKNESS_MM = 18.0

#: Adaosul de prelucrare, derivat din cele două dimensiuni de mai sus.
ALLOWANCE_RADIUS_MM = BLANK_RADIUS_MM - NOMINAL_GEAR.outer_radius
ALLOWANCE_THICKNESS_MM = BLANK_THICKNESS_MM - NOMINAL_GEAR.thickness

#: Împrăștierea poziției blocului pe bandă și urma de siguranță până la marginea benzii.
BLANK_JITTER_M = 0.03
BLANK_EDGE_MARGIN_M = 0.005

#: Formele acceptate pentru blocul brut. Cilindrul e varianta implicită: un semifabricat pentru
#: o roată dințată se debitează rotund. Cutia rămâne disponibilă ca alternativă parametrizabilă.
BLANK_SHAPES = ("cylinder", "box")

#: Piesa culcată pe bandă nu se înclină. Meshul are deja profilul în planul ``XY`` și grosimea
#: pe ``Z``, deci „culcat" este identitatea; constanta există ca schimbarea de orientare să fie
#: o singură linie, dacă fluxul cere vreodată piesa pe cant.
PART_TILT_QUAT = (1.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class BlankStock:
    """Blocul brut: amprentă rotundă ori pătrată, aceeași înălțime."""

    shape: str
    radius_m: float
    half_height_m: float

    def __post_init__(self) -> None:
        if self.shape not in BLANK_SHAPES:
            raise ValueError(f"formă necunoscută pentru blocul brut: {self.shape}")
        if self.radius_m <= 0 or self.half_height_m <= 0:
            raise ValueError("blocul brut are dimensiuni pozitive")

    @property
    def diameter_mm(self) -> float:
        """Diametrul blocului, în milimetri, pentru comparație cu piesa nominală."""
        return 2 * self.radius_m / MESH_SCALE

    @property
    def height_mm(self) -> float:
        return 2 * self.half_height_m / MESH_SCALE

    def rest_z_m(self, surface_z: float) -> float:
        """Cota centrului blocului, culcat pe suprafața unei benzi."""
        return surface_z + self.half_height_m

    def as_dict(self) -> dict[str, Any]:
        return {
            "shape": self.shape,
            "radius_m": self.radius_m,
            "half_height_m": self.half_height_m,
        }


def load_variant(seed: int = DEFAULT_SEED, defect: str | None = None) -> PartVariant:
    """Piesa mașinată, exact cum o dă generatorul procedural existent."""
    return build_variant(seed, defect)


def scaled_geometry(variant: PartVariant) -> tuple[np.ndarray, np.ndarray]:
    """Verticșii piesei, în metri, și fețele ei, neschimbate.

    Se copiază datele, ca scena să nu poată modifica varianta din care provin.
    """
    return (np.asarray(variant.vertices, dtype=np.float64) * MESH_SCALE,
            np.asarray(variant.faces, dtype=np.int64))


def rest_z_m(variant: PartVariant, surface_z: float) -> float:
    """Cota centrului piesei culcate pe o suprafață: grosimea ei se măsoară din mesh."""
    vertices, _ = scaled_geometry(variant)
    return float(surface_z + vertices[:, 2].max())


def blank_stock(shape: str = "cylinder", radius_mm: float = BLANK_RADIUS_MM,
                thickness_mm: float = BLANK_THICKNESS_MM) -> BlankStock:
    """Blocul brut: discul (ori cutia) care intră în CNC, cu adaos de prelucrare."""
    return BlankStock(shape=shape, radius_m=radius_mm * MESH_SCALE,
                      half_height_m=thickness_mm * MESH_SCALE / 2)


def random_xy(seed: int, nominal_xy: tuple[float, float],
              bounds: tuple[float, float, float, float], radius_m: float,
              jitter_m: float = BLANK_JITTER_M, stream: str = "blank") -> tuple[float, float]:
    """Poziția aleatoare a unei piese pe bandă, deterministă din sămânță.

    Piesele reale nu sosesc așezate la milimetru, iar fluxul cere explicit poziție aleatoare atât
    pentru bloc, cât și pentru roata așezată de brațul 1. Împrăștierea este mică, fiindcă ținta de
    prindere trebuie să rămână în raza brațului, iar rezultatul este limitat la amprenta benzii
    (``bounds``, deja strânsă cu raza piesei) ca piesa să stea întreagă pe bandă.
    """
    rng = random.Random(f"{seed}:{stream}:xy")
    x0, x1, y0, y1 = bounds
    x = nominal_xy[0] + rng.uniform(-jitter_m, jitter_m)
    y = nominal_xy[1] + rng.uniform(-jitter_m, jitter_m)
    limit = radius_m + BLANK_EDGE_MARGIN_M
    return (min(max(x, x0 + limit), x1 - limit), min(max(y, y0 + limit), y1 - limit))


def random_yaw_deg(seed: int, stream: str = "part") -> float:
    """Yaw-ul aleator al unei piese, determinist din sămânță și din rolul ei.

    Fluxul așază piesa pe banda 2 la o rotație aleatoare; yaw-ul este eticheta care va însoți
    cadrul de inspecție, deci se citește înapoi din scenă, nu se presupune. Blocului brut i se
    trage un yaw din alt flux, ca cele două piese să nu împartă aceeași orientare doar fiindcă
    împart sămânța. Pentru un disc, yaw-ul nu se vede, dar se păstrează: este parametrul pe care
    îl va folosi piesa mașinată.
    """
    return random.Random(f"{seed}:{stream}:yaw").uniform(0.0, 360.0)


def _quat_mul(left: Any, right: Any) -> tuple[float, float, float, float]:
    """Produsul a două cuaternare ``(w, x, y, z)``, în ordinea MuJoCo."""
    lw, lx, ly, lz = (float(value) for value in left)
    rw, rx, ry, rz = (float(value) for value in right)
    return (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )


def yaw_quat(yaw_deg: float) -> tuple[float, float, float, float]:
    """Orientarea piesei culcate, rotită cu ``yaw_deg`` în jurul lui ``Z``.

    Ordinea este cea a MuJoCo, ``(w, x, y, z)``: întâi înclinarea piesei, apoi yaw-ul.
    """
    half = math.radians(yaw_deg) / 2
    yaw = (math.cos(half), 0.0, 0.0, math.sin(half))
    return _quat_mul(PART_TILT_QUAT, yaw)


def yaw_from_quat(quat: Any) -> float:
    """Yaw-ul, în grade, al unei rotații ``(w, x, y, z)`` care nu înclină piesa."""
    w, x, y, z = (float(value) for value in quat)
    return math.degrees(math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z)))


def describe(variant: PartVariant) -> Mapping[str, Any]:
    """Eticheta piesei, în forma în care intră în datasetul de inspecție."""
    return {
        "part_id": variant.part_id,
        "seed": variant.seed,
        "defect": variant.defect,
        "verdict": variant.verdict,
    }
