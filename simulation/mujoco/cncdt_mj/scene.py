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
from pathlib import Path
from typing import Any, Iterator

import mujoco
import mujoco_menagerie
import numpy as np

from . import MUJOCO_DIR
from . import arms
from . import belt
from . import grasp
from . import layout
from . import part
from . import povs
from . import rack_display
from . import textures

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
#:
#: Montajul stă pe un braț lateral, nu pe axa sculei: la (0, 0, 50) mm — pe axa sculei, cum era
#: înainte — obiectivul cădea **în** carcasa încheieturii (``link6``), iar toate cele 225 de raze
#: ale câmpului de vedere loveau materialul brațului la mai puțin de 20 mm de lentilă. Cadrul ieșea
#: atunci „prin" mână — materialul nu se vedea (fețele din spate nu se randează), dar umbra lui
#: cădea peste piesă, iar scena se vedea ca printr-o fantă. Montajul a stat apoi la (90, 0, 130) mm,
#: dar obiectivul ajungea la numai 38 mm de cochilia mîinii, în deschiderea ei: razele cadrului
#: ieșeau printre pereți, iar brațul se vedea tot ca o fantă. La (120, 0, 130) mm lentila a ieșit
#: din cochilie, dar privea mîna aproape din lung: pereții concavi, văzuți din interior, sînt fețe
#: din spate, deci banda se vedea prin material, iar falcile păreau goale. La (140, 0, 140) mm
#: obiectivul stă în afara cochiliei, cu 78 mm pînă la ea în orice direcție și 83 mm pe razele
#: cadrului, la 156 mm de piesa ținută, și vede mîna de sus, din exterior: falcile sînt solide, iar
#: piesa stă în cadru cu marjă. Măsurat cu ``scene.lens_clearance`` și
#: ``scene.lens_surround_clearance``, pe care `view.py --check` le cere. Cîmpul de vedere este 46°,
#: nu 52°: la 156 mm de piesă, roata de 104 mm iese la ~377 px din 640 (~3,6 px/mm), iar falcile
#: rămîn încadrate; defectele de 2,5 mm cad la ~9 px, dintele ciobit de 4 mm la ~14 px.
WRIST_CAMERA_OFFSET = (0.14, 0.0, 0.14)
WRIST_CAMERA_FOVY = 46.0

#: Canalul de ciocnire al pieselor din rezerva de sub podea. Orice valoare nenulă diferită de 1
#: ține piesa departe de podea și de celulă, dar o lasă în arborele de ciocnire al motorului, ca
#: pornirea ciocnirii la intrarea în ciclu să prindă.
STORED_CONTYPE = 2

#: Cît de mult privește camera la stânga față de punctul de strângere, în grade. Consola stă pe
#: latura încheieturii, deci mîna îi intră în cadru pe jos, chiar sub obiectiv. Rotirea mută
#: privirea peste falcile apropiate, iar piesa rămîne în cîmp cu marjă la dreapta. Semnul este
#: pozitiv în jurul axei locale ``Y`` a camerei, adică exact spre stânga imaginii.
WRIST_CAMERA_YAW_DEG = 4.0

#: Cîte direcții se trag în jurul obiectivului cînd se măsoară spațiul liber din toate părțile.
LENS_SURROUND_GRID = 24

#: Câte raze se trag în câmpul camerei când se măsoară spațiul liber din fața obiectivului.
LENS_RAY_GRID = 15

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


