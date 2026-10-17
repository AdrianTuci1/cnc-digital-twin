"""Generatorul procedural al pieselor plane din celulă.

Piesa de referință este o roată dințată plană: contur dințat, alezaj central cu pană de
cheie și un cerc de găuri de buloane. Generatorul face și **piese cu altă siluetă** — placă
dreptunghiulară, cornier triunghiular, piuliță hexagonală și legătură cu capete semicirculare
—, ca rularea să nu arate doar roți. Meshul este construit direct, din inele 2D, fără operații
booleene la runtime:

* conturul exterior (dințat ori poligonal), alezajul (cu sau fără pană) și fiecare gaură
  sunt câte un inel 2D;
* fețele de sus și de jos se triangulează cu `mapbox_earcut`, care acceptă inele interioare;
* pereții laterali se ridică între cele două fețe, folosind aceiași indici.

Fiecare defect este o schimbare de parametri, nu o operație pe geometrie. Astfel geometria
randată și eticheta provin din același obiect :class:`GearParams`, iar un dataset se poate
regenera exact din seed.

Piesele sunt grupate în **familii** (:data:`FAMILIES`): același proces și aceeași sculă, dar
siluetă, gabarit, alezaj și cerc de găuri diferite. Familia este prima etichetă a piesei —
modelul de inspecție trebuie să spună întâi *ce* piesă este, apoi dacă e conformă.

Modulul nu depinde de Isaac Sim: doar ``numpy`` și ``mapbox_earcut``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, fields, replace
from typing import Any, Mapping

import numpy as np

try:  # pragma: no cover - importul eșuează doar fără pachet.
    import mapbox_earcut as _earcut
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "mapbox_earcut lipsește. Instalați cu 'pip install mapbox-earcut' ori, în Isaac Sim, "
        "cu omni.kit.pipapi / './python.sh -m pip install mapbox-earcut'."
    ) from exc

VERDICT_PASS = "PASS"
VERDICT_REJECT = "REJECT"

DEFECT_PASS = "PASS"
DEFECT_TOOTH_MISSING = "TOOTH_MISSING"
DEFECT_TOOTH_CHIPPED = "TOOTH_CHIPPED"
DEFECT_TOOTH_COUNT_WRONG = "TOOTH_COUNT_WRONG"
DEFECT_BORE_OVERSIZE = "BORE_OVERSIZE"
DEFECT_BORE_UNDERSIZE = "BORE_UNDERSIZE"
DEFECT_BORE_MISSING = "BORE_MISSING"
DEFECT_KEYWAY_MISSING = "KEYWAY_MISSING"
DEFECT_HOLE_MISSING = "HOLE_MISSING"
DEFECT_HOLE_OFF_CIRCLE = "HOLE_OFF_CIRCLE"
DEFECT_BOLT_PATTERN_ROTATED = "BOLT_PATTERN_ROTATED"

DEFECT_CLASSES: tuple[str, ...] = (
    DEFECT_PASS,
    DEFECT_TOOTH_MISSING,
    DEFECT_TOOTH_CHIPPED,
    DEFECT_TOOTH_COUNT_WRONG,
    DEFECT_BORE_OVERSIZE,
    DEFECT_BORE_UNDERSIZE,
    DEFECT_BORE_MISSING,
    DEFECT_KEYWAY_MISSING,
    DEFECT_HOLE_MISSING,
    DEFECT_HOLE_OFF_CIRCLE,
    DEFECT_BOLT_PATTERN_ROTATED,
)

# Formele de contur. Doar roata are dinți; restul sunt piese plane cu muchii drepte ori arce.
SHAPE_GEAR = "gear"
SHAPE_PLATE = "plate"
SHAPE_TRIANGLE = "triangle"
SHAPE_HEXAGON = "hexagon"
SHAPE_LINK = "link"
SHAPES: tuple[str, ...] = (
    SHAPE_GEAR,
    SHAPE_PLATE,
    SHAPE_TRIANGLE,
    SHAPE_HEXAGON,
    SHAPE_LINK,
)

# Ponderea lui PASS în eșantionarea aleatoare. Restul se împarte egal între defecte.
PASS_WEIGHT = 0.35

# Numărul de dinți nu este multiplu de 5, ca ansamblul „dinți + cerc de găuri" să rămână
# cu simetrie de ordin 1. Altfel o rotire de 72° ar suprapune piesa peste ea însăși, iar
# dezalinierea dintre cercul de găuri și dinți ar deveni invizibilă.
TEETH_NOMINAL = 18
TEETH_WRONG = (17, 19)

# Lungimea țintă a coardei, în milimetri. Dă rezoluția inelelor 2D.
CHORD_MM = 0.6


def _nominal_params() -> "GearParams":
    return GearParams()


@dataclass(frozen=True)
class GearParams:
    """Parametrii completi ai piesei. Include defectele, ca tupluri (obiect imutabil)."""

    outer_radius: float = 50.0
    root_radius: float = 42.0
    bore_radius: float = 20.0
    keyway: bool = True
    keyway_width: float = 12.0
    keyway_depth: float = 5.0
    teeth: int = TEETH_NOMINAL
    tooth_tip_width_deg: float = 8.0
    tooth_root_width_deg: float = 12.0
    chip_depth: float = 4.0
    thickness: float = 15.0
    bolt_count: int = 5
    bolt_circle_radius: float = 35.0
    hole_radius: float = 4.5
    pattern_phase_deg: float = 0.0
    missing_teeth: tuple[int, ...] = ()
    chipped_teeth: tuple[int, ...] = ()
    missing_holes: tuple[int, ...] = ()
    hole_radial_offsets: tuple[tuple[int, float], ...] = ()
    shape: str = SHAPE_GEAR
    half_width: float = 0.0
    half_height: float = 0.0
    corner_radius: float = 0.0

    def __post_init__(self) -> None:
        if self.shape not in SHAPES:
            raise ValueError(f"formă necunoscută: {self.shape!r}; cunoscute: {SHAPES}")
        if self.thickness <= 0:
            raise ValueError("grosimea trebuie să fie pozitivă")
        if self.bolt_count < 1:
            raise ValueError("piesa are nevoie de cel puțin o gaură de buloane")
        # Adâncimea penei contează doar la piesele care au pană: o piesă fără pană are
        # alezajul rotund, iar un `keyway_depth` rămas în parametri nu lărgește golul.
        keyway_depth = self.keyway_depth if self.keyway else 0.0
        if self.shape == SHAPE_GEAR:
            self._validate_gear(keyway_depth)
        elif self.shape == SHAPE_PLATE:
            self._validate_plate(keyway_depth)
        elif self.shape == SHAPE_LINK:
            self._validate_link(keyway_depth)
        else:
            self._validate_regular_polygon(keyway_depth)

    def _validate_gear(self, keyway_depth: float) -> None:
        if self.teeth < 3:
            raise ValueError("piesa are nevoie de cel puțin 3 dinți")
        if not self.root_radius < self.outer_radius:
            raise ValueError("root_radius trebuie să fie mai mic decât outer_radius")
        root_half = math.radians(self.tooth_root_width_deg) / 2
        tip_half = math.radians(self.tooth_tip_width_deg) / 2
        pitch_half = math.pi / self.teeth
        if not tip_half < root_half < pitch_half:
            raise ValueError(
                "lățimile dinților trebuie să respecte tip < root < pas/2 "
                f"(tip={tip_half:.4f}, root={root_half:.4f}, pas/2={pitch_half:.4f})"
            )
        if len(self.missing_teeth) + len(self.chipped_teeth) >= self.teeth:
            raise ValueError("nu pot lipsi toți dinții")
        if self.bore_radius > 0:
            if self.bore_radius + keyway_depth >= self.root_radius:
                raise ValueError("alezajul iese din coroana dintată")
        inner = self.bolt_circle_radius - self.hole_radius
        outer = self.bolt_circle_radius + self.hole_radius
        if self.bore_radius > 0 and inner <= self.bore_radius + keyway_depth:
            raise ValueError("găurile de buloane intră în alezaj")
        if outer >= self.root_radius:
            raise ValueError("găurile de buloane ies din coroana dintată")

    def _validate_plate(self, keyway_depth: float) -> None:
        if self.half_width <= 0 or self.half_height <= 0:
            raise ValueError("placa are laturi pozitive")
        margin = min(self.half_width, self.half_height)
        if not 0 < self.corner_radius < margin:
            raise ValueError("colțul plăcii trebuie să încapă între laturi")
        if self.outer_radius + 1e-9 < math.hypot(self.half_width, self.half_height):
            raise ValueError("outer_radius trebuie să acopere placa")
        self._validate_planar_holes(keyway_depth, margin)

    def _validate_regular_polygon(self, keyway_depth: float) -> None:
        if self.outer_radius <= 0:
            raise ValueError("poligonul are nevoie de o rază pozitivă")
        limit = self.inradius_mm
        if not 0 < self.corner_radius < limit:
            raise ValueError("colțul rotunjit trebuie să încapă în poligon")
        self._validate_planar_holes(keyway_depth, limit)

    def _validate_link(self, keyway_depth: float) -> None:
        if self.half_width <= self.half_height:
            raise ValueError("legătura trebuie să fie mai lungă decât lată")
        if self.outer_radius + 1e-9 < self.half_width:
            raise ValueError("outer_radius trebuie să acopere legătura")
        if self.bolt_count != 2:
            raise ValueError("legătura are exact două găuri de buloane")
        if self.bore_radius > 0 and self.bore_radius + keyway_depth >= self.half_height:
            raise ValueError("alezajul iese din lățimea legăturii")
        if self.hole_radius >= self.half_height:
            raise ValueError("gaura de buloane iese din lățimea legăturii")
        inner = self.bolt_circle_radius - self.hole_radius
        if self.bore_radius > 0 and inner <= self.bore_radius + keyway_depth:
            raise ValueError("găurile de buloane intră în alezaj")
        offset = max((value for _, value in self.hole_radial_offsets), default=0.0)
        if self.bolt_circle_radius + offset + self.hole_radius >= self.half_width:
            raise ValueError("găurile de buloane ies din lungimea legăturii")
        phase = math.radians(self.pattern_phase_deg)
        if (self.bolt_circle_radius + offset) * abs(math.sin(phase)) + self.hole_radius \
                >= self.half_height:
            raise ValueError("cercul de găuri iese din lățimea legăturii")

    def _validate_planar_holes(self, keyway_depth: float, inradius: float) -> None:
        """Găurile și alezajul încap în cercul înscris, cu loc pentru fiecare defect.

        Alezajul mărit cu 2 mm și gaura deplasată cu 2,5 mm trebuie să rămână în siluetă;
        altfel un defect ar produce o piesă invalidă, nu una rebut.
        """
        if self.bore_radius > 0 and self.bore_radius + keyway_depth >= inradius:
            raise ValueError("alezajul iese din siluetă")
        inner = self.bolt_circle_radius - self.hole_radius
        if self.bore_radius > 0 and inner <= self.bore_radius + keyway_depth:
            raise ValueError("găurile de buloane intră în alezaj")
        offset = max((value for _, value in self.hole_radial_offsets), default=0.0)
        if self.bolt_circle_radius + offset + self.hole_radius >= inradius:
            raise ValueError("găurile de buloane ies din siluetă")
        if self.bolt_count > 1:
            spacing = 2.0 * self.bolt_circle_radius * math.sin(math.pi / self.bolt_count)
            if spacing <= 2.0 * self.hole_radius:
                raise ValueError("găurile de buloane se ating")

    @property
    def inradius_mm(self) -> float:
        """Raza cercului înscris: cât de departe de margine intră o gaură centrată.

        La legătură, cercul înscris este doar lățimea capătului, iar găurile stau pe axa
        lungă; validarea ei are reguli proprii.
        """
        if self.shape == SHAPE_GEAR:
            return self.root_radius
        if self.shape == SHAPE_PLATE:
            return min(self.half_width, self.half_height)
        if self.shape == SHAPE_LINK:
            return self.half_height
        if self.shape == SHAPE_TRIANGLE:
            return self.outer_radius / 2.0
        return self.outer_radius * math.cos(math.pi / 6.0)

    def as_dict(self) -> dict[str, Any]:
        """Reprezentare serializabilă, pentru manifestul de etichete."""
        return {
            "outer_radius": self.outer_radius,
            "root_radius": self.root_radius,
            "bore_radius": self.bore_radius,
            "keyway": self.keyway,
            "keyway_width": self.keyway_width,
            "keyway_depth": self.keyway_depth,
            "teeth": self.teeth,
            "tooth_tip_width_deg": self.tooth_tip_width_deg,
            "tooth_root_width_deg": self.tooth_root_width_deg,
            "thickness": self.thickness,
            "bolt_count": self.bolt_count,
            "bolt_circle_radius": self.bolt_circle_radius,
            "hole_radius": self.hole_radius,
            "pattern_phase_deg": self.pattern_phase_deg,
            "missing_teeth": list(self.missing_teeth),
            "chipped_teeth": list(self.chipped_teeth),
            "missing_holes": list(self.missing_holes),
            "hole_radial_offsets": [list(item) for item in self.hole_radial_offsets],
            "shape": self.shape,
            "half_width": self.half_width,
            "half_height": self.half_height,
            "corner_radius": self.corner_radius,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GearParams":
        """Reconstruiește parametrii din manifest, fără să depindă de niciun seed."""
        known = {item.name for item in fields(cls)}
        data = {key: value for key, value in payload.items() if key in known}
        for key in ("missing_teeth", "chipped_teeth", "missing_holes"):
            data[key] = tuple(data.get(key) or ())
        data["hole_radial_offsets"] = tuple(
            tuple(item) for item in (data.get("hole_radial_offsets") or ())
        )
        return cls(**data)


NOMINAL = _nominal_params()


# ------------------------------------------------------------------ familiile de piese

# Familiile care intră în celulă. Două sunt roți dințate — piesa de referință și pinionul —,
# iar patru au altă siluetă: placă dreptunghiulară, cornier triunghiular, piuliță hexagonală și
# legătură cu capete semicirculare. Același proces și aceeași sculă, dar siluetă vizibil
# diferită în celulă. Diferențele sunt intenționat în plan, fiindcă de sus, din camera de
# inspecție, se vede silueta, iar grosimea aproape deloc (docs/10-dataset-piese.md).
#
# Gabaritele stau sub Ø110 și peste Ø50: sub ~Ø8 nu se mai distinge nimic la rezoluția de
# inspecție, iar peste Ø110 piesa nu mai intră în universal. Toate piesele încap între fălcile
# de 80 mm ale prinzătorului, în afară de roata de referință, care se prinde pe fața de sus.
#
# Fiecare familie trebuie să rămână la distanță de acțiunea fiecărui defect: alezajul mărit
# cu 2 mm nu are voie să ajungă în cercul de găuri, gaura deplasată cu 2,5 mm nu are voie să
# iasă din coroană, iar lățimile dinților trebuie să rămână valide și la numărul de dinți
# greșit (17 ori 19). Constantele din tabel sunt verificate în
# ``tests/unit/test_part_generator.py``.
@dataclass(frozen=True)
class PartFamily:
    """O familie de piese: numele, descrierea și parametrii nominali."""

    name: str
    description: str
    nominal: GearParams

    @property
    def shape(self) -> str:
        return self.nominal.shape

    @property
    def teeth(self) -> int:
        return self.nominal.teeth

    @property
    def diameter_mm(self) -> float:
        """Diametrul cercului care încadrează piesa, nu doar al corpului."""
        return 2.0 * self.nominal.outer_radius

    @property
    def thickness_mm(self) -> float:
        return self.nominal.thickness

    @property
    def has_keyway(self) -> bool:
        return self.nominal.keyway

    @property
    def supports_teeth(self) -> bool:
        return self.shape == SHAPE_GEAR

    @property
    def grip_width_mm(self) -> float:
        """Cea mai mică lățime din plan: ce trebuie să încapă între fălcile de 80 mm."""
        if self.supports_teeth:
            return self.diameter_mm
        if self.shape == SHAPE_PLATE:
            return 2.0 * min(self.nominal.half_width, self.nominal.half_height)
        if self.shape == SHAPE_TRIANGLE:
            return 1.5 * self.nominal.outer_radius
        if self.shape == SHAPE_HEXAGON:
            return math.sqrt(3.0) * self.nominal.outer_radius
        return 2.0 * self.nominal.half_height

    def defect_classes(self) -> tuple[str, ...]:
        """Clasele care au sens pe familia asta.

        Pana dispărută nu are sens pe o piesă fără pană: ar da exact piesa nominală a
        familiei, adică o etichetă de rebut pe o geometrie corectă. Dinții lipsă nu au
        sens pe o piesă fără dinți, din același motiv.
        """
        classes = DEFECT_CLASSES
        if not self.supports_teeth:
            classes = tuple(item for item in classes if not item.startswith("TOOTH_"))
        if not self.has_keyway:
            classes = tuple(item for item in classes if item != DEFECT_KEYWAY_MISSING)
        return classes

    def as_dict(self) -> dict[str, Any]:
        """Reprezentare serializabilă: intră în manifest, lângă eticheta fiecărei piese."""
        return {
            "name": self.name,
            "description": self.description,
            "shape": self.shape,
            "teeth": self.teeth,
            "diameter_mm": self.diameter_mm,
            "thickness_mm": self.thickness_mm,
        }


FAMILIES: tuple[PartFamily, ...] = (
    PartFamily(
        name="wheel_100",
        description="roata de referință: Ø100, 18 dinți, alezaj Ø40 cu pană 12 × 5, 5 găuri Ø9",
        nominal=NOMINAL,
    ),
    PartFamily(
        name="gusset_72",
        description="cornierul: triunghi echilateral cu cerc încadrător Ø72, colțuri R6, "
                    "alezaj Ø14, 3 găuri Ø5",
        nominal=GearParams(
            outer_radius=36.0,
            root_radius=0.0,
            bore_radius=7.0,
            keyway=False,
            keyway_depth=0.0,
            teeth=0,
            thickness=10.0,
            bolt_count=3,
            bolt_circle_radius=12.0,
            hole_radius=2.5,
            shape=SHAPE_TRIANGLE,
            corner_radius=6.0,
        ),
    ),
    PartFamily(
        name="hex_68",
        description="piulița hexagonală: cerc încadrător Ø68, colțuri R6, alezaj Ø24, "
                    "6 găuri Ø4",
        nominal=GearParams(
            outer_radius=34.0,
            root_radius=0.0,
            bore_radius=12.0,
            keyway=False,
            keyway_depth=0.0,
            teeth=0,
            thickness=14.0,
            bolt_count=6,
            bolt_circle_radius=22.0,
            hole_radius=2.0,
            shape=SHAPE_HEXAGON,
            corner_radius=6.0,
        ),
    ),
    PartFamily(
        name="link_68",
        description="legătura: 68 × 28 mm, capete semicirculare, alezaj Ø12, 2 găuri Ø8",
        nominal=GearParams(
            outer_radius=34.0,
            root_radius=0.0,
            bore_radius=6.0,
            keyway=False,
            keyway_depth=0.0,
            teeth=0,
            thickness=12.0,
            bolt_count=2,
            bolt_circle_radius=24.0,
            hole_radius=4.0,
            shape=SHAPE_LINK,
            half_width=34.0,
            half_height=14.0,
            corner_radius=14.0,
        ),
    ),
    PartFamily(
        name="pinion_56",
        description="pinionul: Ø56, 12 dinți rari, alezaj Ø14 cu pană 5 × 3, 4 găuri Ø6",
        nominal=GearParams(
            outer_radius=28.0,
            root_radius=22.0,
            bore_radius=7.0,
            keyway_width=5.0,
            keyway_depth=3.0,
            teeth=12,
            thickness=12.0,
            bolt_count=4,
            bolt_circle_radius=15.5,
            hole_radius=3.0,
        ),
    ),
    PartFamily(
        name="plate_84",
        description="placa de montaj: 84 × 56 mm, colțuri R8, alezaj Ø18, 4 găuri Ø6",
        nominal=GearParams(
            outer_radius=math.hypot(42.0, 28.0),
            root_radius=26.0,
            bore_radius=9.0,
            keyway=False,
            keyway_depth=0.0,
            teeth=0,
            thickness=12.0,
            bolt_count=4,
            bolt_circle_radius=20.0,
            hole_radius=3.0,
            shape=SHAPE_PLATE,
            half_width=42.0,
            half_height=28.0,
            corner_radius=8.0,
        ),
    ),
)

FAMILY_BY_NAME: Mapping[str, PartFamily] = {family.name: family for family in FAMILIES}

# Familia implicită. Toate uneltele care nu primesc o familie lucrează pe piesa de referință,
# deci rezultatele de dinainte de familii se reproduc exact.
DEFAULT_FAMILY = "wheel_100"


def family_of(family: str | PartFamily | None = None) -> PartFamily:
    """Familia cerută, ori cea implicită. Acceptă și obiectul, ca să nu se caute de două ori."""
    if isinstance(family, PartFamily):
        return family
    if family is None:
        return FAMILY_BY_NAME[DEFAULT_FAMILY]
    try:
        return FAMILY_BY_NAME[family]
    except KeyError:
        raise ValueError(
            f"familie necunoscută: {family!r}; cunoscute: {sorted(FAMILY_BY_NAME)}"
        ) from None


@dataclass(frozen=True)
class PartVariant:
    """O variantă generată: meshul, parametrii și eticheta."""

    part_id: str
    seed: int
    family: str
    defect: str
    verdict: str
    params: GearParams
    vertices: np.ndarray = field(repr=False)
    faces: np.ndarray = field(repr=False)

    @property
    def is_pass(self) -> bool:
        return self.verdict == VERDICT_PASS


def _point(radius: float, angle: float) -> list[float]:
    return [radius * math.cos(angle), radius * math.sin(angle)]


def _arc(radius: float, start: float, stop: float, skip_start: bool = False) -> np.ndarray:
    """Puncte pe un arc, de la ``start`` la ``stop``, fără capătul final.

    Cu ``skip_start`` se sare și primul punct, pentru arcele care continuă după un colț
    deja emis. Fără asta ar apărea doi vertecși în același loc, adică o muchie degenerată.
    """
    span = stop - start
    if abs(span) < 1e-12:
        return np.empty((0, 2), dtype=np.float64)
    count = max(1, int(math.ceil(abs(span) * radius / CHORD_MM)))
    angles = np.linspace(start, stop, count + 1)[:-1]
    if skip_start:
        angles = angles[1:]
    if not len(angles):
        return np.empty((0, 2), dtype=np.float64)
    return np.column_stack([radius * np.cos(angles), radius * np.sin(angles)])


def _circle(radius: float) -> np.ndarray:
    count = max(8, int(math.ceil(2 * math.pi * radius / CHORD_MM)))
    angles = np.linspace(0.0, 2 * math.pi, count, endpoint=False)
    return np.column_stack([radius * np.cos(angles), radius * np.sin(angles)])


def _toothed_outline(params: GearParams) -> np.ndarray:
    """Conturul exterior, cu dinți, în sens trigonometric (CCW)."""
    step = 2 * math.pi / params.teeth
    root_half = math.radians(params.tooth_root_width_deg) / 2
    tip_half = math.radians(params.tooth_tip_width_deg) / 2
    missing = set(params.missing_teeth)
    chipped = set(params.chipped_teeth)

    pieces: list[np.ndarray] = []
    previous_root: float | None = None
    first_start: float | None = None

    for index in range(params.teeth):
        if index in missing:
            continue
        centre = index * step
        lead_root = centre - root_half
        trail_root = centre + root_half
        if previous_root is None:
            first_start = lead_root
        else:
            pieces.append(_arc(params.root_radius, previous_root, lead_root, skip_start=True))

        if index in chipped:
            # Colțul de atac rămâne, vârful cade pe o pantă: arată ca un dinte ciobit.
            pieces.append(
                np.array(
                    [
                        _point(params.outer_radius, centre - tip_half),
                        _point(params.outer_radius - params.chip_depth, centre + tip_half * 0.15),
                    ]
                )
            )
        else:
            pieces.append(np.array([_point(params.outer_radius, centre - tip_half)]))
            pieces.append(
                _arc(params.outer_radius, centre - tip_half, centre + tip_half, skip_start=True)
            )
        pieces.append(np.array([_point(params.root_radius, trail_root)]))
        previous_root = trail_root

    if previous_root is None or first_start is None:
        raise ValueError("nu a rămas niciun dinte pe contur")
    pieces.append(
        _arc(params.root_radius, previous_root, first_start + 2 * math.pi, skip_start=True)
    )
    return np.vstack([piece for piece in pieces if len(piece)])


def _plate_outline(params: GearParams) -> np.ndarray:
    """Conturul unei plăci dreptunghiulare cu colțuri rotunjite, în sens trigonometric.

    Colțurile se parcurg în ordine CCW, iar laturile ies drepte între capetele arcelor:
    punctul de start stă pe latura de est, chiar sub colțul de sus, iar ultimul arc se
    închide pe același punct prin latura de est.
    """
    width = params.half_width
    height = params.half_height
    radius = params.corner_radius
    corners = (
        (width - radius, height - radius, 0.0),
        (-width + radius, height - radius, 90.0),
        (-width + radius, -height + radius, 180.0),
        (width - radius, -height + radius, 270.0),
    )
    pieces: list[np.ndarray] = []
    for cx, cy, start_deg in corners:
        span = math.radians(90.0)
        count = max(2, int(math.ceil(span * radius / CHORD_MM)))
        angles = np.linspace(math.radians(start_deg), math.radians(start_deg) + span,
                             count + 1)
        if pieces:
            angles = angles[1:]
        pieces.append(np.column_stack([cx + radius * np.cos(angles),
                                       cy + radius * np.sin(angles)]))
    return np.vstack(pieces)


def _centered_arc(cx: float, cy: float, radius: float,
                  start: float, sweep: float) -> np.ndarray:
    """Puncte pe un arc cu centrul dat, de la ``start`` cu ``sweep`` radiani, inclusiv capetele."""
    count = max(2, int(math.ceil(abs(sweep) * radius / CHORD_MM)))
    angles = start + sweep * np.linspace(0.0, 1.0, count + 1)
    return np.column_stack([cx + radius * np.cos(angles), cy + radius * np.sin(angles)])


def _rounded_polygon(vertices: np.ndarray, radius: float) -> np.ndarray:
    """Conturul unui poligon convex, cu colțurile rotunjite la ``radius``.

    Pentru fiecare colț se calculează centrul arcului tangent la cele două laturi, apoi arcul
    dintre punctele de tangență. Laturile drepte ies între capetele arcelor vecine.
    """
    if radius <= 0:
        return vertices

    count = len(vertices)
    pieces: list[np.ndarray] = []
    for index in range(count):
        previous = vertices[index - 1]
        current = vertices[index]
        following = vertices[(index + 1) % count]
        toward_previous = (previous - current) / np.linalg.norm(previous - current)
        toward_next = (following - current) / np.linalg.norm(following - current)
        cosine = float(np.clip(np.dot(toward_previous, toward_next), -1.0, 1.0))
        half_angle = math.acos(cosine) / 2.0
        if half_angle < 1e-6:
            raise ValueError("colț degenerat în poligon")
        tangent = radius / math.tan(half_angle)
        bisector = toward_previous + toward_next
        bisector /= np.linalg.norm(bisector)
        center = current + bisector * (radius / math.sin(half_angle))
        start_point = current + toward_previous * tangent
        stop_point = current + toward_next * tangent
        start_dx, start_dy = start_point - center
        stop_dx, stop_dy = stop_point - center
        start_angle = math.atan2(float(start_dy), float(start_dx))
        stop_angle = math.atan2(float(stop_dy), float(stop_dx))
        sweep = (stop_angle - start_angle) % (2.0 * math.pi)
        if sweep > math.pi:
            sweep -= 2.0 * math.pi
        pieces.append(_centered_arc(float(center[0]), float(center[1]), radius,
                                    start_angle, sweep))
    return np.vstack(pieces)


def _regular_outline(params: GearParams, sides: int, start_deg: float) -> np.ndarray:
    """Un poligon regulat cu circumraza dată, rotunjit la colțuri."""
    angles = math.radians(start_deg) + 2.0 * math.pi * np.arange(sides) / sides
    vertices = params.outer_radius * np.column_stack([np.cos(angles), np.sin(angles)])
    return _rounded_polygon(vertices, params.corner_radius)


def _link_outline(params: GearParams) -> np.ndarray:
    """O legătură cu capete semicirculare: două arce de 180° unite prin laturi drepte.

    Punctul de start este capătul de jos al arcului din dreapta, deci conturul se închide
    prin latura de jos, fără să repete niciun vîrf.
    """
    radius = params.half_height
    offset = params.half_width - radius
    right = _centered_arc(offset, 0.0, radius, -math.pi / 2.0, math.pi)
    left = _centered_arc(-offset, 0.0, radius, math.pi / 2.0, math.pi)
    return np.vstack([right, left])


def _outer_outline(params: GearParams) -> np.ndarray:
    """Conturul exterior al formei cerute."""
    if params.shape == SHAPE_PLATE:
        return _plate_outline(params)
    if params.shape == SHAPE_TRIANGLE:
        return _regular_outline(params, 3, start_deg=90.0)
    if params.shape == SHAPE_HEXAGON:
        return _regular_outline(params, 6, start_deg=30.0)
    if params.shape == SHAPE_LINK:
        return _link_outline(params)
    return _toothed_outline(params)


def _bore_outline(params: GearParams) -> np.ndarray:
    """Conturul alezajului, cu pana de cheie opțională, în sens trigonometric."""
    radius = params.bore_radius
    if radius <= 0:
        return np.empty((0, 2), dtype=np.float64)
    if not params.keyway or params.keyway_width <= 0:
        return _circle(radius)

    half = math.asin(min(params.keyway_width / 2 / radius, 0.999))
    body = _arc(radius, half, 2 * math.pi - half)
    notch = np.array(
        [
            _point(radius + params.keyway_depth, 2 * math.pi - half),
            _point(radius + params.keyway_depth, half),
        ]
    )
    return np.vstack([body, notch])


def _hole_outline(params: GearParams, index: int) -> np.ndarray:
    angle = math.radians(params.pattern_phase_deg) + 2 * math.pi * index / params.bolt_count
    offset = dict(params.hole_radial_offsets).get(index, 0.0)
    radius = params.bolt_circle_radius + offset
    centre = np.array([radius * math.cos(angle), radius * math.sin(angle)])
    return _circle(params.hole_radius) + centre


def build_loops(params: GearParams) -> tuple[np.ndarray, list[int]]:
    """Inelele 2D ale piesei. Întoarce punctele și capetele de inel pentru earcut."""
    outer = _outer_outline(params)
    rings: list[np.ndarray] = [outer]

    bore = _bore_outline(params)
    if len(bore):
        rings.append(bore[::-1])  # găurile se parcurg invers (CW)

    missing_holes = set(params.missing_holes)
    for index in range(params.bolt_count):
        if index in missing_holes:
            continue
        rings.append(_hole_outline(params, index)[::-1])

    points = np.vstack(rings)
    ends: list[int] = []
    total = 0
    for ring in rings:
        total += len(ring)
        ends.append(total)
    return points, ends


def build_mesh(params: GearParams) -> tuple[np.ndarray, np.ndarray]:
    """Construiește meshul închis. Întoarce vertecșii (N,3) și fețele (M,3)."""
    points, ends = build_loops(params)
    triangles = _earcut.triangulate_float64(points, np.asarray(ends, dtype=np.uint32))
    triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    if not len(triangles):
        raise ValueError("triangularea a eșuat")

    count = len(points)
    half = params.thickness / 2
    top = np.column_stack([points, np.full(count, half)])
    bottom = np.column_stack([points, np.full(count, -half)])
    vertices = np.vstack([top, bottom]).astype(np.float64)

    faces: list[np.ndarray] = [triangles, triangles[:, ::-1] + count]

    # Pereții laterali: câte o bandă pentru fiecare inel. Pentru inelul exterior dă
    # normala spre exterior, iar pentru găuri spre interiorul golului, fiindcă inelele
    # interioare sunt parcurse invers.
    start = 0
    for end in ends:
        ring = np.arange(start, end)
        nxt = np.roll(ring, -1)
        quads = np.column_stack([ring, ring + count, nxt + count, nxt])
        faces.append(quads[:, [0, 1, 2]])
        faces.append(quads[:, [0, 2, 3]])
        start = end

    return vertices, np.vstack(faces).astype(np.int64)


# ------------------------------------------------------------------ aplicarea defectelor


def _pick_tooth(params: GearParams, rng: random.Random) -> int:
    taken = set(params.missing_teeth) | set(params.chipped_teeth)
    available = [index for index in range(params.teeth) if index not in taken]
    if len(available) < 3:
        raise ValueError("prea puțini dinți liberi pentru a marca un defect")
    return available[rng.randrange(len(available))]


def apply_defect(params: GearParams, defect: str, rng: random.Random) -> GearParams:
    """Întoarce parametrii cu defectul aplicat. Nu modifică intrarea."""
    if defect == DEFECT_PASS:
        return params
    if defect.startswith("TOOTH_") and params.shape != SHAPE_GEAR:
        raise ValueError(f"defectul {defect} nu are sens pe forma {params.shape}")
    if defect == DEFECT_TOOTH_MISSING:
        index = _pick_tooth(params, rng)
        return replace(params, missing_teeth=tuple(sorted({*params.missing_teeth, index})))
    if defect == DEFECT_TOOTH_CHIPPED:
        index = _pick_tooth(params, rng)
        return replace(params, chipped_teeth=tuple(sorted({*params.chipped_teeth, index})))
    if defect == DEFECT_TOOTH_COUNT_WRONG:
        return replace(params, teeth=rng.choice(TEETH_WRONG))
    if defect == DEFECT_BORE_OVERSIZE:
        return replace(params, bore_radius=params.bore_radius + 2.0)
    if defect == DEFECT_BORE_UNDERSIZE:
        return replace(params, bore_radius=params.bore_radius - 1.5)
    if defect == DEFECT_BORE_MISSING:
        return replace(params, bore_radius=0.0)
    if defect == DEFECT_KEYWAY_MISSING:
        return replace(params, keyway=False)
    if defect == DEFECT_HOLE_MISSING:
        index = rng.randrange(params.bolt_count)
        return replace(params, missing_holes=(index,))
    if defect == DEFECT_HOLE_OFF_CIRCLE:
        index = rng.randrange(params.bolt_count)
        return replace(params, hole_radial_offsets=((index, 2.5),))
    if defect == DEFECT_BOLT_PATTERN_ROTATED:
        return replace(params, pattern_phase_deg=15.0)
    raise ValueError(f"defect necunoscut: {defect}")


def _jittered(base: GearParams, rng: random.Random) -> GearParams:
    """Abatere în toleranță. Rămâne mică, ca piesele bune să fie greu de distins."""
    if base.shape == SHAPE_PLATE:
        half_width = base.half_width + rng.uniform(-0.2, 0.2)
        half_height = base.half_height + rng.uniform(-0.2, 0.2)
        return replace(
            base,
            half_width=half_width,
            half_height=half_height,
            corner_radius=base.corner_radius + rng.uniform(-0.1, 0.1),
            outer_radius=math.hypot(half_width, half_height),
            bore_radius=base.bore_radius + rng.uniform(-0.1, 0.1),
            thickness=base.thickness + rng.uniform(-0.2, 0.2),
            bolt_circle_radius=base.bolt_circle_radius + rng.uniform(-0.2, 0.2),
            hole_radius=base.hole_radius + rng.uniform(-0.05, 0.05),
        )
    if base.shape == SHAPE_LINK:
        half_width = base.half_width + rng.uniform(-0.2, 0.2)
        half_height = base.half_height + rng.uniform(-0.2, 0.2)
        return replace(
            base,
            half_width=half_width,
            half_height=half_height,
            corner_radius=half_height,
            outer_radius=half_width,
            bore_radius=base.bore_radius + rng.uniform(-0.1, 0.1),
            thickness=base.thickness + rng.uniform(-0.2, 0.2),
            bolt_circle_radius=base.bolt_circle_radius + rng.uniform(-0.2, 0.2),
            hole_radius=base.hole_radius + rng.uniform(-0.05, 0.05),
        )
    if base.shape in (SHAPE_TRIANGLE, SHAPE_HEXAGON):
        return replace(
            base,
            outer_radius=base.outer_radius + rng.uniform(-0.3, 0.3),
            corner_radius=base.corner_radius + rng.uniform(-0.1, 0.1),
            bore_radius=base.bore_radius + rng.uniform(-0.1, 0.1),
            thickness=base.thickness + rng.uniform(-0.2, 0.2),
            bolt_circle_radius=base.bolt_circle_radius + rng.uniform(-0.2, 0.2),
            hole_radius=base.hole_radius + rng.uniform(-0.05, 0.05),
        )
    return replace(
        base,
        outer_radius=base.outer_radius + rng.uniform(-0.3, 0.3),
        root_radius=base.root_radius + rng.uniform(-0.3, 0.3),
        bore_radius=base.bore_radius + rng.uniform(-0.1, 0.1),
        thickness=base.thickness + rng.uniform(-0.2, 0.2),
        bolt_circle_radius=base.bolt_circle_radius + rng.uniform(-0.2, 0.2),
        hole_radius=base.hole_radius + rng.uniform(-0.05, 0.05),
        keyway_width=base.keyway_width + rng.uniform(-0.1, 0.1),
    )


def choose_defect(rng: random.Random, classes: tuple[str, ...] = DEFECT_CLASSES) -> str:
    """Alege o clasă. PASS are ponderea lui, restul se împart egal."""
    rejects = [item for item in classes if item != DEFECT_PASS]
    if rng.random() < PASS_WEIGHT or not rejects:
        return DEFECT_PASS
    return rng.choice(rejects)


def build_variant(seed: int, defect: str | None = None, family: str | PartFamily | None = None) -> PartVariant:
    """Construiește varianta pentru un seed. Cu ``defect`` dat, clasa este forțată.

    Fluxurile de randomizare sunt separate: abaterea în toleranță, alegerea clasei și
    alegerea indicelui (dintele ori gaura afectată) nu se influențează reciproc. Astfel,
    forțarea clasei dă exact aceeași geometrie ca rularea aleatoare care a produs clasa.

    ``family`` alege familia de piese; fără el se lucrează pe piesa de referință, deci
    rezultatele de dinainte de familii se reproduc exact.
    """
    item = family_of(family)
    jitter_rng = random.Random(f"{seed}:geometry")
    class_rng = random.Random(f"{seed}:class")
    index_rng = random.Random(f"{seed}:index")

    base = _jittered(item.nominal, jitter_rng)
    classes = item.defect_classes()
    chosen = defect if defect is not None else choose_defect(class_rng, classes)
    if chosen not in classes:
        raise ValueError(f"clasa {chosen} nu are sens pe familia {item.name}")
    params = apply_defect(base, chosen, index_rng)
    vertices, faces = build_mesh(params)
    return PartVariant(
        part_id=f"part-{seed:08d}",
        seed=seed,
        family=item.name,
        defect=chosen,
        verdict=VERDICT_PASS if chosen == DEFECT_PASS else VERDICT_REJECT,
        params=params,
        vertices=vertices,
        faces=faces,
    )


def build_variant_from_params(
    seed: int,
    defect: str,
    params: Mapping[str, Any],
    family: str | None = None,
) -> PartVariant:
    """Reconstruiește exact o variantă din manifest, fără randomizare."""
    restored = GearParams.from_dict(params)
    vertices, faces = build_mesh(restored)
    return PartVariant(
        part_id=f"part-{seed:08d}",
        seed=seed,
        family=family_of(family).name,
        defect=defect,
        verdict=VERDICT_PASS if defect == DEFECT_PASS else VERDICT_REJECT,
        params=restored,
        vertices=vertices,
        faces=faces,
    )


def label_of(variant: PartVariant) -> Mapping[str, Any]:
    """Câmpurile de etichetă ale unei variante, pentru manifest."""
    return {
        "part_id": variant.part_id,
        "seed": variant.seed,
        "family": variant.family,
        "verdict": variant.verdict,
        "defect": variant.defect,
        "params": variant.params.as_dict(),
    }
