#!/usr/bin/env python3
"""Scena MuJoCo a celulei CNC: verificare, randare offscreen, viewer.

Trei moduri, toate pe aceeași scenă compusă de `cncdt_mj.scene`:

* `--check` (implicit) — compilează scena, pune poziția nominală, rulează câțiva pași și
  raportează cifrele, distanțele până la ținte, distanța minimă până la camera de inspecție,
  yaw-ul citit din scenă și contactele. Iese cu cod 1 dacă vreo verificare cade.
* `--render` — scrie un PNG offscreen, implicit `renders/cell.png`; cu `--camera camera_quality`
  scrie cadrul de inspecție, la rezoluția lui (`renders/inspection.png`).
* `--view` — deschide viewer-ul interactiv. Pe macOS viewer-ul merge doar sub `mjpython`, deci
  scriptul spune asta explicit și refuză să pornească sub `python`.

Se rulează din venviul geamănului (`simulation/mujoco/.venv`), care este singurul loc unde
`mujoco` există (ADR 0012).
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

import numpy as np

MUJOCO_DIR = Path(__file__).resolve().parent
if str(MUJOCO_DIR) not in sys.path:
    sys.path.insert(0, str(MUJOCO_DIR))

import mujoco  # noqa: E402  (importul cere calea de mai sus)

from cncdt_mj import checks, layout, part, scene  # noqa: E402

#: Unde se scriu cadrele; directorul e comis, ca dovada vizuală să fie în repo.
RENDERS_DIR = MUJOCO_DIR / "renders"

#: Cadrul general al celulei, din camera liberă: centrul celulei, de sus și dinspre sud-est.
FREE_CAMERA = {"lookat": (3.0, 2.0, 0.8), "distance": 8.5, "azimuth": 135.0, "elevation": -28.0}

#: Cât de aproape are voie să ajungă un braț de camera de inspecție, în orice configurație.
MIN_CAMERA_CLEARANCE_M = 0.02

#: Cât de fin trebuie să vadă camera de inspecție piesa. Sub atât, cel mai mic defect din catalog
#: — o gaură dezaxată cu 2,5 mm — cade sub ~9 pixeli și nicio clasă nu mai e separabilă.
MIN_INSPECTION_PX_PER_MM = 3.0

#: Toleranța la compararea yaw-ului cerut cu cel citit înapoi din scenă, în grade.
YAW_TOLERANCE_DEG = 0.1

#: Dacă o piesă se mișcă mai mult de atât la așezare, geometria ei nu stă pe bandă.
MAX_SETTLE_DRIFT_M = 0.005

#: Prefixele corpurilor brațelor, pentru regula de contact (`checks.arm_foreign_contacts`).
ARM_PREFIXES = tuple(f"{prefix}_" for prefix, _ in scene.ARM_MOUNTS)


def write_png(path: Path, image: np.ndarray) -> None:
    """Scrie un PNG RGB fără nicio dependență externă (nici PIL, nici imageio nu sunt instalate)."""
    height, width, channels = image.shape
    if channels != 3:
        raise ValueError(f"imaginea are {channels} canale, nu 3")
    raw = b"".join(b"\x00" + image[row].tobytes() for row in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def build(args: argparse.Namespace) -> tuple[mujoco.MjModel, mujoco.MjData, dict, list[str]]:
    """Compune scena, cu avertismentele MuJoCo strânse pe drum."""
    with scene.warn_capture() as warnings:
        model, metadata = scene.build_model(
            seed=args.seed,
            defect=args.defect,
            blank_shape=args.blank_shape,
            blank_xy=None if args.blank_x is None or args.blank_y is None
            else (args.blank_x, args.blank_y),
            blank_yaw_deg=args.blank_yaw_deg,
            part_yaw_deg=args.part_yaw_deg,
            camera_z=args.camera_z,
        )
    return model, mujoco.MjData(model), metadata, list(warnings)


def prepare(model: mujoco.MjModel, data: mujoco.MjData, metadata: dict,
            steps: int) -> dict:
    """Pune poziția nominală, rulează pașii, apoi citește starea din scenă."""
    scene.apply_nominal(model, data, metadata["placements"])
    before = {body: data.xpos[_body_id(model, body)].copy() for body in metadata["placements"]}
    scene.settle(model, data, steps)
    drift = {body: float(np.linalg.norm(data.xpos[_body_id(model, body)] - position))
             for body, position in before.items()}
    return {
        "drift_m": drift,
        "yaw_deg": {body: scene.part_yaw_deg(model, data, body)
                    for body in metadata["placements"]},
    }


def _body_id(model: mujoco.MjModel, body: str) -> int:
    identifier = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body)
    if identifier < 0:
        raise ValueError(f"corp inexistent în scenă: {body}")
    return identifier


def camera_arguments(args: argparse.Namespace) -> tuple[str | None, int, int]:
    """Camera și rezoluția: cadrul de inspecție are rezoluția cerută de plan, restul 1280x960."""
    if args.camera == "camera_quality":
        width, height = layout.INSPECTION_CAMERA_FRAME
    else:
        width, height = 1280, 960
    return args.camera, args.width or width, args.height or height


def render(model: mujoco.MjModel, data: mujoco.MjData, path: Path, camera: str | None,
           width: int, height: int) -> None:
    """Randează un cadru offscreen și îl scrie ca PNG."""
    with mujoco.Renderer(model, height=height, width=width) as renderer:
        if camera is None:
            free = mujoco.MjvCamera()
            mujoco.mjv_defaultFreeCamera(model, free)
            free.lookat[:] = FREE_CAMERA["lookat"]
            free.distance = FREE_CAMERA["distance"]
            free.azimuth = FREE_CAMERA["azimuth"]
            free.elevation = FREE_CAMERA["elevation"]
            renderer.update_scene(data, camera=free)
        else:
            renderer.update_scene(data, camera=camera)
        write_png(path, renderer.render())


def check(args: argparse.Namespace) -> int:
    """Modul implicit: tot ce se poate verifica fără să te uiți la ecran."""
    steps = args.steps or scene.SETTLE_STEPS
    model, data, metadata, warnings = build(args)
    state = prepare(model, data, metadata, steps)
    counters = scene.metrics(model, data)
    reach = scene.reach_report(model, data)
    clearance = scene.camera_clearance(model, data, samples=args.samples)
    intrinsics = metadata["camera_intrinsics"]
    contacts = scene.contact_pairs(model, data)
    failures: list[str] = []

    print("Scena celulei CNC: verificare")
    print(f"  model de braț            : {metadata['panda_model']} (Apache-2.0), atașat de două ori")
    print(f"  sămânța piesei           : {metadata['seed']}")
    print(f"  avertismente MuJoCo      : {len(warnings)}")
    for message in warnings:
        print(f"      {message}")
    print(f"  nq / nv / nu             : {counters['nq']} / {counters['nv']} / {counters['nu']}")
    print(f"  nbody / ngeom            : {counters['nbody']} / {counters['ngeom']}")
    print(f"  contacte după {steps} pași : {counters['ncon']}")

    print(f"\nBrațe: distanța în spațiu până la fiecare țintă (rază {layout.ARM_REACH_M:.3f} m)")
    for row in reach:
        mark = "ok" if row["ok"] else "DEPĂȘITĂ"
        print(f"  {row['arm']} → {row['label']:<34} {row['distance_m']:.3f} m  {mark}")
        if not row["ok"]:
            failures.append(f"ținta „{row['label']}\" ({row['arm']}) este în afara razei brațului")

    print(f"\nCamera de inspecție: {metadata['camera_z']:.3f} m "
          f"({layout.INSPECTION_CAMERA_CLEARANCE_Z:.2f} m peste banda 2), "
          f"{intrinsics['fovy_deg']:.1f}° pe verticală, "
          f"{layout.INSPECTION_CAMERA_FRAME[0]}x{layout.INSPECTION_CAMERA_FRAME[1]}")
    print(f"  acoperire la nivelul benzii: {intrinsics['coverage_x_m'] * 1000:.0f} x "
          f"{intrinsics['coverage_y_m'] * 1000:.0f} mm, {intrinsics['px_per_mm']:.1f} px/mm")
    for arm, distance in sorted(clearance["nominal"].items()):
        sampled = clearance["sampled"][arm]
        print(f"  distanța minimă la {arm:<5}: {distance:.3f} m la poziția nominală, "
              f"{sampled:.3f} m pe {clearance['samples']} configurații eșantionate")
        if sampled < MIN_CAMERA_CLEARANCE_M:
            failures.append(f"camera intră în calea {arm} ({sampled:.3f} m < "
                            f"{MIN_CAMERA_CLEARANCE_M:.3f} m)")

    machined = next(body for body, place in metadata["placements"].items()
                    if place["state"] == layout.STATE_MACHINED)
    framing = scene.camera_framing(model, data, "camera_quality", machined,
                                   layout.INSPECTION_CAMERA_FRAME[1])
    print(f"  piesa în cadru          : {framing['distance_m']:.3f} m de obiectiv, "
          f"abatere {framing['angle_x_deg']:+.1f}° / {framing['angle_y_deg']:+.1f}° față de ax, "
          f"diametru {framing['diameter_px']:.0f} px ({framing['px_per_mm']:.1f} px/mm)")
    if not framing["in_frame"]:
        failures.append("piesa nu intră în cadrul camerei de inspecție")
    elif framing["px_per_mm"] < MIN_INSPECTION_PX_PER_MM:
        failures.append(f"camera de inspecție vede prea grosier ({framing['px_per_mm']:.1f} px/mm "
                        f"< {MIN_INSPECTION_PX_PER_MM:.1f} px/mm)")

    print("\nMaterial: două stări, amândouă pe benzi")
    for body, place in metadata["placements"].items():
        label = f"{body} ({place['state']})"
        print(f"  {label:<22} la ({place['pos'][0]:.3f}, {place['pos'][1]:.3f}, "
              f"{place['pos'][2]:.3f}), {place['note']}")
        print(f"  {'':<22} yaw cerut {place['yaw_deg']:.1f}°, "
              f"yaw citit din scenă {state['yaw_deg'][body]:.1f}°, "
              f"mișcare la așezare {state['drift_m'][body] * 1000:.1f} mm")
        if abs(part.yaw_delta_deg(state["yaw_deg"][body], place["yaw_deg"])) > YAW_TOLERANCE_DEG:
            failures.append(f"yaw-ul lui {body} nu se citește înapoi din scenă")
        if state["drift_m"][body] > MAX_SETTLE_DRIFT_M:
            failures.append(f"{body} nu s-a așezat pe bandă (s-a mișcat "
                            f"{state['drift_m'][body] * 1000:.1f} mm)")
    print(f"  eticheta piesei mașinate: {metadata['part']}")
    print(f"  blocul brut              : {metadata['blank']}")

    print("\nContacte: " + (", ".join(contacts) if contacts else "niciunul"))
    foreign = checks.arm_foreign_contacts(contacts, ARM_PREFIXES)
    if foreign:
        failures.append("un braț atinge un element al celulei la poziția nominală: "
                        + ", ".join(foreign))

    if failures:
        print(f"\nVerificare: CĂZUTĂ ({len(failures)})")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nVerificare: verde")
    return 0


def view(args: argparse.Namespace) -> int:
    """Viewer-ul interactiv; pe macOS are nevoie de `mjpython`."""
    if sys.platform == "darwin" and Path(sys.executable).name != "mjpython":
        print("Pe macOS viewer-ul MuJoCo merge doar sub `mjpython`, fiindcă fereastra trebuie\n"
              "creată pe firul principal:\n"
              f"  {Path(sys.executable).parent / 'mjpython'} view.py --view\n"
              "Fără afișaj, folosește `--check` și `--render`.", file=sys.stderr)
        return 2
    model, data, metadata, _ = build(args)
    steps = args.steps or 0
    with scene.warn_capture() as warnings:
        scene.apply_nominal(model, data, metadata["placements"])
    print(f"Avertismente la încărcare: {len(warnings)}")
    for message in warnings:
        print(f"  {message}")
    with mujoco.viewer.launch_passive(model, data) as viewer:
        while viewer.is_running():
            mujoco.mj_step(model, data)
            viewer.sync()
            steps -= 1
            if args.steps and steps <= 0:
                break
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="compilează scena, rulează pași, raportează cifrele (implicit)")
    mode.add_argument("--render", nargs="?", const="", metavar="PNG",
                      help="scrie un PNG offscreen: implicit renders/cell.png, "
                           "ori renders/inspection.png cu --camera camera_quality")
    mode.add_argument("--view", action="store_true",
                      help="deschide viewer-ul (pe macOS: mjpython view.py --view)")
    parser.add_argument("--camera", default=None,
                       help="camera de randat: implicit camera liberă; `camera_quality` "
                            "dă cadrul de inspecție, la rezoluția lui")
    parser.add_argument("--width", type=int, default=None, help="lățimea cadrului, în pixeli")
    parser.add_argument("--height", type=int, default=None, help="înălțimea cadrului, în pixeli")
    parser.add_argument("--steps", type=int, default=None,
                       help="pași înainte de randare, ori înainte de închiderea viewer-ului")
    parser.add_argument("--samples", type=int, default=200,
                       help="câte configurații de braț se eșantionează la verificarea camerei")
    parser.add_argument("--seed", type=int, default=part.DEFAULT_SEED,
                       help="sămânța piesei și a așezării ei")
    parser.add_argument("--defect", default=None, help="clasa de defect a piesei mașinate")
    parser.add_argument("--blank-shape", choices=part.BLANK_SHAPES, default=part.BLANK_SHAPES[0],
                       help="forma blocului brut (implicit disc, ca semifabricatul de strunjit)")
    parser.add_argument("--blank-x", type=float, default=None,
                       help="poziția X a blocului pe banda 1 (implicit: aleatoare, din sămânță)")
    parser.add_argument("--blank-y", type=float, default=None,
                       help="poziția Y a blocului pe banda 1 (implicit: aleatoare, din sămânță)")
    parser.add_argument("--blank-yaw-deg", type=float, default=None,
                       help="yaw-ul blocului brut (implicit: aleator, din sămânță)")
    parser.add_argument("--part-yaw-deg", type=float, default=None,
                       help="yaw-ul piesei mașinate pe banda 2 (implicit: aleator, din sămânță)")
    parser.add_argument("--camera-z", type=float, default=None,
                       help="cota centrului optic al camerei de inspecție "
                            f"(implicit {layout.inspection_camera_z():.2f} m)")
    args = parser.parse_args(argv)

    if args.view:
        return view(args)
    if args.render is not None:
        camera, width, height = camera_arguments(args)
        path = Path(args.render) if args.render else RENDERS_DIR / (
            "inspection.png" if camera == "camera_quality" else "cell.png")
        model, data, metadata, warnings = build(args)
        prepare(model, data, metadata, args.steps or scene.SETTLE_STEPS)
        render(model, data, path, camera, width, height)
        print(f"scris {path} ({width}x{height}, camera "
              f"{camera or 'liberă'}, {len(warnings)} avertismente)")
        return 0
    return check(args)


if __name__ == "__main__":
    raise SystemExit(main())
