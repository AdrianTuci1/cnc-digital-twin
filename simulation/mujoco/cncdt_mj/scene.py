"""Compunerea scenei: `scene/cell.xml` plus cele două brațe Panda atașate programatic.

MJCF nu poate aduce singur modelele din `mujoco_menagerie`: atributul `<attach model="...">` nu
funcționează în MuJoCo 3.15.0 (verificat cu patru forme de cale), iar `modeldir` nu există pe
`<compiler>`. Deci scena se compune programatic, cu `MjSpec.attach`, iar `scene/cell.xml` ține doar
componentele celulei. Consecința utilă: același model se poate atașa de două ori, cu
`copy_during_attach`, iar prefixele de nume țin cele două brațe separate.

Modulul este singurul care importă `mujoco` (ADR 0012): `cncdt_mj.layout` și `cncdt_mj.part` rămân
pure, ca testele din `.venv`-ul proiectului să le poată importa fără motorul de fizică.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any, Iterator

import mujoco
import mujoco_menagerie
import numpy as np

from . import MUJOCO_DIR
from . import arms
from . import belt
from . import layout
from . import part

#: Modelul de braț, din registrul Menagerie; licența este Apache-2.0.
PANDA_MODEL = "franka_emika_panda"

#: Șablonul scenei, cu cifrele puse la locul lor de :func:`scene_template`.
CELL_XML = MUJOCO_DIR / "scene" / "cell.xml"

#: Brațele: prefixul numelor din scenă și corpul-mamă la care se atașează fiecare.
ARM_MOUNTS: tuple[tuple[str, str], ...] = (("arm1", "arm1_mount"), ("arm2", "arm2_mount"))

#: Camera de prindere de pe flanșa fiecărui braț: catalogul de modele nu o are, celula o adaugă.
#: Poziția e în sistemul ultimei verigi (``link7``), iar orientarea se calculează la compunere, ca
#: obiectivul să privească punctul de strângere. Așa cum vine din catalog — axele verigii, fără
#: rotire — camera privea **înapoi**, spre cot: punctul de strângere cădea la −143 mm adâncime,
#: adică în spatele obiectivului, iar cadrul arăta cotul. Măsurat cu `view.py --check`.
WRIST_CAMERA_OFFSET = (0.0, 0.0, 0.05)
WRIST_CAMERA_FOVY = 60.0

#: Pozițiile nominale ale brațelor, fitate în scenă, nu luate din plan și nu moștenite din
#: keyframe-ul copilului (care s-ar importa o dată pentru fiecare braț și s-ar ciocni). Criteriul
#: fitării este cel din plan: la poziția nominală, punctul camerei de pe flanșă cade exact pe
#: punctul camerei din plan (a lui `cell_layout.ARMS[*]["wrist_camera"]`), cu eroare sub 0,1 mm,
#: iar poziția rămâne cât mai aproape de „home" al modelului, ca gestul să fie natural.
NOMINAL_ARM_QPOS: dict[str, tuple[float, ...]] = {
    "arm1": (-0.27896, -0.48046, -0.29629, -1.44349, -0.02568, 1.54349, -0.78530),
    "arm2": (0.21421, -1.00423, 0.44731, -1.16020, 0.04422, 1.57013, -0.78530),
}

#: Deschiderea degetelor la poziția nominală: aceeași valoare ca în keyframe-ul modelului copil.
GRIPPER_OPEN = 0.04

#: Câți pași se lasă piesa să se așeze pe bandă înainte de măsurători și de randare.
SETTLE_STEPS = 200


def _fmt(value: Any) -> str:
    """Numărul, cu patru zecimale, fără „-0,0000" (care strică citirea unui fișier de scenă)."""
    text = f"{float(value):.4f}"
    return "0.0000" if text == "-0.0000" else text


