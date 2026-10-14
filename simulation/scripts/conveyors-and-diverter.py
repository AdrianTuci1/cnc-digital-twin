#!/usr/bin/env python3
"""Pregătește benzile care transportă piesa și explică devierea rebutului.

Se rulează **din Isaac Sim** (Script Editor), nu cu `python.sh`: are nevoie de Kit pentru
extensii, comenzi și OmniGraph.

    exec(open("/sim/scripts/conveyors-and-diverter.py").read())
    setup_conveyors()

De ce nu face totul singur: nodul de conveyor al NVIDIA se creează din meniu
(`Create > Isaac Sim > Warehouse Items > Conveyor`) și își scrie graful OmniGraph peste
prim-ul selectat. Scriptul de față pregătește terenul (activează extensiile, citește setul de
date al pieselor de bandă și spune exact ce prim trebuie selectat și ce parametri are nevoie),
dar lasă crearea grafului pe seama comenzii oficiale, ca să nu inventăm o structură de graf
care se schimbă între versiuni.

Metoda de decizie rebut / piese bune:

1. verdictul QA vine la extensie pe topicul `qa-verdict` (Lambda de inspecție, sau butonul de
   defect forțat din panou);
2. la REJECT extensia ridică opritorul din `/World/Scrap/Piston` cu 0,12 m
   (`PISTON_PUSH_OFFSET`), adică exact în calea piesei;
3. lama fixă `/World/Scrap/Guide` și jgheabul trimit piesa în lada de rebut;
4. la OK opritorul rămâne la nivelul benzii, iar piesa trece în lada de piese bune.

Varianta fără opritor, în care banda decide singură: piesa `ConveyorBelt_A20` din
`Isaac/Props/Conveyors` este o joncțiune `Y_MERGE` cu **două trasee de role independente**
(`Rollers` și `Rollers_01` în `track_types.json`). Cu ambele trasee pornite, piesa se
împarte; cu traseul de rebut pornit doar la REJECT, banda sortează fizic.
"""

from __future__ import annotations

import json
import os

# Benzile din scenă și prim-ul de acționare al fiecăreia. Numele vin din setul de date al
# extensiei de conveyor (`data/track_types.json`): piesele A05 au un singur set de role.
CONVEYORS = {
    "/World/Conveyor1": {"asset": "ConveyorBelt_A05", "node": "Rollers"},
    "/World/Conveyor2": {"asset": "ConveyorBelt_A05", "node": "Rollers"},
}

EXTENSIONS = ("isaacsim.asset.gen.conveyor", "isaacsim.asset.gen.conveyor.ui")

# viteza de transport, în m/s; setul de date dă doar scalarea animației texturii
DEFAULT_VELOCITY = 0.4


def _log(message: str) -> None:
    try:
        import carb

        carb.log_info(f"[conveyors] {message}")
    except Exception:
        print(f"[conveyors] {message}", flush=True)


def _dataset() -> dict:
    """Citește setul de date al pieselor de bandă din extensia de conveyor."""
    import omni.kit.app

    manager = omni.kit.app.get_app().get_extension_manager()
    for extension in ("isaacsim.asset.gen.conveyor.ui", "isaacsim.asset.gen.conveyor"):
        extension_id = manager.get_enabled_extension_id(extension)
        if not extension_id:
            continue
        path = os.path.join(manager.get_extension_path(extension_id), "data", "track_types.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return json.load(handle).get("assets", {})
    return {}


def enable_extensions() -> list[str]:
    """Activează extensiile de conveyor. Întoarce lista celor activate acum."""
    import omni.kit.app

    manager = omni.kit.app.get_app().get_extension_manager()
    enabled = []
    for extension in EXTENSIONS:
        if manager.is_extension_enabled(extension):
            continue
        manager.set_extension_enabled_immediate(extension, True)
        enabled.append(extension)
    if enabled:
        _log(f"extensii activate: {', '.join(enabled)}")
    else:
        _log("extensiile de conveyor erau deja active")
    return enabled


def setup_conveyors() -> dict:
    """Activează extensiile și raportează ce trebuie configurat pe fiecare bandă.

    Întoarce un dicționar cu parametrii fiecărei benzi, ca să poată fi folosit din alte
    scripturi (de exemplu pentru a porni traseul de rebut doar la REJECT).
    """
    try:
        import omni.usd
    except ImportError as exc:  # pragma: no cover - doar în afara Kit.
        raise RuntimeError("rulați scriptul din Script Editor (are nevoie de omni.usd)") from exc

    enable_extensions()
    assets = _dataset()
    stage = omni.usd.get_context().get_stage()
    plan: dict[str, dict] = {}

    for path, spec in CONVEYORS.items():
        prim = stage.GetPrimAtPath(path) if stage else None
        if prim is None or not prim.IsValid():
            _log(f"ATENȚIE: {path} lipsește; rulați build-scene.py întâi")
            continue

        metadata = assets.get(spec["asset"], {})
        if not metadata:
            _log(f"ATENȚIE: {spec['asset']} nu apare în setul de date al extensiei")
        nodes = metadata.get("conveyor_nodes", {})
        direction = nodes.get(spec["node"], {}).get("direction", [1.0, 0.0, 0.0])
        plan[path] = {
            "asset": spec["asset"],
            "node": spec["node"],
            "direction": direction,
            "velocity": DEFAULT_VELOCITY,
            "animate_scale": nodes.get(spec["node"], {}).get("animate_scale", 0.5),
        }
        _log(
            f"{path} ({spec['asset']}, stil {metadata.get('style', '?')}): selectați "
            f"'{spec['node']}', apoi Create > Isaac Sim > Warehouse Items > Conveyor; "
            f"direction={direction}, velocity={DEFAULT_VELOCITY} m/s"
        )

    _log(
        "deviere rebut: la REJECT extensia ridică /World/Scrap/Piston cu 0,12 m; "
        "lama /World/Scrap/Guide duce piesa în lada de rebut"
    )
    _log(
        "alternativă fizică: înlocuiți capătul benzii cu ConveyorBelt_A20 (Y_MERGE) și porniți "
        "traseul 'Rollers_01' doar la REJECT"
    )
    return plan
