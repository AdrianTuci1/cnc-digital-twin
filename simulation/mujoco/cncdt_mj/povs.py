"""Din ce camere se strânge datasetul de antrenare: cele de pe flanșe, plus cadrul general.

Camerele sunt cele din plan: câte una pe flanșa fiecărui braț (prinderea) și camera liberă, pentru
cadrul general al celulei. Camera de pe brațul 2 este cea care dă **verdictul de calitate**: ea
vede piesa ținută în falci, de aproape, și tot ea decide ruta piesei. Nu există un al doilea post
de decizie: rolul `sorting` din `cnc.qa.snapshot.v1.json` este al cadrului care decide ruta, iar
aici acela este cadrul de pe flanșa brațului 2.

Cadrul se ia la **punctele de lucru** ale ciclului, imediat după ce scula s-a așezat pe poziția de
jos (``stages``).

Modulul este **pur** — nume de cameră, rezoluții, momente, roluri din contract —, deci se importă
fără MuJoCo: randarea stă în `cncdt_mj/camera.py`, iar sesiunea în `run.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import layout
from . import moves

#: Rezoluția cadrelor de proces, de pe flanșele brațelor.
PROCESS_FRAME = (640, 480)

#: Cadrul general al celulei: mai lat, ca să încapă celula întreagă.
CELL_FRAME = (1280, 960)

#: Rolul din `telemetry/schemas/cnc.qa.snapshot.v1.json` al cadrului care decide ruta piesei.
ROLE_SORTING = "sorting"

#: Momentele secvenței de prindere, în ordinea în care le parcurge scula: urcarea deasupra
#: punctului, coborîrea pe el și ridicarea. Cadrele sînt pentru antrenarea prinderii, nu pentru
#: verdict: arată falcile apropiindu-se, închise pe piesă și ridicîndu-se.
GRASP_SUBSTAGES = ("sus", "jos", "ridicare")


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

    #: Rolul cadrului în contractul de inspecție, dacă rândul lui intră în `snapshots.jsonl`.
    role: str | None = None

    #: Punctele de lucru la care se fotografiază secvența de prindere, la fiecare moment din
    #: :data:`GRASP_SUBSTAGES`. Un punct de vedere de prindere nu are `stages`: el nu așteaptă
    #: așezarea pe punct, ci trece prin toate momentele mișcării.
    grasp_points: tuple[str, ...] = ()

    #: Umbrele aruncate în cadru. Cadrele de pe flanșe le opresc: silueta umbrei cheii pe bandă
    #: și umbra falcii pe piesă se citesc ca o a doua piesă și ca o față ciobită, iar verdictul
    #: are nevoie de suprafața piesei, nu de efecte de lumină.
    shadows: bool = True

    @property
    def resolution(self) -> tuple[int, int]:
        """Rezoluția cadrului, ca pereche."""
        return (self.width, self.height)


#: Registrul punctelor de vedere. Ordinea este ordinea în care se scriu în documentație.
POVS: tuple[Pov, ...] = (
    Pov("arm1_wrist", "camera de pe flanșa brațului 1: blocul brut în falci și piesa în universal",
        "arm1_wrist_camera", *PROCESS_FRAME, stages=("belt1_pick", "cnc_chuck"), shadows=False),
    Pov("arm2_wrist", "camera de pe flanșa brațului 2: verdictul de calitate, piesa ținută în falci",
        "arm2_wrist_camera", *PROCESS_FRAME, stages=("belt2_pick",), role=ROLE_SORTING,
        shadows=False),
    Pov("cell", "cadrul general al celulei, din camera liberă: piesa în locul familiei ei",
        None, *CELL_FRAME, stages=tuple(layout.rack_target(family)
                                       for family in layout.RACK_FAMILIES)),
    Pov("arm1_grasp", "secvența de prindere a brațului 1: apropiere, piesa în falci, ridicare",
        "arm1_wrist_camera", *PROCESS_FRAME, grasp_points=("belt1_pick",), shadows=False),
    Pov("arm2_grasp", "secvența de prindere a brațului 2: apropiere, piesa în falci, ridicare",
        "arm2_wrist_camera", *PROCESS_FRAME, grasp_points=("belt2_pick",), shadows=False),
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


def at_grasp(point: str, substage: str) -> tuple[Pov, ...]:
    """Punctele de vedere ale secvenței de prindere, la un moment al unui punct de lucru.

    ``point`` este punctul de lucru (``belt1_pick``, ``belt2_pick``), iar ``substage`` unul din
    :data:`GRASP_SUBSTAGES`. Un moment necunoscut nu are cadre, în loc să ridice eroare: apelantul
    este bucla de mișcare, care poate trece prin alte momente fără să fotografieze.
    """
    if substage not in GRASP_SUBSTAGES:
        return ()
    return tuple(item for item in POVS if point in item.grasp_points)


def frames_per_cycle(family: str, grasp: bool = False) -> tuple[tuple[str, Pov], ...]:
    """Cadrele unui ciclu, în ordinea în care se iau: momentul, apoi punctul de vedere.

    Ordinea momentelor este ordinea ciclului brațelor (`moves.route_names`), ca planul capturii să
    nu rămână în urmă dacă ruta se schimbă; locul din raft depinde de familie. Cu ``grasp``,
    fiecare punct de preluare primește și cele trei momente ale secvenței de prindere.
    """
    plan: list[tuple[str, Pov]] = []
    for arm in moves.ROUTES:
        for stage in moves.route_names(arm, family):
            plan.extend((stage, item) for item in at_stage(stage))
            if grasp:
                for substage in GRASP_SUBSTAGES:
                    plan.extend((f"{stage}-{substage}", item)
                                for item in at_grasp(stage, substage))
    return tuple(plan)
