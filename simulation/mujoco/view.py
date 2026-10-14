#!/usr/bin/env python3
"""Scena MuJoCo a celulei CNC: verificare, randare offscreen, viewer.

Patru moduri, toate pe aceeași scenă compusă de `cncdt_mj.scene`:

* `--check` (implicit) — compilează scena, pune poziția nominală, rulează câțiva pași și
  raportează cifrele, distanțele până la ținte, distanța minimă până la camera de inspecție,
  yaw-ul citit din scenă și contactele. Iese cu cod 1 dacă vreo verificare cade.
* `--render` — scrie un PNG offscreen, implicit `renders/cell.png`; cu `--camera camera_quality`
  scrie cadrul de inspecție, la rezoluția lui (`renders/inspection.png`).
* `--demo` — ciclul brațelor, punct cu punct: piesele sunt aduse la liniile de preluare, fiecare
  braț își urmează ruta din `cncdt_mj/moves.py`, iar la fiecare oprire se tipăresc eroarea, unghiul
  și contactele. Se termină cu „Ciclul: verde" ori cu „Ciclul: CĂZUT (n)". Cu `--shots` scrie un
  PNG la fiecare oprire.
* `--view` — deschide viewer-ul interactiv, cu benzile pornite: forța de antrenare se rescrie la
  fiecare pas (`cell.apply_belt_forces`), iar ceasul se ține în ritm real, ca transportul să se
  vadă. `--no-belts` îngheață transportul, `--speed` schimbă ritmul de rulare, iar `--demo` rulează
  ciclul în fereastră, în locul benzilor goale. Pe macOS viewer-ul merge doar sub `mjpython`, deci
  scriptul spune asta explicit și refuză să pornească sub `python`.

Se rulează din venviul geamănului (`simulation/mujoco/.venv`), care este singurul loc unde
`mujoco` există (ADR 0012).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

MUJOCO_DIR = Path(__file__).resolve().parent
if str(MUJOCO_DIR) not in sys.path:
    sys.path.insert(0, str(MUJOCO_DIR))

import mujoco  # noqa: E402  (importul cere calea de mai sus)

from cncdt_mj import arms, belt, camera, cell, checks, layout, moves, part, scene  # noqa: E402

#: Unde se scriu cadrele; directorul e comis, ca dovada vizuală să fie în repo.
RENDERS_DIR = MUJOCO_DIR / "renders"

#: Prefixele corpurilor brațelor, pentru regula de contact (`checks.arm_foreign_contacts`).
ARM_PREFIXES = tuple(f"{prefix}_" for prefix, _ in scene.ARM_MOUNTS)

#: La cât timp simulat se tipărește o linie de transport în viewer.
REPORT_PERIOD_S = 1.0

# Pragurile verificării (`MIN_CAMERA_CLEARANCE_M`, `MIN_INSPECTION_PX_PER_MM`,
# `YAW_TOLERANCE_DEG`, `MAX_SETTLE_DRIFT_M`) stau în `cncdt_mj/checks.py`: sunt reguli pure, iar
# testele din `.venv`-ul proiectului le împart cu scriptul de față.


def build(args: argparse.Namespace) -> tuple[mujoco.MjModel, mujoco.MjData, dict, list[str]]:
    """Compune scena, cu avertismentele MuJoCo strânse pe drum."""
    with scene.warn_capture() as warnings:
        model, metadata = scene.build_model(
            seed=args.seed,
            defect=args.defect,
            family=args.family,
            blank_shape=args.blank_shape,
            blank_xy=requested_xy(args.blank_x, args.blank_y, layout.STATE_BLANK),
            blank_yaw_deg=args.blank_yaw_deg,
            part_yaw_deg=args.part_yaw_deg,
            part_xy=requested_xy(args.part_x, args.part_y, layout.STATE_MACHINED),
            camera_z=args.camera_z,
        )
    return model, mujoco.MjData(model), metadata, list(warnings)


def requested_xy(x: float | None, y: float | None, state: str) -> tuple[float, float] | None:
    """Poziția cerută prin `--blank-x`/`--part-x` și perechea lor, cu planul ca rezervă.

    Cota care nu se dă rămâne cea nominală din plan: altfel `--blank-x 0.6` singur ar fi ignorat
    tăcut, fiindcă scena are nevoie de amândouă coordonatele.
    """
    if x is None and y is None:
        return None
    place = next(item for item in layout.part_placements() if item.state == state)
    return (place.x if x is None else x, place.y if y is None else y)


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
        if sampled < checks.MIN_CAMERA_CLEARANCE_M:
            failures.append(f"camera intră în calea {arm} ({sampled:.3f} m < "
                            f"{checks.MIN_CAMERA_CLEARANCE_M:.3f} m)")

    machined = next(body for body, place in metadata["placements"].items()
                    if place["state"] == layout.STATE_MACHINED)
    framing = scene.camera_framing(model, data, "camera_quality", machined)
    print(f"  piesa în cadru          : {framing['distance_m']:.3f} m de obiectiv, "
          f"abatere {framing['angle_x_deg']:+.1f}° / {framing['angle_y_deg']:+.1f}° față de ax, "
          f"diametru {framing['diameter_px']:.0f} px ({framing['px_per_mm']:.1f} px/mm)")
    if not framing["in_frame"]:
        failures.append("piesa nu intră în cadrul camerei de inspecție")
    elif framing["px_per_mm"] < checks.MIN_INSPECTION_PX_PER_MM:
        failures.append(f"camera de inspecție vede prea grosier ({framing['px_per_mm']:.1f} px/mm "
                        f"< {checks.MIN_INSPECTION_PX_PER_MM:.1f} px/mm)")

    print("\nCamere de pe flanșe: piesa ținută, la 60° pe verticală")
    for arm in arms.ARMS:
        subject = arms.tool_point(model, data, arm)
        view = scene.camera_view(model, data, f"{arm}_wrist_camera", subject)
        diameter_mm = 2 * part.gear_radius_m(metadata["part"]["family"]) * 1000
        pixels = diameter_mm * float(view["px_per_mm"])
        print(f"  {arm}: punctul de strângere la {view['depth_m'] * 1000:.0f} mm, "
              f"abatere {view['angle_x_deg']:+.1f}° / {view['angle_y_deg']:+.1f}° față de ax, "
              f"piesa de {diameter_mm:.0f} mm iese la {pixels:.0f} px")
        if not view["in_frame"]:
            failures.append(f"camera de pe {arm} nu are scula în cadru")
        elif pixels < checks.MIN_WRIST_PART_PX:
            failures.append(f"camera de pe {arm} vede prea departe piesa ținută "
                            f"({pixels:.0f} px < {checks.MIN_WRIST_PART_PX:.0f} px)")

    print("\nMaterial: două stări, amândouă pe benzi")
    for body, place in metadata["placements"].items():
        label = f"{body} ({place['state']})"
        print(f"  {label:<22} la ({place['pos'][0]:.3f}, {place['pos'][1]:.3f}, "
              f"{place['pos'][2]:.3f}), {place['note']}")
        print(f"  {'':<22} yaw cerut {place['yaw_deg']:.1f}°, "
              f"yaw citit din scenă {state['yaw_deg'][body]:.1f}°, "
              f"mișcare la așezare {state['drift_m'][body] * 1000:.1f} mm")
        yaw_error = part.yaw_delta_deg(state["yaw_deg"][body], place["yaw_deg"])
        if abs(yaw_error) > checks.YAW_TOLERANCE_DEG:
            failures.append(f"yaw-ul lui {body} nu se citește înapoi din scenă")
        if state["drift_m"][body] > checks.MAX_SETTLE_DRIFT_M:
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


def transport_line(model: mujoco.MjModel, data: mujoco.MjData,
                   parts: tuple[str, ...]) -> str:
    """O linie cu ce se întâmplă acum: ce piesă e pe ce bandă, cu ce viteză și cât mai are până
    la linia de oprire.

    Viewer-ul își are peisajul propriu, dar transportul se citește mai bine din cifre: linia se
    tipărește în terminal la fiecare `REPORT_PERIOD_S` secunde de timp simulat. Distanța rămasă
    este pe direcția de mers a benzii, deci pozitiv înseamnă că piesa mai are de mers.
    """
    carrying = cell.carried_parts(model, data, parts)
    positions = cell.part_positions(model, data, parts)
    cells = []
    for body in parts:
        x, y, _ = positions[body]
        speed = math.hypot(*cell.part_velocity(model, data, body))
        key = carrying.get(body)
        if key is None:
            cells.append(f"{body}: ({x:.3f}, {y:.3f}) v={speed:.3f} m/s, în afara benzii")
            continue
        item = belt.belt(key)
        remaining = (item.stop_x() - x) * item.direction[0]
        cells.append(f"{body}: ({x:.3f}, {y:.3f}) v={speed:.3f} m/s pe {key}, "
                     f"mai are {remaining:+.3f} m până la linia de oprire")
    return f"t={data.time:6.2f} s | " + " | ".join(cells)


def demo(args: argparse.Namespace, hook: arms.StepHook | None = None,
         shots: Path | None = None) -> int:
    """Compune scena, așază totul la poziția nominală și rulează o dată ciclul brațelor."""
    steps = args.steps or scene.SETTLE_STEPS
    model, data, metadata, warnings = build(args)
    if warnings:
        print(f"Avertismente la încărcare: {len(warnings)}")
        for message in warnings:
            print(f"  {message}")
    scene.apply_nominal(model, data, metadata["placements"])
    scene.settle(model, data, steps)
    return run_demo(model, data, args, str(metadata["part"]["family"]), hook=hook, shots=shots)


def run_demo(model: mujoco.MjModel, data: mujoco.MjData, args: argparse.Namespace,
             family: str | None = None, hook: arms.StepHook | None = None,
             shots: Path | None = None) -> int:
    """Ciclul de lucru al brațelor: fiecare braț își parcurge punctele, în ordinea din plan.

    Este singura parte a verificării care se vede: brațele pornesc din poziția nominală spre
    fiecare punct de lucru, pe drumul din `moves.stages` — trecerile, urcarea deasupra, coborârea
    pe punct și ridicarea înapoi. La fiecare oprire se tipăresc eroarea rămasă, cea mai mare eroare
    de pe drum, câți pași a durat și contactele, iar o verigă a brațului care atinge celula este
    eșec. Atingerea falcilor este raportată fără să fie eșec: falcile sunt scula, iar acolo — pe
    piesă, pe bandă, pe raft — chiar au ce căuta. ``family`` este familia pieselor din scenă: ea
    alege compartimentul în care brațul 2 lasă piesa, deci toate cele cinci se pot proba.
    """
    parts = cell.part_bodies(model)
    arrived = cell.bring_parts_to_pickup(model, data, parts)
    tool = {arm: arms.tool_length(model, data, arm) for arm in arms.ARMS}
    frames = camera.Frames(model) if shots is not None else None
    failures: list[str] = []

    print(f"Ciclul brațelor: fiecare punct de lucru, atins de sus; familia {family}")
    for body, x in sorted(arrived.items()):
        print(f"  piesa {body:<12} la x = {x:.3f} m, pe linia de preluare")
    for arm in arms.ARMS:
        print(f"  scula {arm}                : {tool[arm] * 1000:.1f} mm de la flanșă la punctul "
              f"de strângere")
    print()

    for arm in arms.ARMS:
        label = layout.arm(arm)["label"]
        print(f"{label} ({arm}), rază {layout.ARM_REACH_M:.3f} m")
        for item in moves.route(arm, tool[arm], family):
            for stage, point in moves.stages(item):
                report = arms.reach_point(model, data, arm, point, item.approach,
                                          item.spin_deg, settle=args.demo_settle, hook=hook)
                pairs = scene.contact_pairs(model, data)
                fingers = checks.arm_foreign_contacts(pairs, ARM_PREFIXES)
                links = checks.arm_link_contacts(pairs, ARM_PREFIXES)
                print(f"  {item.name:<11} {item.label:<28} {stage} "
                      f"({point[0]:.3f}, {point[1]:.3f}, {point[2]:.3f})  "
                      f"eroare {report['position_m'] * 1000:6.1f} mm / "
                      f"{report['angle_deg']:4.1f}°  drum "
                      f"{report['path_residual_m'] * 1000:6.1f} mm  "
                      f"pași {int(report['steps']):5d}")
                if fingers:
                    print(f"  {'':<11} falcile ating: {', '.join(fingers)}")
                for pair in links:
                    failures.append(f"o verigă a brațului atinge ceva la {item.name} {stage}: "
                                    f"{pair}")
                if report["position_m"] > checks.MAX_POSE_ERROR_M:
                    failures.append(
                        f"scula rămâne {report['position_m'] * 1000:.0f} mm de {item.name} "
                        f"({stage}), peste pragul de {checks.MAX_POSE_ERROR_M * 1000:.0f} mm")
                elif report["angle_deg"] > checks.MAX_POSE_ANGLE_DEG:
                    failures.append(
                        f"scula rămâne cu {report['angle_deg']:.1f}° răsuciți la {item.name} "
                        f"({stage}), peste pragul de {checks.MAX_POSE_ANGLE_DEG:.0f}°")
                if frames is not None and shots is not None:
                    frames.write(data, shots / f"{arm}-{item.name}-{stage}.png")
        print()

    if frames is not None:
        frames.close()
    print(f"Timp simulat: {data.time:.2f} s")

    # Unde au ajuns piesele: deocamdată ciclul nu prinde nimic, deci falcile doar le ating, iar o
    # piesă împinsă de sculă se vede aici. Când prinderea există (welding equality, ca în celula
    # din Isaac Sim), rămânerea pe bandă devine criteriu de eșec.
    print("\nPiesele după ciclu:")
    for body in parts:
        x, y, z = cell.part_positions(model, data, parts)[body]
        home = belt.belt_at(x, y)
        place = f"pe {home}" if home else "în afara benzilor"
        print(f"  {body:<12} la ({x:.3f}, {y:.3f}, {z:.3f})  {place}")

    if failures:
        print(f"\nCiclul: CĂZUT ({len(failures)})")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Ciclul: verde")
    return 0


def view(args: argparse.Namespace) -> int:
    """Viewer-ul interactiv, cu benzile pornite; pe macOS are nevoie de `mjpython`."""
    if sys.platform == "darwin" and "MJPYTHON_BIN" not in os.environ:
        print("Pe macOS viewer-ul MuJoCo merge doar sub `mjpython`, fiindcă fereastra trebuie\n"
              "creată pe firul principal. Semnul că rulăm sub el este `MJPYTHON_BIN`, nu numele\n"
              "din `sys.executable`: acolo scrie tot interpretul din venv.\n"
              f"  {Path(sys.executable).parent / 'mjpython'} view.py --view\n"
              "Fără afișaj, folosește `--check` și `--render`.", file=sys.stderr)
        return 2
    model, data, metadata, build_warnings = build(args)
    with scene.warn_capture() as captured:
        scene.apply_nominal(model, data, metadata["placements"])
    messages = list(build_warnings) + list(captured)
    print(f"Avertismente la încărcare: {len(messages)}")
    for message in messages:
        print(f"  {message}")
    parts = cell.part_bodies(model)
    if args.belts:
        print(f"Benzile sunt pornite la {belt.BELT_SURFACE_SPEED_MPS:.3f} m/s; își scot forța la "
              f"{layout.belt_stop_x('belt_in'):.3f} m (banda 1) și "
              f"{layout.belt_stop_x('belt_out'):.3f} m (banda 2).")
    else:
        print("Benzile sunt oprite; `--belts` le pornește.")
    print(f"Ritm de rulare {args.speed:g}x față de timpul real." if args.speed > 0.0
          else "Rulare cât de repede merge (fără ritm real).")
    steps = args.steps or 0
    started = time.monotonic()
    next_report = 0.0
    with mujoco.viewer.launch_passive(model, data) as viewer:
        camera.configure_camera(viewer.cam)
        if args.demo:
            def hook(model: mujoco.MjModel, data: mujoco.MjData) -> None:
                """Ține viewer-ul la curent cu starea și ritmul de rulare."""
                if not viewer.is_running():
                    raise SystemExit(0)
                if args.speed > 0.0:
                    delay = started + data.time / args.speed - time.monotonic()
                    if delay > 0.0:
                        time.sleep(delay)
                viewer.sync()

            return run_demo(model, data, args, str(metadata["part"]["family"]), hook=hook)
        while viewer.is_running():
            if args.belts:
                cell.apply_belt_forces(model, data)
            mujoco.mj_step(model, data)
            if args.speed > 0.0:
                delay = started + data.time / args.speed - time.monotonic()
                if delay > 0.0:
                    time.sleep(delay)
            viewer.sync()
            if args.belts and data.time >= next_report:
                print(transport_line(model, data, parts), flush=True)
                next_report = data.time + REPORT_PERIOD_S
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
    parser.add_argument("--demo", action="store_true",
                       help="rulează ciclul brațelor: fiecare punct de lucru, atins de sus, cu "
                            "erorile și contactele la fiecare oprire; cu `--view`, ciclul se "
                            "vede în viewer")
    parser.add_argument("--shots", nargs="?", const="", metavar="DIR",
                       help="cu `--demo`, scrie un PNG la fiecare oprire (implicit "
                            "renders/demo/)")
    parser.add_argument("--demo-settle", type=int, default=300,
                       help="câți pași se așteaptă la fiecare punct de lucru din ciclu")
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
    parser.add_argument("--family", default=None, choices=part.family_names(),
                        help="familia de piese: alege piesa mașinată, blocul brut, "
                             "compartimentul din raft și cota de lucru "
                             f"(implicit {part.family().name})")
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
    parser.add_argument("--part-x", type=float, default=None,
                       help="poziția X a piesei mașinate pe banda 2, verificată față de amprenta "
                            "benzii (implicit: sub camera de inspecție, din plan)")
    parser.add_argument("--part-y", type=float, default=None,
                       help="poziția Y a piesei mașinate pe banda 2 (implicit: axa benzii)")
    parser.add_argument("--camera-z", type=float, default=None,
                       help="cota centrului optic al camerei de inspecție "
                            f"(implicit {layout.inspection_camera_z():.2f} m)")
    parser.add_argument("--base-z", type=float, default=None,
                       help="cota vârfului piedestalului brațelor, pentru măsurători: "
                            f"implicit cea din document, {layout.documented_height('arm_base'):.2f} "
                            "m. La 0,55 m brațul 2 nu poate trece cu antebrațul peste banda 2 ca "
                            "să ridice piesa (`arm2_link3↔belt_out`, 106 mm eroare)")
    parser.add_argument("--belts", action=argparse.BooleanOptionalAction, default=True,
                       help="în viewer, benzile sunt pornite (implicit); `--no-belts` le oprește, "
                            "ca scena să stea pe loc")
    parser.add_argument("--speed", type=float, default=1.0,
                       help="ritmul de rulare în viewer față de timpul real (implicit 1; "
                            "0 rulează cât de repede merge)")
    args = parser.parse_args(argv)

    if args.base_z is not None:
        # Cota documentată este cea din `docs/11-geaman-mujoco.md` §2; coborârea se face doar
        # pentru măsurători, ca scena comisă să rămână cea verificată.
        print(f"Măsurătoare: vârful piedestalului brațelor la {args.base_z:.2f} m, în locul cotei "
              f"documentate, {layout.documented_height('arm_base'):.2f} m.")
        layout.ARM_BASE_Z = args.base_z

    if args.view:
        return view(args)
    if args.demo:
        shots = None if args.shots is None else (
            Path(args.shots) if args.shots else RENDERS_DIR / "demo")
        return demo(args, shots=shots)
    if args.render is not None:
        camera_name, width, height = camera_arguments(args)
        path = Path(args.render) if args.render else RENDERS_DIR / (
            "inspection.png" if camera_name == "camera_quality" else "cell.png")
        model, data, metadata, warnings = build(args)
        prepare(model, data, metadata, args.steps or scene.SETTLE_STEPS)
        camera.render(model, data, path, camera_name, width, height)
        print(f"scris {path} ({width}x{height}, camera "
              f"{camera_name or 'liberă'}, {len(warnings)} avertismente)")
        return 0
    return check(args)


if __name__ == "__main__":
    raise SystemExit(main())