def scene_template(camera_z: float | None = None) -> str:
    """`scene/cell.xml` cu cifrele din `cncdt_mj/layout.py` puse la locul lor.

    Niciun număr care vine din plan ori din înălțimile documentate nu este scris în XML: toate
    intră de aici, din aceeași sursă pe care o folosesc și tabelele din `docs/`.
    """
    geometry = layout.scene_geometry(camera_z)
    values = {key: _fmt(value) for key, value in geometry.items()}
    # Simetria din XML se scrie cu semn (`{neg_...}`), fiindcă `{-x}` nu e o formulă validă
    # pentru `str.format`. Perechile negative se adaugă o dată, pentru toate cifrele.
    values.update({f"neg_{key}": _fmt(-value) for key, value in geometry.items()})
    return CELL_XML.read_text(encoding="utf-8").format(**values)


def placements(variant: part.PartVariant, blank: part.BlankStock, seed: int = part.DEFAULT_SEED,
               blank_xy: tuple[float, float] | None = None, blank_yaw_deg: float | None = None,
               part_yaw_deg: float | None = None,
               part_xy: tuple[float, float] | None = None) -> dict[str, dict[str, Any]]:
    """Unde și cum stau cele două stări de material, cu parametri peste valorile din plan.

    Blocul brut primește poziție și rotație aleatoare, ca marfa reală de pe bandă; piesa mașinată
    stă sub camera de inspecție și primește doar yaw, fiindcă poziția ei acolo este cea care face
    cadrul util. Toate se pot fixa explicit, ca scena să fie reproductibilă; poziția explicită a
    piesei mașinate se verifică față de amprenta benzii 2.
    """
    nominal = {item.state: item for item in layout.part_placements()}
    blank_place = nominal[layout.STATE_BLANK]
    machined_place = nominal[layout.STATE_MACHINED]

    if blank_xy is None:
        blank_xy = part.stopped_xy(seed, blank_place.x, blank_place.y, blank_place.bounds,
                                  blank.radius_m)
    if blank_yaw_deg is None:
        blank_yaw_deg = part.random_yaw_deg(seed, "blank")
    if part_yaw_deg is None:
        part_yaw_deg = part.random_yaw_deg(seed, "part")
    if part_xy is None:
        part_xy = (machined_place.x, machined_place.y)
    else:
        key = belt.belt_at(machined_place.x, machined_place.y)
        if key is None:
            raise ValueError("piesa mașinată nu are bandă nominală în plan")
        part_xy = part.on_belt_xy(part_xy[0], part_xy[1], machined_place.bounds,
                                  part.belt_part_radius_m(key, variant.family))

    return {
        blank_place.body: {
            "state": blank_place.state,
            "note": blank_place.note,
            "pos": (float(blank_xy[0]), float(blank_xy[1]),
                    blank.rest_z_m(blank_place.surface_z)),
            "yaw_deg": float(blank_yaw_deg),
            "shape": blank.shape,
            "radius_m": blank.radius_m,
            "half_height_m": blank.half_height_m,
        },
        machined_place.body: {
            "state": machined_place.state,
            "note": machined_place.note,
            "pos": (float(part_xy[0]), float(part_xy[1]),
                    part.rest_z_m(variant, machined_place.surface_z)),
            "yaw_deg": float(part_yaw_deg),
        },
    }


def _joint_qpos(model: mujoco.MjModel, name: str) -> int:
    """Adresa în `qpos` a unei articulații, după nume; eroarea listează numele disponibile."""
    joint = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
    if joint < 0:
        available = sorted(
            mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, index)
            for index in range(model.njnt)
        )
        raise ValueError(f"articulație inexistentă în scenă: {name}; există: {available}")
    return int(model.jnt_qposadr[joint])


def nominal_qpos(model: mujoco.MjModel, places: dict[str, dict[str, Any]]) -> np.ndarray:
    """Vectorul `qpos` al poziției nominale, scris de celulă, în ordinea modelului compilat."""
    qpos = np.zeros(model.nq)
    for prefix, _ in ARM_MOUNTS:
        for index, value in enumerate(NOMINAL_ARM_QPOS[prefix]):
            qpos[_joint_qpos(model, f"{prefix}_joint{index + 1}")] = value
        for side in (1, 2):
            qpos[_joint_qpos(model, f"{prefix}_finger_joint{side}")] = GRIPPER_OPEN
    for body, place in places.items():
        address = _joint_qpos(model, f"{body}_free")
        qpos[address:address + 3] = place["pos"]
        qpos[address + 3:address + 7] = part.yaw_quat(place["yaw_deg"])
    return qpos


