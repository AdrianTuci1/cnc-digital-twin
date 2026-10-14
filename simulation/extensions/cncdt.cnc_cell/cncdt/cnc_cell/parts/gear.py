"""Generatorul procedural al roților dințate.

Piesa de referință este o roată dințată plană: contur dințat, alezaj central cu pană de
cheie și un cerc de găuri de buloane. Meshul este construit direct, din inele 2D, fără
operații booleene la runtime:

* conturul dințat, alezajul (cu sau fără pană) și fiecare gaură sunt câte un inel 2D;
* fețele de sus și de jos se triangulează cu `mapbox_earcut`, care acceptă inele interioare;
* pereții laterali se ridică între cele două fețe, folosind aceiași indici.

Fiecare defect este o schimbare de parametri, nu o operație pe geometrie. Astfel geometria
randată și eticheta provin din același obiect :class:`GearParams`, iar un dataset se poate
regenera exact din seed.

Piesele sunt grupate în **familii** (:data:`FAMILIES`): același proces și aceeași sculă, dar
diametru, număr de dinți, alezaj și cerc de găuri diferite. Familia este prima etichetă a
piesei — modelul de inspecție trebuie să spună întâi *ce* piesă este, apoi dacă e conformă.

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

    def __post_init__(self) -> None:
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
        # Adâncimea penei contează doar la piesele care au pană: o piesă fără pană are
        # alezajul rotund, iar un `keyway_depth` rămas în parametri nu lărgește golul.
        keyway_depth = self.keyway_depth if self.keyway else 0.0
        if self.bore_radius > 0:
            if self.bore_radius + keyway_depth >= self.root_radius:
                raise ValueError("alezajul iese din coroana dintată")
        if self.bolt_count < 1:
            raise ValueError("piesa are nevoie de cel puțin o gaură de buloane")
        inner = self.bolt_circle_radius - self.hole_radius
        outer = self.bolt_circle_radius + self.hole_radius
        if self.bore_radius > 0 and inner <= self.bore_radius + keyway_depth:
            raise ValueError("găurile de buloane intră în alezaj")
        if outer >= self.root_radius:
            raise ValueError("găurile de buloane ies din coroana dintată")
        if self.thickness <= 0:
            raise ValueError("grosimea trebuie să fie pozitivă")

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

# Familiile care intră în celulă. Sunt toate roți dințate plane — același proces, aceeași
# sculă — dar diferă în plan: diametru, număr de dinți, alezaj, pană de cheie și cerc de
# găuri. Diferențele sunt intenționat în plan, fiindcă de sus, din camera de inspecție, se
# vede silueta, iar grosimea aproape deloc (docs/10-dataset-piese.md).
#
# Diametrele stau sub Ø110 și peste Ø50: sub ~Ø8 nu se mai distinge nimic la rezoluția de
# inspecție, iar peste Ø110 piesa nu mai intră în universal. Familiile mici (≤ Ø72) încap
# între fălcile de 83 mm ale prinzătorului, deci acolo prinderea poate fi reală.
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
    def teeth(self) -> int:
        return self.nominal.teeth

    @property
    def diameter_mm(self) -> float:
        return 2.0 * self.nominal.outer_radius

    @property
    def thickness_mm(self) -> float:
        return self.nominal.thickness

    @property
    def has_keyway(self) -> bool:
        return self.nominal.keyway

    def defect_classes(self) -> tuple[str, ...]:
        """Clasele care au sens pe familia asta.

        Pana dispărută nu are sens pe o piesă fără pană: ar da exact piesa nominală a
        familiei, adică o etichetă de rebut pe o geometrie corectă.
        """
        if self.has_keyway:
            return DEFECT_CLASSES
        return tuple(item for item in DEFECT_CLASSES if item != DEFECT_KEYWAY_MISSING)

    def as_dict(self) -> dict[str, Any]:
        """Reprezentare serializabilă: intră în manifest, lângă eticheta fiecărei piese."""
        return {
            "name": self.name,
            "description": self.description,
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
        name="flange_72",
        description="flansa: Ø72, 24 dinți mărunți, alezaj Ø28 fără pană, 3 găuri Ø6",
        nominal=GearParams(
            outer_radius=36.0,
            root_radius=30.0,
            bore_radius=14.0,
            keyway=False,
            keyway_depth=0.0,
            teeth=24,
            tooth_tip_width_deg=5.0,
            tooth_root_width_deg=9.0,
            thickness=10.0,
            bolt_count=3,
            bolt_circle_radius=24.0,
            hole_radius=3.0,
        ),
    ),
    PartFamily(
        name="bushing_68",
        description="bucșa: Ø68, 20 dinți, alezaj Ø30 fără pană, 3 găuri Ø7",
        nominal=GearParams(
            outer_radius=34.0,
            root_radius=28.0,
            bore_radius=15.0,
            keyway=False,
            keyway_depth=0.0,
            teeth=20,
            thickness=14.0,
            bolt_count=3,
            bolt_circle_radius=22.0,
            hole_radius=3.5,
        ),
    ),
    PartFamily(
        name="wheel_60",
        description="roata mică: Ø60, 18 dinți, alezaj Ø20 cu pană 6 × 3,5, 5 găuri Ø5",
        nominal=GearParams(
            outer_radius=30.0,
            root_radius=24.0,
            bore_radius=10.0,
            keyway_width=6.0,
            keyway_depth=3.5,
            teeth=18,
            thickness=15.0,
            bolt_count=5,
            bolt_circle_radius=18.5,
            hole_radius=2.5,
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
    outer = _toothed_outline(params)
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