def scene_template() -> str:
    """`scene/cell.xml` cu cifrele din `cncdt_mj/layout.py` puse la locul lor.

    Niciun număr care vine din plan ori din înălțimile documentate nu este scris în XML: toate
    intră de aici, din aceeași sursă pe care o folosesc și tabelele din `docs/`.
    """
    geometry = layout.scene_geometry()
    values: dict[str, Any] = {key: _fmt(value) for key, value in geometry.items()}
    # Simetria din XML se scrie cu semn (`{neg_...}`), fiindcă `{-x}` nu e o formulă validă
    # pentru `str.format`. Perechile negative se adaugă o dată, pentru toate cifrele.
    values.update({f"neg_{key}": _fmt(-value) for key, value in geometry.items()})
    # Texturile se scriu înaintea șablonului, fiindcă XML-ul le încarcă la parsare. Căile sînt
    # absolute: șablonul se compilează din memorie, iar `modelfiledir` al scenei este al modelului
    # Panda. Texturile adăugate programatic (`spec.add_texture`) corup heap-ul la compilări
    # repetate în MuJoCo 3.15.0, deci fișierul se dă din XML.
    files = textures.ensure_textures(MUJOCO_DIR / "runs" / "textures")
    values.update({f"{name}_texture": str(path) for name, path in files.items()})
    return CELL_XML.read_text(encoding="utf-8").format(**values)