def _add_parts(spec: mujoco.MjSpec, variant: part.PartVariant, blank: part.BlankStock,
               places: dict[str, dict[str, Any]]) -> None:
    """Cele două stări de material, ca două corpuri libere așezate pe benzi.

    Blocul brut și roata mașinată sunt amândouă în scenă: în flux, blocul intră în mașină și iese
    ca rotă, dar transformarea nu se modelează aici, iar yaw-ul fiecăruia este parametru.
    """
    vertices, faces = part.scaled_geometry(variant)
    spec.add_mesh(name="machined_part_mesh", uservert=vertices.ravel().tolist(),
                  userface=faces.ravel().tolist())
    for body, place in places.items():
        spec_body = spec.worldbody.add_body(name=body, pos=list(place["pos"]),
                                            quat=list(part.yaw_quat(place["yaw_deg"])))
        spec_body.add_freejoint(name=f"{body}_free")
        if place["state"] == layout.STATE_BLANK:
            if place["shape"] == part.BLANK_SHAPES[0]:
                spec_body.add_geom(name=f"{body}_geom", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                                   size=[place["radius_m"], place["half_height_m"], 0.0],
                                   material="blank_mat")
            else:
                spec_body.add_geom(name=f"{body}_geom", type=mujoco.mjtGeom.mjGEOM_BOX,
                                   size=[place["radius_m"], place["radius_m"],
                                         place["half_height_m"]],
                                   material="blank_mat")
        else:
            spec_body.add_geom(name=f"{body}_geom", type=mujoco.mjtGeom.mjGEOM_MESH,
                               meshname="machined_part_mesh", material="part_mat")


def _body_id(model: mujoco.MjModel, name: str) -> int:
    """Identificatorul unui corp, cu eroare care spune ce nume lipsește."""
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
    if identifier < 0:
        raise ValueError(f"corp inexistent în scenă: {name}")
    return int(identifier)


def wrist_camera_quat(model: mujoco.MjModel, arm: str,
                      mount: tuple[float, float, float] = WRIST_CAMERA_OFFSET) -> np.ndarray:
    """Orientarea camerei de pe flanșă, ca obiectivul să privească punctul de strângere.

    Punctul de strângere se măsoară din geometria falcilor (`arms.tool_point`), nu se presupune:
    de el atârnă distanța la care ajunge piesa în cadru, iar distanța se citește din montajul de
    mai sus — 143 mm, cât să cuprindă roata de ~104 mm cu contextul falcilor.

    Camerele MuJoCo privesc de-a lungul axei lor ``−Z``, iar „sus"-ul cadrului se ia din axa
    sculei, proiectată perpendicular pe direcția de privire: imaginea stă la fel față de piesă
    oricum ar fi orientat brațul, în loc să se răsucească o dată cu încheietura.
    """
    data = mujoco.MjData(model)
    for side in (1, 2):
        data.qpos[_joint_qpos(model, f"{arm}_finger_joint{side}")] = GRIPPER_OPEN
    mujoco.mj_forward(model, data)
    link = _body_id(model, f"{arm}_link7")
    rotation = np.asarray(data.xmat[link], dtype=float).reshape(3, 3)
    pinch = rotation.T @ (arms.tool_point(model, data, arm)
                          - np.asarray(data.xpos[link], dtype=float))
    back = np.asarray(mount, dtype=float) - pinch
    z_axis = back / float(np.linalg.norm(back))
    up = pinch - z_axis * float(pinch @ z_axis)
    if float(np.linalg.norm(up)) < 1e-6:
        up = np.array([0.0, 1.0, 0.0])
    y_axis = up / float(np.linalg.norm(up))
    x_axis = np.cross(y_axis, z_axis)
    x_axis = x_axis / float(np.linalg.norm(x_axis))
    y_axis = np.cross(z_axis, x_axis)
    quat = np.zeros(4)
    mujoco.mju_mat2Quat(quat, np.column_stack((x_axis, y_axis, z_axis)).ravel())
    return quat


