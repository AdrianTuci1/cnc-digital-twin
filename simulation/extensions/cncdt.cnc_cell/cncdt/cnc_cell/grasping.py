"""Prinderea piesei de griper, prin articulație fixă.

Griperul din conținut nu are falci articulate care să poată fi comandate din script, deci
prinderea se face ca în exemplele simple de pick-and-place: o articulație fixă
(``UsdPhysics.FixedJoint``) între griper și piesă, creată la prindere și ștearsă la eliberare.

Contractul de cale este stabil: articulația are întotdeauna calea :data:`GRASP_PATH`, iar
``body0`` este griperul, ``body1`` piesa. Poziția relativă de la momentul prinderii este
scrisă în ``localPos1``/``localRot1``, deci piesa rămâne exact unde era în falci, în loc să
sară în originea griperului.

Modulul are nevoie de ``pxr`` (USD), care vine din Isaac Sim sau din containerul de simulare;
importul se face în funcție, ca restul extensiei să poată fi importat fără USD.
"""

from __future__ import annotations

from typing import Any

# Calea articulației de prindere. Extensia o caută și o șterge după același nume.
GRASP_PATH = "/World/Robot1/GraspJoint"


def has_grasp(stage: Any, joint_path: str = GRASP_PATH) -> bool:
    """Adevărat dacă articulația de prindere există în scenă."""
    prim = stage.GetPrimAtPath(joint_path)
    return bool(prim and prim.IsValid())


def create_grasp_joint(
    stage: Any,
    gripper_path: str,
    part_path: str,
    joint_path: str = GRASP_PATH,
) -> str:
    """Creează articulația fixă dintre griper și piesă. Întoarce calea ei.

    Ridică :class:`ValueError` dacă vreunul dintre prim-uri lipsește: o articulație fără ținte
    ar face fizica să raporteze erori nedumeritoare la rulare.
    """
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    gripper = stage.GetPrimAtPath(gripper_path)
    part = stage.GetPrimAtPath(part_path)
    if not gripper or not gripper.IsValid():
        raise ValueError(f"griperul nu există la {gripper_path}")
    if not part or not part.IsValid():
        raise ValueError(f"piesa nu există la {part_path}")

    # Transformarea piesei în sistemul griperului, la momentul prinderii.
    time = Usd.TimeCode.Default()
    gripper_world = UsdGeom.Xformable(gripper).ComputeLocalToWorldTransform(time)
    part_world = UsdGeom.Xformable(part).ComputeLocalToWorldTransform(time)
    relative = gripper_world.GetInverse() * part_world
    transform = Gf.Transform(relative)

    joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
    joint.CreateBody0Rel().SetTargets([Sdf.Path(gripper_path)])
    joint.CreateBody1Rel().SetTargets([Sdf.Path(part_path)])
    joint.CreateLocalPos1Attr().Set(Gf.Vec3f(transform.GetTranslation()))
    quaternion = transform.GetRotation().GetQuat()
    joint.CreateLocalRot1Attr().Set(
        Gf.Quatf(quaternion.GetReal(), Gf.Vec3f(quaternion.GetImaginary()))
    )
    return joint_path


def remove_grasp_joint(stage: Any, joint_path: str = GRASP_PATH) -> bool:
    """Șterge articulația de prindere, dacă există. Întoarce True dacă a șters ceva."""
    prim = stage.GetPrimAtPath(joint_path)
    if not prim or not prim.IsValid():
        return False
    stage.RemovePrim(joint_path)
    return True
