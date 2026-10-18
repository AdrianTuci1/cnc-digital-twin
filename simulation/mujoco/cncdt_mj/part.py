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
from functools import lru_cache
from typing import Any, Mapping

import numpy as np

from . import REPO_ROOT

EXTENSION_DIR = REPO_ROOT / "simulation" / "extensions" / "cncdt.cnc_cell"
if str(EXTENSION_DIR) not in sys.path:
    sys.path.insert(0, str(EXTENSION_DIR))

from cncdt.cnc_cell.parts.gear import (  # noqa: E402
    FAMILIES,
    GearParams,
    PartVariant,
    build_variant,
    family_of,
)

#: Sămânța implicită a piesei din scenă: aceeași sămânță dă mereu aceeași roată.
DEFAULT_SEED = 7

#: Geometria generatorului este în milimetri; MuJoCo lucrează în metri.
MESH_SCALE = 0.001

#: Piesa de referință a generatorului, folosită ca reper de dimensionare a blocului brut.
NOMINAL_GEAR = GearParams()

#: Adaosul de prelucrare: cât este semifabricatul mai mare decât piesa care iese din el, pe rază
#: și pe grosime. Se aplică familiei alese, nu unei singure piese: blocul brut al fiecărei
#: familii are diametrul și grosimea piesei ei, plus adaosul de aici.
ALLOWANCE_RADIUS_MM = 2.0
ALLOWANCE_THICKNESS_MM = 3.0

#: Blocul brut al piesei de referință: 52 mm rază și 18 mm grosime.
BLANK_RADIUS_MM = NOMINAL_GEAR.outer_radius + ALLOWANCE_RADIUS_MM
BLANK_THICKNESS_MM = NOMINAL_GEAR.thickness + ALLOWANCE_THICKNESS_MM

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


def family(name: str | None = None):
    """Familia de piese cu care lucrează scena. Fără nume, familia de referință."""
    return family_of(name)


def family_names() -> tuple[str, ...]:
    """Numele familiilor, în ordinea din generator — aceeași cu ordinea compartimentelor."""
    return tuple(item.name for item in FAMILIES)


#: Sub această variație a lățimii, piesa se citește ca rotundă: dinții unei roți schimbă lățimea
#: cu sub 3 %, deci nu are sens să rotești scula după un dinte.
ROUND_WIDTH_VARIATION = 0.03


@lru_cache(maxsize=None)
def narrow_axes_deg(family_name: str | None = None) -> tuple[float, ...]:
    """Direcțiile, în planul piesei, pe care lățimea este minimă.

    Sînt axele pe care falcile închid pe fețele cele mai apropiate: la hexagon, normala la o
    muchie (la 0°, 60° și 120°), la triunghi, normala la o latură, iar la placă și legătură,
    axa scurtă. Roțile sînt rotunde, deci întorc lista goală: orice direcție e la fel de bună.

    Unghiurile se obțin din meshul nominal, cu o căutare de 0,25°; sînt în sistemul piesei, iar
    la prindere se adaugă yaw-ul citit din scenă. Funcția este pură și se memorează: familiile
    sînt fixe, iar căutarea nu trebuie repetată la fiecare ciclu.
    """
    variant = build_variant(DEFAULT_SEED, "PASS", family_name)
    points = np.asarray(variant.vertices, dtype=float)[:, :2]
    angles = np.arange(0.0, 180.0, 0.25)
    directions = np.column_stack((np.cos(np.radians(angles)), np.sin(np.radians(angles))))
    projections = points @ directions.T
    widths = projections.max(axis=0) - projections.min(axis=0)
    minimum = float(widths.min())
    if (float(widths.max()) - minimum) / max(minimum, 1e-9) < ROUND_WIDTH_VARIATION:
        return ()
    previous = np.r_[widths[-1], widths[:-1]]
    following = np.r_[widths[1:], widths[0]]
    tolerance = minimum * 0.003 + 0.05
    candidates = angles[(widths <= previous) & (widths <= following)
                        & (widths <= minimum + tolerance)]
    clusters: list[list[float]] = []
    for angle in candidates:
        if clusters and abs(yaw_delta_deg(angle, clusters[-1][-1])) <= 2.0:
            clusters[-1].append(float(angle))
        else:
            clusters.append([float(angle)])
    if len(clusters) > 1 and abs(yaw_delta_deg(clusters[0][0], clusters[-1][-1])) <= 2.0:
        clusters[0] = clusters[-1] + clusters[0]
        clusters.pop()
    axes = []
    for cluster in clusters:
        radians = np.radians(cluster)
        angle = math.degrees(math.atan2(float(np.sin(radians).mean()),
                                        float(np.cos(radians).mean()))) % 180.0
        axes.append(0.0 if angle > 179.5 else round(angle, 1))
    return tuple(sorted(set(axes)))


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


def load_variant(seed: int = DEFAULT_SEED, defect: str | None = None,
                 family_name: str | None = None) -> PartVariant:
    """Piesa mașinată, exact cum o dă generatorul procedural existent.

    ``family_name`` alege familia de piese: fără el, scena lucrează pe piesa de referință, ca
    rezultatele de dinainte de familii să se reproducă exact.
    """
    return build_variant(seed, defect, family_name)


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


def blank_radius_mm(family_name: str | None = None) -> float:
    """Raza semifabricatului familiei: raza piesei plus adaosul de prelucrare."""
    return family(family_name).nominal.outer_radius + ALLOWANCE_RADIUS_MM


