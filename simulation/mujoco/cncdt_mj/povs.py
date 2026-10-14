"""Din ce camere se strânge datasetul de antrenare: patru puncte de vedere, două feluri de momente.

Camerele sunt cele din plan: câte una pe flanșa fiecărui braț (prinderea), una fixă deasupra benzii
2 (cadrul de proces al piesei mașinate) și camera liberă, pentru cadrul general al celulei. Camera de
pe brațul 2 este cea care dă **verdictul de calitate**: ea vede piesa ținută în falci, de aproape.
Nu se adaugă un al doilea post de decizie — rolul `sorting` din `cnc.qa.snapshot.v1.json` este al
cadrului care decide ruta piesei, iar aici acela este cadrul de pe flanșa brațului 2.

Cadrul se ia la **două feluri de momente**, fiindcă nu toate camerele privesc un punct de lucru:

* la un punct de lucru, imediat după ce brațul s-a așezat pe poziția de jos (``stages``);
* când piesa mașinată trece prin dreptul camerei fixe, pe bandă (``belt_x_m``).

Modulul este **pur** — nume de cameră, rezoluții, momente, roluri din contract —, deci se importă
fără MuJoCo: randarea stă în `cncdt_mj/camera.py`, iar sesiunea în `run.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import layout
from . import moves

#: Numele momentului de captură de pe bandă, când piesa trece prin dreptul camerei fixe.
ON_BELT = "on_belt"

#: Rezoluția cadrelor de proces: aceeași cu a camerei de inspecție din plan, ca două cadre să se
#: poată pune unul peste altul la comparație (`docs/11-geaman-mujoco.md` §2).
PROCESS_FRAME = layout.INSPECTION_CAMERA_FRAME

#: Cadrul general al celulei: mai lat, ca să încapă celula întreagă.
CELL_FRAME = (1280, 960)

#: Rolurile din `telemetry/schemas/cnc.qa.snapshot.v1.json`. Verdictul care decide ruta piesei este
#: al cadrului `sorting`; cadrul `inspection` rămâne pentru comparație și pentru dataset.
ROLE_SORTING = "sorting"
ROLE_INSPECTION = "inspection"


@dataclass(frozen=True)
class Pov:
    """Un punct de vedere: ce cameră, la ce rezoluție, la ce moment și cu ce rol în contract."""

    name: str
    label: str

    #: Numele camerei din scenă; ``None`` înseamnă camera liberă, așezată pe un cadru știut.
    camera: str | None

    width: int
    height: int

    #: Punctele de lucru la care se ia cadrul, după ce brațul s-a așezat pe poziția de jos.
    stages: tuple[str, ...] = ()

    #: Ori cota din lungul benzii 2 la care se ia cadrul, când piesa trece prin dreptul camerei fixe.
    belt_x_m: float | None = None

    #: Rolul cadrului în contractul de inspecție, dacă rândul lui intră în `snapshots.jsonl`.
    role: str | None = None

    @property
    def resolution(self) -> tuple[int, int]:
        """Rezoluția cadrului, ca pereche."""
        return (self.width, self.height)


#: Registrul punctelor de vedere. Ordinea este ordinea în care se scriu în documentație.
POVS: tuple[Pov, ...] = (
    Pov("arm1_wrist", "camera de pe flanșa brațului 1: blocul brut în falci și piesa în universal",
        "arm1_wrist_camera", *PROCESS_FRAME, stages=("belt1_pick", "cnc_chuck")),
    Pov("arm2_wrist", "camera de pe flanșa brațului 2: verdictul de calitate, piesa ținută în falci",
        "arm2_wrist_camera", *PROCESS_FRAME, stages=("belt2_pick",), role=ROLE_SORTING),
    Pov("process", "camera fixă de deasupra benzii 2: cadrul de proces al piesei mașinate",
        "camera_quality", *PROCESS_FRAME, belt_x_m=float(layout.CAMERAS[0]["x"]),
        role=ROLE_INSPECTION),
    Pov("cell", "cadrul general al celulei, din camera liberă: piesa în locul familiei ei",
        None, *CELL_FRAME, stages=tuple(layout.rack_target(family)
                                       for family in layout.RACK_FAMILIES)),
)


def pov(name: str) -> Pov:
    """Punctul de vedere cu numele dat; eroarea listează numele cunoscute."""
    for item in POVS:
        if item.name == name:
            return item
    raise KeyError(f"punct de vedere inexistent: {name}; există {[item.name for item in POVS]}")


def at_stage(stage: str) -> tuple[Pov, ...]:
    """Punctele de vedere care se fotografiază la un punct de lucru, în ordinea registrului."""
    return tuple(item for item in POVS if stage in item.stages)


def on_belt() -> tuple[Pov, ...]:
    """Punctele de vedere care se fotografiază când piesa trece pe bandă prin dreptul camerei."""
    return tuple(item for item in POVS if item.belt_x_m is not None)


def frames_per_cycle(family: str) -> tuple[tuple[str, Pov], ...]:
    """Cadrele unui ciclu, în ordinea în care se iau: momentul, apoi punctul de vedere.

    Ordinea momentelor este ordinea ciclului brațelor (`moves.route_names`), ca planul capturii să
    nu rămână în urmă dacă ruta se schimbă; locul din raft depinde de familie. Cadrul de pe bandă se
    strecoară între brațe: acolo piesa mașinată trece prin dreptul camerei fixe, în drumul ei de la
    cota la care a lăsat-o brațul 1 până la linia de preluare a brațului 2.
    """
    plan: list[tuple[str, Pov]] = []
    for index, arm in enumerate(moves.ROUTES):
        if index:
            plan.extend((ON_BELT, item) for item in on_belt())
        for stage in moves.route_names(arm, family):
            plan.extend((stage, item) for item in at_stage(stage))
    return tuple(plan)