def build_spec(seed: int = part.DEFAULT_SEED, defect: str | None = None,
               blank_shape: str = part.BLANK_SHAPES[0],
               blank_xy: tuple[float, float] | None = None, blank_yaw_deg: float | None = None,
               part_yaw_deg: float | None = None, part_xy: tuple[float, float] | None = None,
               camera_z: float | None = None,
               family: str | None = None) -> tuple[mujoco.MjSpec, dict[str, Any]]:
    """Scena completă, ca `MjSpec`, plus metadatele pieselor (poziție, yaw, etichetă).

    Toți parametrii de material sunt opționali: implicit, sămânța dă aceeași scenă de fiecare dată.
    ``family`` alege familia de piese; blocul brut și piesa mașinată sunt din aceeași familie,
    fiindcă între ele se petrece prelucrarea.
    """
    panda_dir = Path(mujoco_menagerie.get(PANDA_MODEL).path())
    variant = part.load_variant(seed, defect, family)
    blank = part.blank_stock(blank_shape, family)
    places = placements(variant, blank, seed, blank_xy, blank_yaw_deg, part_yaw_deg, part_xy)

    spec = mujoco.MjSpec.from_string(scene_template(camera_z))
    # Meshele modelului copil se caută relativ la directorul lui, nu al fișierului părinte.
    spec.modelfiledir = str(panda_dir)
    # Același model atașat de două ori: fără `copy_during_attach`, al doilea braț ar refolosi
    # activele primului, iar primul ar fi rescris.
    spec.copy_during_attach = True
    for prefix, mount in ARM_MOUNTS:
        child = mujoco.MjSpec.from_file(str(panda_dir / "panda.xml"))
        spec.attach(child, prefix=f"{prefix}_", frame=spec.body(mount).to_frame())

    _add_parts(spec, variant, blank, places)

    # Keyframe-ul „nominal" al celulei. Ca să scriem vectorul în ordinea modelului, compilăm o
    # dată, citim adresele articulațiilor după nume, adăugăm keyframe-ul și compilăm din nou:
    # compilarea e ieftină, iar ordinea este deterministică pentru același spec. Tot de prima
    # compilare are nevoie și orientarea camerelor de pe flanșe, care se calculează din poziția
    # măsurată a punctului de strângere.
    model = spec.compile()
    for prefix, _ in ARM_MOUNTS:
        spec.body(f"{prefix}_link7").add_camera(
            name=f"{prefix}_wrist_camera", pos=list(WRIST_CAMERA_OFFSET),
            quat=[float(value) for value in wrist_camera_quat(model, prefix)],
            fovy=WRIST_CAMERA_FOVY)
    spec.add_key(name="nominal", qpos=nominal_qpos(model, places).tolist(),
                 ctrl=nominal_ctrl(model).tolist())

    metadata: dict[str, Any] = {
        "seed": seed,
        "panda_model": PANDA_MODEL,
        "part": part.describe(variant),
        "blank": blank.as_dict(),
        "placements": places,
        "camera_z": layout.inspection_camera_z() if camera_z is None else camera_z,
        "camera_intrinsics": layout.inspection_camera_intrinsics(camera_z),
    }
    return spec, metadata


def build_model(**kwargs: Any) -> tuple[mujoco.MjModel, dict[str, Any]]:
    """Scena compilată, gata de pași; vezi :func:`build_spec` pentru parametri."""
    spec, metadata = build_spec(**kwargs)
    return spec.compile(), metadata