def placements(variant: part.PartVariant, blank: part.BlankStock, seed: int = part.DEFAULT_SEED,
               blank_xy: tuple[float, float] | None = None, blank_yaw_deg: float | None = None,
               part_yaw_deg: float | None = None,
               part_xy: tuple[float, float] | None = None) -> dict[str, dict[str, Any]]:
    """Unde și cum stau cele două stări de material, cu parametri peste valorile din plan.

    Blocul brut primește poziție și rotație aleatoare, ca marfa reală de pe bandă; piesa mașinată
    stă pe masa de descărcare a mașinii și primește doar yaw, fiindcă poziția ei acolo este cea care
    face cadrul util. Toate se pot fixa explicit, ca scena să fie reproductibilă; poziția explicită
    a piesei mașinate se verifică față de amprenta mesei mașinii.
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
        part_xy = part.on_surface_xy(part_xy[0], part_xy[1], machined_place.bounds,
                                     part.gear_radius_m(variant.family), surface="mesei")

    body = layout.part_body_name(variant.family)
    places: dict[str, dict[str, Any]] = {
        blank_place.body: {
            "state": blank_place.state,
            "family": variant.family,
            "note": blank_place.note,
            "pos": (float(blank_xy[0]), float(blank_xy[1]),
                    blank.rest_z_m(blank_place.surface_z)),
            "yaw_deg": float(blank_yaw_deg),
            "shape": blank.shape,
            "radius_m": blank.radius_m,
            "half_height_m": blank.half_height_m,
        },
        body: {
            "state": machined_place.state,
            "family": variant.family,
            "note": machined_place.note,
            "pos": (float(part_xy[0]), float(part_xy[1]),
                    part.rest_z_m(variant, machined_place.surface_z)),
            "yaw_deg": float(part_yaw_deg),
        },
    }
    # Piesele celorlalte familii există din construcția scenei, ca fiecare să aibă geometria și
    # masa ei compilate. Stau sub podea, în afara celulei, pînă le vine rîndul în rotație.
    for index, name in enumerate(part.family_names()):
        if name == variant.family:
            continue
        other = layout.part_body_name(name)
        places[other] = {
            "state": layout.STATE_PARKED,
            "family": name,
            "note": "în rezervă, sub podea, pînă îi vine rîndul",
            "pos": layout.parked_part_position(index),
            "yaw_deg": 0.0,
        }
    return places


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


def _planar_uv(vertices: np.ndarray) -> np.ndarray:
    """Coordonate de textură în planul piesei: fața de sus și cea de jos primesc aceeași hartă.

    Meshul generatorului nu are UV-uri, iar MuJoCo are nevoie de ele ca textura de metal periat
    să se vadă pe roată. Harta este simplă — dreptunghiul care încadrează piesa —, destul pentru
    un material uniform, fără cusături vizibile.
    """
    x = vertices[:, 0]
    y = vertices[:, 1]
    span_x = max(float(x.max() - x.min()), 1e-9)
    span_y = max(float(y.max() - y.min()), 1e-9)
    return np.column_stack(((x - x.min()) / span_x, (y - y.min()) / span_y))


def _part_mesh_name(family: str) -> str:
    """Numele meshului unei familii, în scenă."""
    return f"part_mesh_{family}"


def _add_part_mesh(spec: mujoco.MjSpec, variant: part.PartVariant) -> str:
    """Adaugă meshul piesei mașinate și întoarce numele lui din scenă.

    Se adaugă câte un mesh pentru fiecare familie, iar fiecare piesă are propriul corp, compilat
    cu meshul ei. Un singur corp cu meshul schimbat la rulare păstra masa și inerția formei
    vechi, iar placa lată și subțire era aruncată din falci la deschidere.
    """
    vertices, faces = part.scaled_geometry(variant)
    # Vîrfurile se repetă pentru fiecare triunghi, ca la randarea de catalog: altfel un vîrf de
    # pe muchia de sus este împărtășit cu flancul, iar randatorul ia normala flancului pentru
    # triunghiurile feței de sus. Piesa apare atunci ca niște cioburi albe, deși meshul e plat.
    triangles = vertices[faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0],
                       triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    degenerate = lengths < 1e-12
    lengths[degenerate] = 1.0
    normals = normals / lengths[:, None]
    normals[degenerate] = (0.0, 0.0, 1.0)
    flat_vertices = triangles.reshape(-1, 3)
    flat_normals = np.repeat(normals, 3, axis=0)
    uv = _planar_uv(flat_vertices)
    corners = np.arange(len(flat_vertices), dtype=np.int64).reshape(-1, 3)
    name = _part_mesh_name(variant.family)
    spec.add_mesh(name=name, uservert=flat_vertices.ravel().tolist(),
                  usernormal=flat_normals.ravel().tolist(),
                  usertexcoord=uv.ravel().tolist(), userface=corners.ravel().tolist(),
                  userfacetexcoord=corners.ravel().tolist(), smoothnormal=False)
    return name


def set_part_stored(model: mujoco.MjModel, body: str, stored: bool) -> None:
    """Pune o piesă în rezervă ori o scoate din ea.

    Piesele care așteaptă sub podea primesc un alt canal de ciocnire, ca planul infinit al
    podelei să nu le împingă afară, iar greutatea lor este compensată, ca să rămînă la cota de
    depozitare fără o legătură rigidă. Cînd piesa intră în ciclu, amîndouă se dau la loc.

    Canalul de rezervă trebuie să fie **nenul**: MuJoCo își construiește arborele de ciocnire
    la compilare din geomele care pot ciocni vreodată, iar o geomă cu `contype = 0` nu mai intră
    în el. Pornirea ciocnirii la rulare ar fi atunci ignorată tăcut — piesa ar trece prin falci
    și prin universal fără niciun contact.
    """
    geom = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"{body}_geom")
    if geom >= 0:
        channel = STORED_CONTYPE if stored else 1
        model.geom_contype[geom] = channel
        model.geom_conaffinity[geom] = channel
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body)
    if identifier >= 0:
        # Motorul ține și un rezumat pe corp, din care își construiește perechile de ciocnire:
        # schimbat doar pe geomă, rezumatul rămîne pe canalul vechi și piesa nu mai ciocnește
        # nimic, deși `geom_contype` arată bine.
        model.body_contype[identifier] = channel
        model.body_conaffinity[identifier] = channel
        model.body_gravcomp[identifier] = 1.0 if stored else 0.0


def _add_parts(spec: mujoco.MjSpec, places: dict[str, dict[str, Any]],
               meshes: dict[str, str]) -> None:
    """Materialul celulei: blocul brut și cîte un corp de piesă pentru fiecare familie.

    Blocul brut și roata mașinată sunt amândouă în scenă: în flux, blocul intră în mașină și iese
    ca rotă, iar schimbul dintre ele se face la prelucrare. Fiecare familie are propriul corp,
    compilat cu meshul, masa și inerția ei; cele inactive stau sub podea, pe un canal de ciocnire
    care nu atinge nimic, pînă le vine rîndul.
    """
    for body, place in places.items():
        # `gravcomp` se scrie în spec, nu doar în model: fără ea la compilare, MuJoCo nu
        # activează compensarea greutății (`flg_gravcomp`), iar pornirea la rulare e ignorată.
        stored = place["state"] == layout.STATE_PARKED
        spec_body = spec.worldbody.add_body(name=body, pos=list(place["pos"]),
                                            quat=list(part.yaw_quat(place["yaw_deg"])),
                                            gravcomp=1.0 if stored else 0.0)
        spec_body.add_freejoint(name=f"{body}_free")
        if place["state"] == layout.STATE_BLANK:
            if place["shape"] == part.BLANK_SHAPES[0]:
                spec_body.add_geom(name=f"{body}_geom", type=mujoco.mjtGeom.mjGEOM_CYLINDER,
                                   size=[place["radius_m"], place["half_height_m"], 0.0],
                                   material="blank_brushed")
            else:
                spec_body.add_geom(name=f"{body}_geom", type=mujoco.mjtGeom.mjGEOM_BOX,
                                   size=[place["radius_m"], place["radius_m"],
                                         place["half_height_m"]],
                                   material="blank_brushed")
        else:
            quiet = place["state"] == layout.STATE_PARKED
            channel = STORED_CONTYPE if quiet else 1
            spec_body.add_geom(name=f"{body}_geom", type=mujoco.mjtGeom.mjGEOM_MESH,
                               meshname=meshes[str(place["family"])], material="part_brushed",
                               contype=channel, conaffinity=channel)
    # Copiile de afișare ale pieselor sortate: aceeași geometrie, dar corpuri `mocap`. Nu cad și
    # nu se ciocnesc, deci pot sta în raft fără să deranjeze brațele. Pornesc ascunse sub podea.
    for name in layout.RACK_COPY_BODIES:
        copy_body = spec.worldbody.add_body(name=name, pos=[0.0, 0.0, layout.RACK_COPY_PARK_Z],
                                            mocap=True)
        copy_body.add_geom(name=f"{name}_geom", type=mujoco.mjtGeom.mjGEOM_MESH,
                           meshname=meshes[layout.DEFAULT_PART_FAMILY], material="part_brushed",
                           contype=0, conaffinity=0)


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
    el stă la ~143 mm de flanșă, iar camera la ~156 mm de el, cât să cuprindă roata de ~104 mm
    împreună cu contextul falcilor.

    Camerele MuJoCo privesc de-a lungul axei lor ``−Z``, iar „sus"-ul cadrului se ia din axa
    sculei, proiectată perpendicular pe direcția de privire: imaginea stă la fel față de piesă
    oricum ar fi orientat brațul, în loc să se răsucească o dată cu încheietura.

    Privirea se mai rotește cu :data:`WRIST_CAMERA_YAW_DEG` spre stânga, în jurul axei locale
    ``Y`` a camerei, după ce s-a țintit punctul de strângere: mîna stă chiar sub obiectiv, iar
    fără rotire intră adînc în cadru, pe jos, și își arată interiorul.
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
    yaw = np.zeros(4)
    mujoco.mju_axisAngle2Quat(yaw, np.array([0.0, 1.0, 0.0]),
                              math.radians(WRIST_CAMERA_YAW_DEG))
    result = np.zeros(4)
    mujoco.mju_mulQuat(result, quat, yaw)
    return result


def build_spec(seed: int = part.DEFAULT_SEED, defect: str | None = None,
               blank_shape: str = part.BLANK_SHAPES[0],
               blank_xy: tuple[float, float] | None = None, blank_yaw_deg: float | None = None,
               part_yaw_deg: float | None = None, part_xy: tuple[float, float] | None = None,
               family: str | None = None) -> tuple[mujoco.MjSpec, dict[str, Any]]:
    """Scena completă, ca `MjSpec`, plus metadatele pieselor (poziție, yaw, etichetă).

    Toți parametrii de material sunt opționali: implicit, sămânța dă aceeași scenă de fiecare dată.
    ``family`` alege familia de piese; blocul brut și piesa mașinată sunt din aceeași familie,
    fiindcă între ele se petrece prelucrarea.
    """
    panda_dir = Path(mujoco_menagerie.get(PANDA_MODEL).path())
    variants = {name: part.load_variant(seed, defect, name) for name in part.family_names()}
    variant = variants[part.family(family).name]
    blank = part.blank_stock(blank_shape, variant.family)
    places = placements(variant, blank, seed, blank_xy, blank_yaw_deg, part_yaw_deg, part_xy)

    spec = mujoco.MjSpec.from_string(scene_template())
    # Meshele modelului copil se caută relativ la directorul lui, nu al fișierului părinte.
    spec.modelfiledir = str(panda_dir)
    # Același model atașat de două ori: fără `copy_during_attach`, al doilea braț ar refolosi
    # activele primului, iar primul ar fi rescris.
    spec.copy_during_attach = True
    for prefix, mount in ARM_MOUNTS:
        child = mujoco.MjSpec.from_file(str(panda_dir / "panda.xml"))
        # Corpul și cadrul se țin în variabile: `attach` primește un `MjsFrame`, iar wrapper-ul
        # Python al corpului nu trebuie să dispară în timpul apelului (altfel, rar, MuJoCo
        # raportează „incompatible id in geom array" ori `bad_alloc`).
        mount_body = spec.body(mount)
        frame = mount_body.to_frame()
        spec.attach(child, prefix=f"{prefix}_", frame=frame)

    # Modelul Panda își aduce propriul reflector „top", care se lipește de braț și se rotește cu
    # el. Umbra lui cădea peste piesa ținută ca o a doua siluetă, decalată, iar la 60% rezoluția
    # hărții de umbre fața de sus ieșea vălurită — exact efectul de „piesă stricată" rămas după
    # repararea normalelor. Lumina rămîne (umple brațul), dar umbra se ia doar de la `key_light`.
    for prefix, _ in ARM_MOUNTS:
        spec.light(f"{prefix}_top").castshadow = False

    # Câte un mesh și câte un corp pentru fiecare familie: aceeași scenă poate prelucra oricare
    # dintre ele, iar corpul fiecăreia are geometria și masa ei compilate.
    meshes = {name: _add_part_mesh(spec, item) for name, item in variants.items()}
    _add_parts(spec, places, meshes)
    # Legăturile de prindere și clemele pieselor: inactive pînă la ciclu, dar prezente în model,
    # fiindcă MuJoCo nu poate adăuga constrângeri după compilare.
    grasp.add_constraints(spec, arms.ARMS, tuple(places))

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
        "families": {name: part.describe(item) for name, item in variants.items()},
        "meshes": meshes,
        "blank": blank.as_dict(),
        "placements": places,
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
    # Raftul pornește gol: copiile de afișare se ascund sub podea înainte de primul pas.
    rack_display.park_all(model, data)
    mujoco.mj_forward(model, data)
    # Piesele familiilor inactive rămîn în rezerva de sub podea, cu ciocnirea oprită și greutatea
    # compensată, pînă le vine rîndul; piesa familiei active stă normal, pe masa mașinii.
    for body, place in places.items():
        if place["state"] == layout.STATE_BLANK:
            continue
        set_part_stored(model, body, place["state"] == layout.STATE_PARKED)


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


def camera_view(model: mujoco.MjModel, data: mujoco.MjData, camera: str,
                point: Any) -> dict[str, float | bool]:
    """Unde cade un punct din scenă în cadrul unei camere: distanța, abaterea de la ax, câmpul.

    Camerele MuJoCo privesc de-a lungul axei lor locale −Z (verificat cu o scenă de probă), iar
    verificarea asta prinde exact capcana orientării: o cameră întoarsă pe dos trece de orice
    măsurătoare de distanță, dar nu are subiectul în față. Subiectul este punctul de strângere al
    brațului, față de care se așază cadrul fiecărei camere de pe flanșă.
    """
    camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, camera)
    if camera_id < 0:
        raise ValueError(f"cameră inexistentă în scenă: {camera}")
    width_px, height_px = povs.PROCESS_FRAME
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


def lens_clearance(model: mujoco.MjModel, data: mujoco.MjData, camera: str, arm: str,
                   grid: int = LENS_RAY_GRID) -> float:
    """Cea mai mică distanță de la obiectiv la materialul brațului propriu, pe razele cadrului.

    Razele se trag doar în câmpul camerei, adică exact acolo unde se vede ceva. Un obiectiv
    îngropat în mâna brațului are toate razele oprite la câțiva milimetri: cadrul iese atunci
    „prin" material, fiindcă fețele din spate nu se randează, dar umbra lor ajunge pe piesă.
    Întoarce ``inf`` când nicio rază nu atinge brațul propriu.
    """
    camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, camera)
    if camera_id < 0:
        raise ValueError(f"cameră inexistentă în scenă: {camera}")
    width_px, height_px = povs.PROCESS_FRAME
    half_y = math.tan(math.radians(float(model.cam_fovy[camera_id])) / 2)
    half_x = half_y * width_px / height_px
    origin = np.asarray(data.cam_xpos[camera_id], dtype=float)
    axes = np.asarray(data.cam_xmat[camera_id], dtype=float).reshape(3, 3)
    group = np.ones(6, dtype=np.uint8)
    hit = np.zeros(1, dtype=np.int32)
    nearest = math.inf
    for row in range(grid):
        for column in range(grid):
            direction = np.array([(2 * (column + 0.5) / grid - 1) * half_x,
                                  (1 - 2 * (row + 0.5) / grid) * half_y, -1.0])
            distance = mujoco.mj_ray(model, data, origin, axes @ (direction / np.linalg.norm(direction)),
                                     group, 1, -1, hit)
            geom_id = int(hit[0])
            if geom_id < 0:
                continue
            body = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY,
                                     int(model.geom_bodyid[geom_id])) or ""
            if body.startswith(arm):
                nearest = min(nearest, float(distance))
    return float(nearest)


def lens_surround_clearance(model: mujoco.MjModel, data: mujoco.MjData, camera: str, arm: str,
                            grid: int = LENS_SURROUND_GRID) -> float:
    """Cea mai mică distanță de la obiectiv la brațul propriu, în toate direcțiile.

    ``lens_clearance`` măsoară doar razele din cîmpul camerei. O cameră așezată în deschiderea
    cochiliei mîinii vede scena printre pereți: toate razele cadrului ies afară, deci verificarea
    din cîmp raportează o distanță mare, iar brațul apare ca o fantă. Aici obiectivul este
    înconjurat cu raze, ca poziția din cavitate să fie prinsă.
    """
    camera_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, camera)
    if camera_id < 0:
        raise ValueError(f"cameră inexistentă în scenă: {camera}")
    origin = np.asarray(data.cam_xpos[camera_id], dtype=float)
    axes = np.asarray(data.cam_xmat[camera_id], dtype=float).reshape(3, 3)
    group = np.ones(6, dtype=np.uint8)
    hit = np.zeros(1, dtype=np.int32)
    nearest = math.inf
    for row in range(grid):
        elevation = math.pi * (row + 0.5) / grid - math.pi / 2
        for column in range(2 * grid):
            azimuth = 2 * math.pi * (column + 0.5) / (2 * grid)
            direction = np.array([math.cos(elevation) * math.cos(azimuth),
                                  math.cos(elevation) * math.sin(azimuth),
                                  math.sin(elevation)])
            distance = mujoco.mj_ray(model, data, origin, axes @ direction, group, 1, -1, hit)
            if distance < 0:
                continue
            geom_id = int(hit[0])
            body = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY,
                                     int(model.geom_bodyid[geom_id])) or ""
            if body.startswith(arm):
                nearest = min(nearest, float(distance))
    return float(nearest)


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
