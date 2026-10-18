"""Regulile verificării care nu ating motorul de fizică.

`view.py --check` are nevoie de ele, dar locul lor nu este `scene.py` — singurul modul care
importă `mujoco` (ADR 0012). Aici se lucrează doar pe nume de corpuri, deci regulile se pot
testa din venv-ul proiectului, unde `mujoco` nu există.
"""

from __future__ import annotations

from typing import Iterable

#: Separatorul dintre numele corpurilor într-o pereche de contact (`scene.contact_pairs`).
PAIR_SEPARATOR = "↔"

# Pragurile verificării. Stau aici, nu în `view.py`, fiindcă sunt reguli pure: testele din
# `.venv`-ul proiectului le pot citi fără motorul de fizică, iar `view.py --check` le
# folosește pe aceleași.

#: Câți pixeli trebuie să ocupe piesa ținută în cadrul camerei de pe flanșă. Camera stă la 14,4 cm
#: de punctul de strângere, cu 46° pe verticală: roata de 104 mm iese la ~392 px din 640, iar o
#: montare greșită — obiectivul întors spre cot — dă zero. Pragul prinde și o cameră prea
#: depărtată, la care piesa ajunge un blob de câteva zeci de pixeli, de neclasificat.
MIN_WRIST_PART_PX = 150.0

#: Cât spațiu liber cere obiectivul camerei de pe flanșă, pe orice rază a cadrului, în metri.
#: Sub câțiva centimetri, obiectivul stă în materialul brațului: cadrul iese „prin" mână, cu umbra
#: ei peste piesă, fiindcă fețele din spate nu se randează. Vezi montajul din `scene.py`.
MIN_LENS_CLEARANCE_M = 0.02

#: Cât spațiu liber cere obiectivul în toate direcțiile, nu doar în câmp. La 4 cm de cochilia
#: mîinii, camera stă în deschiderea ei și vede scena prin braț; montajul de acum dă ~60 mm.
MIN_LENS_SURROUND_M = 0.05

#: Toleranța la compararea yaw-ului cerut cu cel citit înapoi din scenă, în grade.
YAW_TOLERANCE_DEG = 0.1

#: Cât are voie să se miște o piesă la așezarea pe bandă, în metri.
MAX_SETTLE_DRIFT_M = 0.005

#: Cât de departe are voie să rămână scula de punctul de lucru când brațul l-a atins, în metri, și
#: cu cât are voie să fie orientarea ei greșită, în grade. Servo-urile au o eroare de regim
#: permanentă de câțiva milimetri, deci pragul prinde un braț care nu ajunge, nu unul care tremură.
#: Compartimentul cel mai jos și cel mai aproape de bază (`rack_wheel_100`, la 0,71 m în plan și
#: 9 cm sub umăr) cere brațul îndoit la limită; eroarea măsurată acolo este 21-23 mm. O ratare
#: adevărată — un punct în afara anvelopei — rămînea la peste 100 mm.
MAX_POSE_ERROR_M = 0.025
MAX_POSE_ANGLE_DEG = 5.0

#: Sufixul corpurilor care prind. Falcile ating piesa prin definiție — o strâng, o lasă pe bandă,
#: se apropie de universal —, deci nu intră în regula „brațul atinge celula".
FINGER_SUFFIX = "finger"


def _arm_of(name: str, prefixes: tuple[str, ...]) -> str | None:
    """Prefixul brațului căruia îi aparține un corp, ori ``None`` dacă nu e al niciunui braț."""
    for prefix in prefixes:
        if name.startswith(prefix):
            return prefix
    return None


def arm_foreign_contacts(pairs: Iterable[str], arm_prefixes: Iterable[str]) -> list[str]:
    """Perechile în care un braț atinge altceva decât propriile verigi.

    Autocontactele unui braț (``arm1_hand↔arm1_link5``) sunt normale: brațul are geome care se
    ating la capetele articulațiilor. Ce se caută este un braț care atinge un element al celulei,
    piesa ori celălalt braț — acolo verificarea are ceva de spus.
    """
    prefixes = tuple(arm_prefixes)
    foreign: list[str] = []
    for pair in pairs:
        left, _, right = pair.partition(PAIR_SEPARATOR)
        left_arm = _arm_of(left, prefixes)
        right_arm = _arm_of(right, prefixes)
        if left_arm is None and right_arm is None:
            continue
        if left_arm is not None and left_arm == right_arm:
            continue
        foreign.append(pair)
    return foreign


def arm_link_contacts(pairs: Iterable[str], arm_prefixes: Iterable[str]) -> list[str]:
    """Perechile în care un braț atinge ceva cu altceva decât falcile.

    Falcile sunt scula: ating piesa când o prind, banda când o lasă pe ea, universalul când o
    așază în mașină. Verigile, încheietura și flanșa nu au ce căuta în nimic, deci pentru ele
    regula este cea de mai sus, fără perechile de falci.
    """
    prefixes = tuple(arm_prefixes)
    links: list[str] = []
    for pair in arm_foreign_contacts(pairs, prefixes):
        left, _, right = pair.partition(PAIR_SEPARATOR)
        bodies = [name for name in (left, right) if _arm_of(name, prefixes) is not None]
        if all(name.endswith(FINGER_SUFFIX) for name in bodies):
            continue
        links.append(pair)
    return links