def blank_thickness_mm(family_name: str | None = None) -> float:
    """Grosimea semifabricatului familiei: grosimea piesei plus adaosul de prelucrare."""
    return family(family_name).nominal.thickness + ALLOWANCE_THICKNESS_MM


def gear_half_thickness_m(family_name: str | None = None) -> float:
    """Jumătatea grosimii piesei mașinate, în metri."""
    return family(family_name).nominal.thickness * MESH_SCALE / 2


def gear_radius_m(family_name: str | None = None) -> float:
    """Raza piesei mașinate, în metri: de la centru până în vârful dinților.

    Este raza conturului ei din desen, deci și cea care se vede în cadru, spre deosebire de
    jumătatea grosimii, care este cota centrului când piesa stă culcată.
    """
    return family(family_name).nominal.outer_radius * MESH_SCALE


def blank_half_thickness_m(family_name: str | None = None) -> float:
    """Jumătatea grosimii semifabricatului, în metri."""
    return blank_thickness_mm(family_name) * MESH_SCALE / 2


def belt_part_radius_m(key: str, family_name: str | None = None) -> float:
    """Raza materialului care merge pe o bandă, în metri.

    Blocul brut intră pe banda 1, piesa mașinată iese pe banda 2; raza limitează așezarea la
    amprenta benzii, deci depinde de familia care circulă.
    """
    if key == "belt_in":
        return blank_radius_mm(family_name) * MESH_SCALE
    if key == "belt_out":
        return gear_radius_m(family_name)
    raise KeyError(f"bandă fără material cunoscut: {key}")


def blank_stock(shape: str = "cylinder", family_name: str | None = None) -> BlankStock:
    """Blocul brut al familiei: discul (ori cutia) care intră în CNC, cu adaos de prelucrare."""
    return BlankStock(shape=shape, radius_m=blank_radius_mm(family_name) * MESH_SCALE,
                      half_height_m=blank_thickness_mm(family_name) * MESH_SCALE / 2)


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


def on_surface_xy(x: float, y: float, bounds: tuple[float, float, float, float],
                  radius_m: float, surface: str = "suprafață") -> tuple[float, float]:
    """O poziție cerută explicit pe o suprafață, verificată: piesa trebuie să încapă întreagă.

    Se folosește cînd poziția de start vine de la cineva (de pildă o piesă lăsată mai în amonte,
    ca transportul să aibă ce arăta): o piesă așezată cu o parte în afara suprafeței cade la
    primul pas, iar căderea ar părea un defect de fizică.
    """
    x0, x1, y0, y1 = bounds
    limit = radius_m + BLANK_EDGE_MARGIN_M
    if not (x0 + limit <= x <= x1 - limit and y0 + limit <= y <= y1 - limit):
        raise ValueError(
            f"poziția ({x:.3f}, {y:.3f}) m lasă piesa de rază {radius_m:.3f} m peste capătul "
            f"{surface} {bounds}")
    return (float(x), float(y))


def on_belt_xy(x: float, y: float, bounds: tuple[float, float, float, float],
               radius_m: float) -> tuple[float, float]:
    """O poziție cerută explicit pe bandă; vezi :func:`on_surface_xy`."""
    return on_surface_xy(x, y, bounds, radius_m, surface="benzii")


def stopped_xy(seed: int, stop_x: float, nominal_y: float,
               bounds: tuple[float, float, float, float], radius_m: float,
               jitter_m: float = BLANK_JITTER_M, stream: str = "stopped") -> tuple[float, float]:
    """Poziția de așezare a unei piese la linia de oprire a benzii: cota din lungul ei e fixă.

    Linia este cea până la care banda mai împinge piesa (`belt.Belt.reached_stop`), iar acolo piesa
    cade în dreptul țintei de preluare. Cota din lungul benzii rămâne fixă fiindcă banda ar aduce
    oricum piesa la aceeași linie; împrăștierea se păstrează numai pe lat, plus rotația, care se
    trage separat (:func:`random_yaw_deg`). Poziția rămâne limitată la amprenta benzii, ca piesa să
    stea întreagă pe ea.
    """
    x0, x1, y0, y1 = bounds
    limit = radius_m + BLANK_EDGE_MARGIN_M
    if not (x0 + limit <= stop_x <= x1 - limit):
        raise ValueError(
            f"linia de oprire de la {stop_x:.3f} m lasă piesa peste capătul benzii {bounds}")
    rng = random.Random(f"{seed}:{stream}:y")
    y = nominal_y + rng.uniform(-jitter_m, jitter_m)
    return (stop_x, min(max(y, y0 + limit), y1 - limit))


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


def yaw_delta_deg(measured_deg: float, requested_deg: float) -> float:
    """Diferența dintre două yaw-uri, în grade, adusă în intervalul ``(-180, 180]``.

    Unghiurile se compară pe cerc: 348,5° citit și −11,5° cerut sunt aceeași orientare, iar
    scăderea lor naivă (360°) ar da o cădere falsă la verificarea scenei.
    """
    return (measured_deg - requested_deg + 180.0) % 360.0 - 180.0


def describe(variant: PartVariant) -> Mapping[str, Any]:
    """Eticheta piesei, în forma în care intră în datasetul de inspecție."""
    return {
        "part_id": variant.part_id,
        "seed": variant.seed,
        "family": variant.family,
        "defect": variant.defect,
        "verdict": variant.verdict,
    }