def nominal_ctrl(model: mujoco.MjModel) -> np.ndarray:
    """Vectorul `ctrl` al poziției nominale: țintele actuatoarelor, nu doar poziția lor.

    Fără el, servo-urile de poziție ale brațelor primesc comanda zero și încep să tragă spre
    configurația cu toate articulațiile la zero — iar scena „nominală" se mișcă singură de la
    primul pas. Griperul primește comanda deschisă, cât ține keyframe-ul modelului copil.
    """
    ctrl = np.zeros(model.nu)
    for prefix, _ in ARM_MOUNTS:
        for index, value in enumerate(NOMINAL_ARM_QPOS[prefix]):
            name = f"{prefix}_actuator{index + 1}"
            actuator = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            if actuator < 0:
                raise ValueError(f"actuator inexistent în scenă: {name}")
            ctrl[actuator] = value
        fingers = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR,
                                    f"{prefix}_actuator{len(NOMINAL_ARM_QPOS[prefix]) + 1}")
        if fingers < 0:
            raise ValueError(f"actuator inexistent în scenă: {prefix}_actuator8")
        # Falcile stau deschise la maximum, adică la capătul cursei lor, iar comanda actuatorului
        # griperului este remapată pe cursa aceea (`panda.xml`), deci „deschis" este maximul ei.
        ctrl[fingers] = model.actuator_ctrlrange[fingers][1]
    return ctrl


def apply_nominal(model: mujoco.MjModel, data: mujoco.MjData,
                  places: dict[str, dict[str, Any]]) -> None:
    """Aduce scena în poziția nominală scrisă de celulă, nu în cea moștenită din modelele copil.

    Scrie și țintele actuatoarelor, nu doar `qpos`: altfel brațele ar primi comanda zero și ar
    porni spre altă configurație din primul pas.
    """
    data.qpos[:] = nominal_qpos(model, places)
    data.ctrl[:] = nominal_ctrl(model)
    mujoco.mj_forward(model, data)


def settle(model: mujoco.MjModel, data: mujoco.MjData, steps: int = SETTLE_STEPS) -> None:
    """Rulează câțiva pași, ca piesele să se așeze pe benzi înainte de măsurători și de randare."""
    for _ in range(steps):
        mujoco.mj_step(model, data)


def reach_report(model: mujoco.MjModel, data: mujoco.MjData) -> list[dict[str, Any]]:
    """Distanța, în spațiu, de la baza reală a fiecărui braț la fiecare țintă a lui.

    Verificarea din plan măsoară doar în plan; aici se măsoară pe corpurile scenei compilate, ceea
    ce prinde exact capcana semnalată în `docs/11-geaman-mujoco.md` §2: o țintă poate părea
    la îndemână în desen și să fie prea sus.
    """
    rows: list[dict[str, Any]] = []
    for prefix, _ in ARM_MOUNTS:
        body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, f"{prefix}_link0")
        base = data.xpos[body]
        for item in layout.targets_of(prefix):
            name = str(item["name"])
            target = np.array([float(item["x"]), float(item["y"]), layout.target_z(name)])
            distance = float(np.linalg.norm(target - base))
            rows.append({
                "arm": prefix,
                "target": name,
                "label": item["label"],
                "distance_m": distance,
                "reach_m": layout.ARM_REACH_M,
                "ok": distance <= layout.ARM_REACH_M,
            })
    return rows


def part_yaw_deg(model: mujoco.MjModel, data: mujoco.MjData, body: str) -> float:
    """Yaw-ul unei piese, citit din starea scenei.

    Este exact eticheta care va însoți cadrul de inspecție: fluxul așază piesa la o rotație
    aleatoare, iar valoarea se citește din model, nu se presupune.
    """
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body)
    if identifier < 0:
        raise ValueError(f"corp inexistent în scenă: {body}")
    return part.yaw_from_quat(data.xquat[identifier])


def _geoms_of_bodies(model: mujoco.MjModel, names: tuple[str, ...]) -> list[int]:
    """Geomele corpurilor date; se sar cele fără contact (`contype` 0), care nu pot fi lovite."""
    geoms: list[int] = []
    for name in names:
        body = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        if body < 0:
            raise ValueError(f"corp inexistent în scenă: {name}")
        for index in range(model.body_geomnum[body]):
            geom = int(model.body_geomadr[body]) + index
            if model.geom_contype[geom] or model.geom_conaffinity[geom]:
                geoms.append(geom)
    return geoms


