#!/usr/bin/env python3
"""Construiește scena celulei CNC din asset-uri reale, la căile cerute de extensie.

Scena are două straturi:

1. **contractul** — prim-urile pe care le caută extensia (`/World/CNC/Spindle`, `/World/Robot1`,
   `/World/Conveyor1`, `/World/Conveyor2`, `/World/Part`, `/World/QA/Camera`,
   `/World/QA/DiverterCamera`, `/World/Scrap/Piston`), cu atributele `cncdt:process:*` pe ax;
2. **decorul** — asset-urile Isaac Sim (hala, manipulatorul, benzile) plus mașina CNC, care nu
   există în conținut și se compune din primitive.

De ce primitive pentru mașina CNC: în conținutul Isaac Sim nu există nicio mașină CNC, doar
roboți, benzi, props de depozit și hale. Mașina reală se aduce ulterior ca USD din CAD
(FreeCAD/Onshape) sau din STEP, iar scriptul este idempotent, deci își păstrează referințele
și atributele când îl rulați din nou.

Trei moduri de rulare:

1. **În Isaac Sim, pe scena deschisă** (Script Editor):

       exec(open("/sim/scripts/build-scene.py").read())
       build_scene_on_open_stage()

2. **Ca script independent**, care scrie un fișier USD nou (în container, fără Kit):

       CNCDT_ASSETS_ROOT=/isaac-assets \\
       ./python.sh /sim/scripts/build-scene.py --usd /logs/cell.usd --assets

   Fără Kit, referințele se compun, dar fizica și OmniGraph-ul nu sunt disponibile. Pentru
   banda care transportă piesa și pentru nodurile de conveyor, rulați apoi
   `conveyors-and-diverter.py` din Script Editor (vezi `docs/09-scena-celulei-cnc.md`).

3. **Fără asset-uri**, doar scheletul cu primitive (util când nu există oglinda de conținut):

       ./python.sh /sim/scripts/build-scene.py --usd /logs/skeleton.usd
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics
except ImportError:  # pragma: no cover - se întâmplă doar fără USD instalat.
    print(
        "pxr (USD) lipsește. Rulați în Isaac Sim ori cu PYTHONPATH către\n"
        "/isaac-sim/extscache/omni.usd.libs-*/ și LD_LIBRARY_PATH către .../bin:.../deps.",
        file=sys.stderr,
    )
    raise

# ---------------------------------------------------------------- contract de scenă
# Căile pe care le rezolvă `CANDIDATE_PATHS` din extensie. Sunt singurele căi pe care
# o schimbare de scenă trebuie să le respecte.
SPINDLE_PATH = "/World/CNC/Spindle"
ROBOT_PATH = "/World/Robot1"
PISTON_PATH = "/World/Scrap/Piston"
CAMERA_PATH = "/World/QA/Camera"
# Camera de la zona lamei/piston. Cadrul ei este cel care decide ruta piesei: pleacă spre
# endpoint-ul de viziune, iar verdictul ridică ori retrage opritorul.
DIVERTER_CAMERA_PATH = "/World/QA/DiverterCamera"
PART_PATH = "/World/Part"
CONVEYOR_INFEED_PATH = "/World/Conveyor1"
CONVEYOR_INSPECT_PATH = "/World/Conveyor2"

# Atributele de proces citite de extensie, cu valorile de referință din generatorul sintetic.
# Doar spindleLoad și vibration sunt rescrise de extensie; restul rămân la valoarea autorată
# până când le animă cineva.
PROCESS_ATTRIBUTES: dict[str, float] = {
    "cncdt:process:spindleLoad": 0.58,
    "cncdt:process:vibration": 1.35,
    "cncdt:process:temperature": 24.0,
    "cncdt:process:coolantFlow": 8.5,
    "cncdt:process:axisFeed": 0.0,
}

# Decalajul pistonului, identic cu `PISTON_PUSH_OFFSET` din extensie. Extensia îl adaugă pe
# `xformOp:translate` **local**, pe Z, deci deviatorul este o poartă care ridică 0,12 m un
# opritor în calea piesei: la repaus opritorul stă la nivelul benzii, la REJECT iese 0,12 m
# deasupra ei. Nu rotim pistonul: mișcarea pe verticală este exact ce știe extensia.
PISTON_PUSH_OFFSET = 0.12

# ---------------------------------------------------------------- asset-uri
# Rădăcina conținutului: oglinda locală din instanță (vezi `make sim-assets`). Browserele de
# asset-uri citesc din același loc, deci referințele din scenă și lista din browser coincid.
ASSETS_ROOT = os.environ.get("CNCDT_ASSETS_ROOT", "/isaac-assets")
ASSET_ISAAC = f"{ASSETS_ROOT}/Isaac"
WAREHOUSE_ASSET = f"{ASSET_ISAAC}/Environments/Simple_Warehouse/warehouse.usd"
# `Legacy/ur10_short_suction.usd` este singurul UR10 din conținut cu rădăcina de articulație pe
# prim-ul rădăcină (varianta modernă o pune pe `root_joint`, ceea ce merge la fel, dar
# contractul cere ca `/World/Robot1` să fie chiar rădăcina de articulație). Venuză cu care vine
# se dezactivează, iar în locul ei se montează griperul cu falci.
ROBOT_ASSET = f"{ASSET_ISAAC}/Robots/UniversalRobots/ur10/Legacy/ur10_short_suction.usd"
# Griperul cu două falci al UR10, din folderul propriu al robotului, plus alternativa Robotiq.
GRIPPER_ASSET = f"{ASSET_ISAAC}/Robots/UniversalRobots/ur10/grippers/short_gripper.usd"
GRIPPER_FALLBACK_ASSET = f"{ASSET_ISAAC}/Robots/Robotiq/2F-85/2f85.usd"
# Numele posibile ale flanșei pe care se montează griperul și ale sculei pe care o înlocuiește.
# Se încearcă în ordine, pentru că numele diferă între variantele de UR10 din conținut.
WRIST_LINK_CANDIDATES = ("wrist_3_link", "tool0", "ee_link", "flange", "wrist_3")
TOOL_LINK_CANDIDATES = ("suction_link", "short_suction", "suction_gripper", "vacuum_gripper")
# Piesa este ținută de o articulație fixă, creată de extensie la prindere (vezi
# `docs/09-scena-celulei-cnc.md`, secțiunea despre manipulator).
GRASP_PATH = "/World/Robot1/GraspJoint"
# Piesele A05 sunt role drepte, plate, de 2 m (înălțime 1,17 m, nivel 0 în setul de date al
# extensiei de conveyor). A06 ar fi o bandă cu nivel 1 (1,76 m), prea sus pentru o celulă de
# prelucrare. Numele prim-ului de acționare vin din `track_types.json`: `Rollers`.
CONVEYOR_ASSET = f"{ASSET_ISAAC}/Props/Conveyors/ConveyorBelt_A05.usd"

# ---------------------------------------------------------------- dimensiuni
# Toate în metri, în sistemul halei (Z în sus, +X sensul de curgere al pieselor).
BELT_DECK_Z = 1.09  # suprafața rolelor la A05 (înălțimea totală a piesei este 1,17 m)
BELT_WIDTH = 1.16
PART_SIZE = (0.16, 0.16, 0.10)
MACHINE_SIZE = (1.9, 1.6, 0.95)  # batiu
MACHINE_TABLE_Z = 0.95
MACHINE_TABLE_SIZE = (1.0, 0.7, 0.10)
MACHINE_COLUMN_SIZE = (0.22, 0.22, 1.35)
MACHINE_BEAM_SIZE = (1.5, 0.22, 0.22)
SPINDLE_RADIUS = 0.09
SPINDLE_HEIGHT = 0.42

# Centrul celulei: golful dintre pereții halei de la y = -9 și y = -3 (fără rafturi, fără
# palete, doar pardoseală, tavan și lămpi la 5,83 m). Vezi `docs/09-scena-celulei-cnc.md`.
CELL_ORIGIN = (0.0, -6.0, 0.0)

# Așezarea în celulă, în coordonate locale (X = sensul de curgere):
#   banda de intrare -> manipulator -> mașina CNC -> banda de inspecție -> poartă -> lăzi
INFEED_X = -4.6
ROBOT_X = -1.9
MACHINE_X = 0.7
INSPECT_X = 3.9
# Poarta stă chiar la capătul benzii de inspecție (banda A05 are 2 m, deci se termină la 5,9).
DIVERTER_X = 6.0
# Poarta are lame de 0,30 m în lungul lui Y, centrate pe traseul piesei.
GATE_HALF_WIDTH = 0.15
GATE_HEIGHT = 0.16
SCRAP_BIN_XY = (7.2, -1.7)
GOOD_BIN_XY = (7.2, 0.9)


def _log(message: str) -> None:
    try:  # pragma: no cover - disponibil doar în Kit.
        import carb  # type: ignore

        carb.log_info(f"[build-scene] {message}")
    except Exception:
        print(f"[build-scene] {message}", flush=True)


def world(local: tuple[float, float, float]) -> Gf.Vec3d:
    """Transformă o poziție din sistemul celulei în sistemul halei."""
    return Gf.Vec3d(
        CELL_ORIGIN[0] + local[0],
        CELL_ORIGIN[1] + local[1],
        CELL_ORIGIN[2] + local[2],
    )


def _xform(stage: Usd.Stage, path: str) -> Usd.Prim:
    return UsdGeom.Xform.Define(stage, path).GetPrim()


def _set_translate(prim: Usd.Prim, value: Gf.Vec3d) -> None:
    xformable = UsdGeom.Xformable(prim)
    for op in xformable.GetOrderedXformOps():
        if op.GetOpName() == "xformOp:translate":
            op.Set(value)
            return
    xformable.AddTranslateOp().Set(value)


def _set_rotate_xyz(prim: Usd.Prim, value: tuple[float, float, float]) -> None:
    xformable = UsdGeom.Xformable(prim)
    for op in xformable.GetOrderedXformOps():
        if op.GetOpName() == "xformOp:rotateXYZ":
            op.Set(Gf.Vec3f(*value))
            return
    xformable.AddRotateXYZOp().Set(Gf.Vec3f(*value))


def _cube(
    stage: Usd.Stage,
    path: str,
    size: tuple[float, float, float],
    local_translate: tuple[float, float, float],
    color: tuple[float, float, float],
) -> Usd.Prim:
    """Cub cu dimensiunile date (metri), așezat în sistemul celulei."""
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    cube.AddTranslateOp().Set(world(local_translate))
    cube.AddScaleOp().Set(Gf.Vec3f(*size))
    cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    return cube.GetPrim()


def _cylinder(
    stage: Usd.Stage,
    path: str,
    radius: float,
    height: float,
    local_translate: tuple[float, float, float],
    color: tuple[float, float, float],
) -> Usd.Prim:
    cylinder = UsdGeom.Cylinder.Define(stage, path)
    cylinder.CreateRadiusAttr(radius)
    cylinder.CreateHeightAttr(height)
    cylinder.CreateAxisAttr(UsdGeom.Tokens.z)
    cylinder.AddTranslateOp().Set(world(local_translate))
    cylinder.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    return cylinder.GetPrim()


def _author_float(prim: Usd.Prim, name: str, value: float) -> bool:
    """Autorează un atribut Float dacă nu are deja o valoare. Întoarce True dacă a scris."""
    existing = prim.GetAttribute(name)
    if existing and existing.IsValid():
        if existing.Get() is not None:
            return False
        existing.Set(float(value))
        return True
    attribute = prim.CreateAttribute(name, Sdf.ValueTypeNames.Float)
    attribute.Set(float(value))
    return True


def _reference(stage: Usd.Stage, path: str, asset_path: str, prim_path: str | None = None) -> bool:
    """Adaugă o referință la un asset, dacă nu există deja. Întoarce True dacă a adăugat-o."""
    prim = _xform(stage, path)
    references = prim.GetReferences()
    for existing in references.GetAddedOrExplicitItems():
        if isinstance(existing, Sdf.Reference) and existing.assetPath == asset_path:
            return False
    if prim_path:
        references.AddReference(asset_path, prim_path)
    else:
        references.AddReference(asset_path)
    return True


def _remove_children(prim: Usd.Prim) -> None:
    """Șterge copiii direcți ai unui prim (marcajele înlocuite de un asset real)."""
    stage = prim.GetStage()
    for child in prim.GetChildren():
        stage.RemovePrim(child.GetPath())


def _find_descendant(root: Usd.Prim, names: tuple[str, ...]) -> Usd.Prim | None:
    """Caută în subarbore prim-ul cu unul dintre numele date (asset-urile își schimbă numele)."""
    for child in Usd.PrimRange(root):
        if child.GetName() in names:
            return child
    return None


def _prepare_stage(stage: Usd.Stage) -> None:
    """Setează unitățile și prim-ul implicit, ca scena să se comporte ca în Isaac Sim."""
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world_prim = _xform(stage, "/World")
    if stage.GetDefaultPrim() is None or not stage.GetDefaultPrim().IsValid():
        stage.SetDefaultPrim(world_prim)


def _author_physics_scene(stage: Usd.Stage) -> None:
    """Scena de fizică: referința la hală nu o aduce (este în afara prim-ului implicit)."""
    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0))
    scene.CreateGravityMagnitudeAttr(9.81)


def _build_warehouse(stage: Usd.Stage, with_assets: bool) -> bool:
    if not with_assets or not os.path.exists(WAREHOUSE_ASSET):
        _log(f"hala lipsește ({WAREHOUSE_ASSET}); scena rămâne fără decor")
        return False
    reference = _reference(stage, "/World/Warehouse", WAREHOUSE_ASSET)
    _log(f"hală: {WAREHOUSE_ASSET}")
    return reference


def _build_machine(stage: Usd.Stage) -> None:
    """Mașina CNC: nu există în conținutul Isaac Sim, deci o compunem din primitive.

    Forma urmează un centru de prelucrare vertical: batiu, masă, două coloane, grindă, ax
    (cilindrul care poartă atributele de proces), panou de comandă și un perete din spate.
    Când apare CAD-ul real, se șterge conținutul lui `/World/CNC` mai puțin `Spindle`, iar
    referința CAD se pune pe `/World/CNC/Frame`.
    """
    _xform(stage, "/World/CNC")
    _xform(stage, "/World/CNC/Frame")

    width, depth, height = MACHINE_SIZE
    _cube(
        stage,
        "/World/CNC/Frame/Base",
        (width, depth, height),
        (MACHINE_X, 0.0, height / 2.0),
        (0.42, 0.44, 0.47),
    )
    _cube(
        stage,
        "/World/CNC/Frame/Back",
        (width, 0.10, 1.5),
        (MACHINE_X, -depth / 2.0 + 0.05, height + 0.75),
        (0.36, 0.38, 0.41),
    )
    for index, offset in enumerate((-0.55, 0.55)):
        _cube(
            stage,
            f"/World/CNC/Frame/Column{index + 1}",
            MACHINE_COLUMN_SIZE,
            (MACHINE_X + offset, 0.0, height + MACHINE_COLUMN_SIZE[2] / 2.0),
            (0.5, 0.52, 0.55),
        )
    _cube(
        stage,
        "/World/CNC/Frame/Beam",
        MACHINE_BEAM_SIZE,
        (MACHINE_X, 0.0, height + MACHINE_COLUMN_SIZE[2] + MACHINE_BEAM_SIZE[2] / 2.0),
        (0.5, 0.52, 0.55),
    )
    _cube(
        stage,
        "/World/CNC/Frame/Table",
        MACHINE_TABLE_SIZE,
        (MACHINE_X, 0.0, MACHINE_TABLE_Z),
        (0.62, 0.64, 0.68),
    )
    _cube(
        stage,
        "/World/CNC/Frame/ControlPanel",
        (0.30, 0.12, 0.45),
        (MACHINE_X + width / 2.0 + 0.20, 0.45, height + 0.30),
        (0.15, 0.16, 0.18),
    )

    # Axul: cilindrul care coboară din grindă spre masă. Aici scrie extensia degradarea, iar
    # aici citește atributele de proces.
    spindle = _xform(stage, SPINDLE_PATH)
    _cylinder(
        stage,
        f"{SPINDLE_PATH}/Body",
        radius=SPINDLE_RADIUS,
        height=SPINDLE_HEIGHT,
        local_translate=(MACHINE_X, 0.0, MACHINE_TABLE_Z + 0.45),
        color=(0.78, 0.79, 0.82),
    )
    _cube(
        stage,
        f"{SPINDLE_PATH}/Tool",
        (0.06, 0.06, 0.16),
        (MACHINE_X, 0.0, MACHINE_TABLE_Z + 0.14),
        (0.25, 0.25, 0.28),
    )
    authored = [name for name, value in PROCESS_ATTRIBUTES.items() if _author_float(spindle, name, value)]
    if authored:
        _log(f"atribute de proces autorate pe ax: {', '.join(sorted(authored))}")


def _strip_nested_articulation(root: Usd.Prim) -> None:
    """Scoate API-ul de rădăcină de articulație din subarborele unui asset atașat.

    Brațul este deja o articulație, iar PhysX acceptă o singură rădăcină pe ierarhie. Asset-ul
    griperului se supune brațului, ca articulația citită de extensie să rămână cea a brațului.
    """
    for prim in Usd.PrimRange(root):
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            prim.RemoveAPI(UsdPhysics.ArticulationRootAPI)
            _log(f"rădăcină de articulație scoasă de pe {prim.GetPath()}")


def _build_gripper(stage: Usd.Stage, robot: Usd.Prim, with_assets: bool) -> str:
    """Montează griperul pe flanșa brațului și dezactivează scula cu care vine brațul.

    Griperul este un asset separat, referit pe un prim copil al flanșei, deci se mișcă o dată
    cu brațul. Prinderea piesei nu stă în geometrie: la nevoie, extensia creează o articulație
    fixă între griper și piesă (``GRASP_PATH``), pentru că falcile nu au grade de libertate
    articulate în acest asset.

    Întoarce calea prim-ului griperului, sau șirul vid când nu există niciun asset.
    """
    tool = _find_descendant(robot, TOOL_LINK_CANDIDATES)
    if with_assets and tool is not None:
        # Rămâne un singur efector final: ventuza din asset iese din scenă.
        tool.SetActive(False)
        _log(f"sculă originală dezactivată: {tool.GetPath()}")

    flange = _find_descendant(robot, WRIST_LINK_CANDIDATES)
    if flange is None:
        flange = robot
        _log("flanșa nu a fost găsită în asset; griperul se montează pe rădăcina brațului")

    gripper_path = f"{flange.GetPath()}/Gripper"
    if not with_assets:
        cube = UsdGeom.Cube.Define(stage, f"{gripper_path}/Placeholder")
        cube.CreateSizeAttr(1.0)
        cube.AddScaleOp().Set(Gf.Vec3f(0.10, 0.18, 0.22))
        cube.CreateDisplayColorAttr([Gf.Vec3f(0.90, 0.60, 0.20)])
        return gripper_path

    asset = GRIPPER_ASSET if os.path.exists(GRIPPER_ASSET) else GRIPPER_FALLBACK_ASSET
    if not os.path.exists(asset):
        _log(f"griperul lipsește ({GRIPPER_ASSET}); brațul rămâne cu scula din asset")
        tool.SetActive(True)
        return ""
    if _reference(stage, gripper_path, asset):
        _log(f"griper: {asset} pe {flange.GetPath()}")
    _strip_nested_articulation(stage.GetPrimAtPath(gripper_path))
    return gripper_path


def _build_robot(stage: Usd.Stage, with_assets: bool) -> str:
    """Manipulatorul: UR10 cu griper cu falci, la intrarea în celulă.

    Contractul cere ca `/World/Robot1` să fie rădăcină de articulație, iar asset-ul
    `Legacy/ur10_short_suction.usd` o are pe prim-ul rădăcină. Fără asset, rămâne un cub-marcaj,
    iar extensia citește eforturile articulațiilor doar din semnalul sintetic.

    Întoarce calea griperului (poate fi vidă).
    """
    prim = _xform(stage, ROBOT_PATH)
    if with_assets and os.path.exists(ROBOT_ASSET):
        if _reference(stage, ROBOT_PATH, ROBOT_ASSET):
            _remove_children(prim)
            _log(f"manipulator: {ROBOT_ASSET}")
        # Brațul privește spre axul mașinii (parked pose din asset este orientat pe +X).
        _set_translate(prim, world((ROBOT_X, 0.0, 0.0)))
        return _build_gripper(stage, prim, with_assets=True)

    _remove_children(prim)
    _cube(
        stage,
        f"{ROBOT_PATH}/Placeholder",
        (0.30, 0.30, 0.80),
        (ROBOT_X, 0.0, 0.40),
        (0.85, 0.55, 0.20),
    )
    return _build_gripper(stage, prim, with_assets=False)


def _build_conveyors(stage: Usd.Stage, with_assets: bool) -> None:
    """Banda de intrare și banda de inspecție.

    Piesele `ConveyorBelt_A05` sunt role drepte de 2 m, cu punctul de intrare în origine,
    aliniate pe X. Nodul de conveyor (care chiar mișcă piesa) se adaugă din Script Editor, cu
    `conveyors-and-diverter.py`; fără el, banda este doar decor fizic.
    """
    for path, local_x, name in (
        (CONVEYOR_INFEED_PATH, INFEED_X, "intrare"),
        (CONVEYOR_INSPECT_PATH, INSPECT_X, "inspecție"),
    ):
        prim = _xform(stage, path)
        if with_assets and os.path.exists(CONVEYOR_ASSET):
            if _reference(stage, path, CONVEYOR_ASSET):
                _remove_children(prim)
                _log(f"bandă {name}: {CONVEYOR_ASSET}")
            _set_translate(prim, world((local_x, 0.0, 0.0)))
            continue

        _remove_children(prim)
        _cube(
            stage,
            f"{path}/Belt",
            (2.0, BELT_WIDTH, 0.12),
            (local_x, 0.0, BELT_DECK_Z - 0.06),
            (0.20, 0.20, 0.22),
        )


def _build_part(stage: Usd.Stage) -> None:
    """Piesa, pe banda de inspecție, în câmpul camerei QA.

    Piesa este **kinematică**, nu dinamică: nu cade prin decor cât timp banda nu are nodul de
    conveyor care o transportă, dar are corp rigid și collider, deci poate fi prinsă de griper
    printr-o articulație fixă și poate fi mutată de un script. Când banda primește nodul de
    conveyor, se poate comuta pe dinamic (`kinematicEnabled = false`).
    """
    _xform(stage, PART_PATH)
    body = _cube(
        stage,
        f"{PART_PATH}/Body",
        PART_SIZE,
        (INSPECT_X, 0.0, BELT_DECK_Z + PART_SIZE[2] / 2.0),
        (0.72, 0.76, 0.80),
    )
    if not body.HasAPI(UsdPhysics.RigidBodyAPI):
        rigid = UsdPhysics.RigidBodyAPI.Apply(body)
        rigid.CreateKinematicEnabledAttr(True)
        UsdPhysics.CollisionAPI.Apply(body)
        mass = UsdPhysics.MassAPI.Apply(body)
        mass.CreateMassAttr(0.32)


def _define_camera(
    stage: Usd.Stage,
    path: str,
    translate: tuple[float, float, float],
    focal_length: float = 24.0,
) -> None:
    """Definește o cameră orientată în jos spre bandă.

    Toate camerele celulei folosesc aceeași convenție de orientare (90° în jurul lui X),
    ca framingul să fie previzibil: poziția și distanța focală sunt singurele care diferă.
    """
    if stage.GetPrimAtPath(path).IsValid():
        return
    camera = UsdGeom.Camera.Define(stage, path)
    camera.CreateFocalLengthAttr(focal_length)
    camera.CreateClippingRangeAttr(Gf.Vec2f(0.05, 1000.0))
    camera.AddTranslateOp().Set(world(translate))
    camera.AddRotateXYZOp().Set(Gf.Vec3f(90.0, 0.0, 0.0))


def _build_camera(stage: Usd.Stage) -> None:
    """Camerele celulei: inspecția de la ieșirea din mașină și zona lamei/piston.

    Camera de la lamă stă mai sus și are un obiectiv mai scurt (18 mm), ca în cadru să intre
    piesa oprită, lama și gura jgheabului de rebut. Framingul se verifică cu ochii în Isaac
    Sim: `Capture QA acum` din panou scrie un cadru pe disc.
    """
    _xform(stage, "/World/QA")
    _define_camera(stage, CAMERA_PATH, (INSPECT_X, 0.0, BELT_DECK_Z + 1.05))
    _define_camera(
        stage,
        DIVERTER_CAMERA_PATH,
        (DIVERTER_X - 0.5, 0.0, BELT_DECK_Z + 1.15),
        focal_length=18.0,
    )


def _build_diverter(stage: Usd.Stage) -> None:
    """Poarta care decide între piese bune și rebut.

    Metoda: verdictul QA ajunge la extensie pe topicul `qa-verdict`; extensia apelează
    pistonul, iar pistonul urcă opritorul cu 0,12 m (`PISTON_PUSH_OFFSET`). Opritorul stă la
    repaus cu partea de sus la nivelul benzii (`BELT_DECK_Z`) și iese în calea piesei doar la
    REJECT. O lamă fixă, înclinată, ghidează piesa oprită spre jgheabul și lada de rebut, în
    timp ce piesele acceptate trec mai departe în lada de piese bune.

    Varianta „banda decide singură", fără opritor, este joncțiunea `ConveyorBelt_A20`
    (Y_MERGE, două trasee de role independente) — vezi `conveyors-and-diverter.py`.
    """
    _xform(stage, "/World/Scrap")
    piston = _xform(stage, PISTON_PATH)
    gate_top = BELT_DECK_Z - GATE_HEIGHT / 2.0
    _set_translate(piston, world((DIVERTER_X, 0.0, gate_top)))
    _cube(
        stage,
        f"{PISTON_PATH}/Rod",
        (0.08, 0.08, 0.28),
        (DIVERTER_X, 0.0, gate_top - 0.14),
        (0.80, 0.22, 0.22),
    )
    _cube(
        stage,
        f"{PISTON_PATH}/Blade",
        (0.20, 2.0 * GATE_HALF_WIDTH, GATE_HEIGHT),
        (DIVERTER_X, 0.0, gate_top),
        (0.62, 0.64, 0.68),
    )

    if not stage.GetPrimAtPath("/World/Scrap/Guide").IsValid():
        # Lama fixă: 1,6 m, rotită -35° în jurul lui Z, de la poartă spre gura jgheabului.
        guide = _xform(stage, "/World/Scrap/Guide")
        _set_rotate_xyz(guide, (0.0, 0.0, -35.0))
        _set_translate(guide, world((DIVERTER_X + 0.65, -0.65, BELT_DECK_Z + 0.02)))
        _cube(
            stage,
            "/World/Scrap/Guide/Plate",
            (1.6, 0.08, 0.22),
            (0.0, 0.0, 0.0),
            (0.58, 0.60, 0.64),
        )

    for name, local, size, color in (
        ("/World/Scrap/Chute", (SCRAP_BIN_XY[0] - 0.1, SCRAP_BIN_XY[1] + 0.5, 0.72), (0.9, 0.9, 0.08), (0.45, 0.46, 0.50)),
        ("/World/Scrap/Bin", (SCRAP_BIN_XY[0], SCRAP_BIN_XY[1], 0.35), (0.9, 0.8, 0.70), (0.60, 0.24, 0.24)),
        ("/World/GoodParts/Bin", (GOOD_BIN_XY[0], GOOD_BIN_XY[1], 0.35), (0.9, 0.8, 0.70), (0.24, 0.52, 0.30)),
    ):
        if not stage.GetPrimAtPath(name).IsValid():
            _cube(stage, name, size, local, color)


def build(stage: Usd.Stage, with_assets: bool = True) -> list[str]:
    """Construiește scena. Întoarce lista căilor din contract create sau deja existente."""
    _prepare_stage(stage)
    _author_physics_scene(stage)
    _build_warehouse(stage, with_assets)
    _build_machine(stage)
    gripper_path = _build_robot(stage, with_assets)
    _build_conveyors(stage, with_assets)
    _build_part(stage)
    _build_camera(stage)
    _build_diverter(stage)

    contract = [
        SPINDLE_PATH,
        ROBOT_PATH,
        CONVEYOR_INFEED_PATH,
        CONVEYOR_INSPECT_PATH,
        PART_PATH,
        CAMERA_PATH,
        DIVERTER_CAMERA_PATH,
        PISTON_PATH,
    ]
    # Griperul stă pe flanșa brațului, al cărei nume vine din asset, deci calea se adaugă
    # doar când a fost găsită.
    if gripper_path:
        contract.append(gripper_path)
    missing = [path for path in contract if not stage.GetPrimAtPath(path).IsValid()]
    if missing:
        _log(f"ATENȚIE: căi lipsă din contract: {', '.join(missing)}")
    else:
        _log(f"contract complet: {len(contract)} căi")
    return contract


def describe() -> str:
    """Rezumatul așezării, pentru jurnal și pentru documentație."""
    return (
        "celula CNC (coordonate locale, X = sensul de curgere)\n"
        f"  hala          {WAREHOUSE_ASSET}\n"
        f"  banda intrare x={INFEED_X:+.1f}  (role A05, suprafața la {BELT_DECK_Z:.2f} m)\n"
        f"  manipulator   x={ROBOT_X:+.1f}  UR10 cu griper\n"
        f"  mașina CNC    x={MACHINE_X:+.1f}  primitive (batiu, masă, coloane, ax)\n"
        f"  banda inspecție x={INSPECT_X:+.1f} + camera QA deasupra\n"
        f"  poartă rebut  x={DIVERTER_X:+.1f}  opritor care urcă {PISTON_PUSH_OFFSET:.2f} m; "
        f"lada de rebut la (x={SCRAP_BIN_XY[0]:.1f}, y={SCRAP_BIN_XY[1]:.1f}), "
        f"piese bune la (x={GOOD_BIN_XY[0]:.1f}, y={GOOD_BIN_XY[1]:.1f})\n"
        f"  camera lamei  x={DIVERTER_X - 0.5:+.1f}  obiectiv 18 mm, cadrul care decide ruta\n"
        f"  centrul celulei în hală: {CELL_ORIGIN}"
    )


def build_scene_on_open_stage(with_assets: bool = True) -> list[str]:
    """Varianta pentru Script Editor: construiește pe scena deschisă în Isaac Sim."""
    try:  # pragma: no cover - necesită Kit.
        import omni.usd  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "omni.usd lipsește; rulați în Script Editor sau folosiți --usd."
        ) from exc

    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("nu există un stage USD deschis")
    return build(stage, with_assets=with_assets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scena celulei CNC (contract + asset-uri).")
    parser.add_argument(
        "--usd",
        required=True,
        help="calea fișierului USD de scris (de exemplu /logs/cell.usd)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="suprascrie fișierul dacă există (implicit: îl completează)",
    )
    parser.add_argument(
        "--assets",
        dest="assets",
        action="store_true",
        default=None,
        help="folosește asset-urile din CNCDT_ASSETS_ROOT (implicit: dacă rădăcina există)",
    )
    parser.add_argument(
        "--no-assets",
        dest="assets",
        action="store_false",
        help="doar primitive, fără nicio referință la asset-uri",
    )
    args = parser.parse_args(argv)

    if args.force and os.path.exists(args.usd):
        os.remove(args.usd)

    with_assets = args.assets
    if with_assets is None:
        with_assets = os.path.isdir(ASSET_ISAAC)

    print(describe())
    print(f"asset-uri: {'da' if with_assets else 'nu'} (rădăcină {ASSETS_ROOT})")

    if os.path.exists(args.usd):
        stage = Usd.Stage.Open(args.usd)
    else:
        stage = Usd.Stage.CreateNew(args.usd)

    if stage is None:
        print(f"nu pot deschide sau crea {args.usd}", file=sys.stderr)
        return 1

    build(stage, with_assets=with_assets)
    stage.GetRootLayer().Save()
    print(f"scenă scrisă: {args.usd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