def _geoms_of_arm(model: mujoco.MjModel, prefix: str) -> list[int]:
    """Geomele unui braț, adunate după prefixul corpurilor atașate."""
    names = tuple(
        name for name in (
            mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body)
            for body in range(model.nbody)
        ) if name and name.startswith(prefix)
    )
    return _geoms_of_bodies(model, names)


def _min_distance(model: mujoco.MjModel, data: mujoco.MjData, geoms_a: list[int],
                  geoms_b: list[int], distmax: float) -> float:
    """Cea mai mică distanță geometrică dintre două mulțimi de geome (negativă la penetrare)."""
    fromto = np.zeros(6)
    best = distmax
    for geom_a in geoms_a:
        for geom_b in geoms_b:
            best = min(best, float(mujoco.mj_geomDistance(model, data, geom_a, geom_b, distmax,
                                                          fromto)))
    return best


def camera_clearance(model: mujoco.MjModel, data: mujoco.MjData, samples: int = 200,
                     seed: int = 20261014, distmax: float = 0.5) -> dict[str, Any]:
    """Cât de aproape ajunge camera de inspecție de fiecare braț, măsurat în scenă.

    `docs/11-geaman-mujoco.md` §2 lasă explicit verificarea asta pentru montaj: distanța mică
    dintre axul brațului 2 și banda 2 nu se poate judeca din plan. Se eșantionează configurații în
    limitele articulațiilor fiecărui braț și se măsoară distanța minimă dintre geomele camerei și
    geomele brațului; se raportează și distanța la poziția nominală, singura pe care scena o
    folosește efectiv.
    """
    camera_geoms = _geoms_of_bodies(model, ("camera_mount", "camera_quality_mount"))
    camera_geoms = [geom for geom in camera_geoms if model.geom_contype[geom]]
    saved = data.qpos.copy()
    rng = random.Random(seed)
    report: dict[str, Any] = {"samples": samples, "nominal": {}, "sampled": {}}
    for prefix, _ in ARM_MOUNTS:
        arm_geoms = _geoms_of_arm(model, f"{prefix}_")
        report["nominal"][prefix] = _min_distance(model, data, camera_geoms, arm_geoms, distmax)
        joints = []
        for index in range(7):
            name = f"{prefix}_joint{index + 1}"
            joint = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
            low, high = (float(value) for value in model.jnt_range[joint])
            joints.append((_joint_qpos(model, name), low, high))
        worst = distmax
        for _ in range(samples):
            data.qpos[:] = saved
            for address, low, high in joints:
                data.qpos[address] = rng.uniform(low, high)
            mujoco.mj_kinematics(model, data)
            worst = min(worst, _min_distance(model, data, camera_geoms, arm_geoms, distmax))
        report["sampled"][prefix] = worst
    data.qpos[:] = saved
    mujoco.mj_kinematics(model, data)
    return report


def _part_radius_m(model: mujoco.MjModel, geom: int) -> float:
    """Raza piesei în planul ei: maximul distanței orizontale a vârfurilor, nu raza de încadrare.

    Raza de încadrare a unei meșe este sfera care o cuprinde (include și grosimea), deci ar
    umfla diametrul aparent al piesei din cadru; silueta văzută de sus este cea care contează.
    """
    if model.geom_type[geom] == mujoco.mjtGeom.mjGEOM_MESH:
        mesh = int(model.geom_dataid[geom])
        start = int(model.mesh_vertadr[mesh])
        vertices = model.mesh_vert[start:start + int(model.mesh_vertnum[mesh])].reshape(-1, 3)
        return float(np.hypot(vertices[:, 0], vertices[:, 1]).max())
    return float(model.geom_size[geom][0])


def camera_view(model: mujoco.MjModel, data: mujoco.MjData, camera: str,
                point: Any) -> dict[str, float | bool]:
    """Unde cade un punct din scenă în cadrul unei camere: distanța, abaterea de la ax, câmpul.

    Camerele MuJoCo privesc de-a lungul axei lor locale −Z (verificat cu o scenă de probă), iar
    verificarea asta prinde exact capcana orientării: o cameră întoarsă pe dos trece de orice
    măsurătoare de distanță, dar nu are subiectul în față. Subiectul poate fi un corp (piesa
    mașinată, pentru camera de inspecție) ori un punct (punctul de strângere, pentru camerele de pe
    flanșe, care fotografiază piesa ținută în falci).
    """
    camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, camera)
    if camera_id < 0:
        raise ValueError(f"cameră inexistentă în scenă: {camera}")
    width_px, height_px = layout.INSPECTION_CAMERA_FRAME
    axes = data.cam_xmat[camera_id].reshape(3, 3)
    offset = np.asarray(point, dtype=float) - data.cam_xpos[camera_id]
    local = axes.T @ offset
    depth = -float(local[2])
    fovy = math.radians(float(model.cam_fovy[camera_id]))
    fovx = 2 * math.atan(math.tan(fovy / 2) * width_px / height_px)
    angle_x = math.degrees(math.atan2(float(local[0]), depth))
    angle_y = math.degrees(math.atan2(float(local[1]), depth))
    return {
        "distance_m": float(np.linalg.norm(offset)),
        "depth_m": depth,
        "angle_x_deg": angle_x,
        "angle_y_deg": angle_y,
        "px_per_mm": height_px / (2 * depth * math.tan(fovy / 2)) / 1000 if depth > 0 else 0.0,
        "fovx_deg": math.degrees(fovx),
        "fovy_deg": math.degrees(fovy),
        "in_frame": (depth > 0 and abs(angle_x) <= math.degrees(fovx) / 2
                     and abs(angle_y) <= math.degrees(fovy) / 2),
    }


def camera_framing(model: mujoco.MjModel, data: mujoco.MjData, camera: str,
                   body: str) -> dict[str, float | bool]:
    """Unde cade o piesă în cadrul unei camere: distanța, abaterea de la ax și diametrul în pixeli.

    Diametrul iese din silueta geomei piesei, nu din vreo constantă transcrisă, iar restul din
    :func:`camera_view`, care lucrează pe un punct oarecare.
    """
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body)
    if body_id < 0:
        raise ValueError(f"corp inexistent în scenă: {body}")
    view = camera_view(model, data, camera, data.xpos[body_id])
    radius = _part_radius_m(model, int(model.body_geomadr[body_id]))
    return {**view, "diameter_px": 2 * radius * float(view["px_per_mm"]) * 1000}


def metrics(model: mujoco.MjModel, data: mujoco.MjData) -> dict[str, int]:
    """Cifrele pe care `view.py --check` le raportează ca dovadă că scena s-a compilat și rulează."""
    return {
        "nq": int(model.nq),
        "nv": int(model.nv),
        "nu": int(model.nu),
        "nbody": int(model.nbody),
        "ngeom": int(model.ngeom),
        "ncon": int(data.ncon),
    }


def contact_pairs(model: mujoco.MjModel, data: mujoco.MjData, limit: int = 12) -> list[str]:
    """Perechile de corpuri aflate în contact, ca listă de nume „a↔b", fără duplicate.

    Servește la verificarea așezării: dacă un braț atinge celula ori piesa atinge altceva decât
    banda pe care stă, se vede imediat aici.
    """
    pairs: set[str] = set()
    for contact in data.contact[:data.ncon]:
        names = []
        for geom in (contact.geom1, contact.geom2):
            body = model.geom_bodyid[geom]
            names.append(mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body) or "lume")
        pairs.add("↔".join(sorted(names)))
    return sorted(pairs)[:limit]


class warn_capture:
    """Context manager care strânge avertismentele MuJoCo emise în blocul curent.

    MuJoCo nu ridică excepții la conflictele de compilare (integrator, `<attach>`, poziții
    nominale), ci le scrie prin `mju_user_warning`. `view.py --check` le adună astfel și le
    raportează, ca „scena se încarcă fără avertismente" să fie o afirmație verificată.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __enter__(self) -> "warn_capture":
        mujoco.set_mju_user_warning(self.messages.append)
        return self

    def __exit__(self, *_: Any) -> None:
        mujoco.set_mju_user_warning(None)

    def __iter__(self) -> Iterator[str]:
        return iter(self.messages)
