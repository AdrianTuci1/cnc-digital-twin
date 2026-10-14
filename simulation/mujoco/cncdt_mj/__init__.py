"""Geamănul MuJoCo al celulei CNC: scena, verificarea și randarea ei.

Pachetul e împărțit după regula din `AGENTS.md` și din `docs/11-geaman-mujoco.md` §7: logica
pură stă separat de cea care atinge motorul de fizică.

* :mod:`cncdt_mj.layout` — așezarea în plan (importată din `docs/diagrams/cell_layout.py`) și
  înălțimile documentate; **fără** MuJoCo;
* :mod:`cncdt_mj.part` — piesa din scenă, construită de generatorul procedural existent
  (`simulation/extensions/cncdt.cnc_cell`), scalată în metri; **fără** MuJoCo;
* :mod:`cncdt_mj.scene` — compunerea scenei: `scene/cell.xml` plus cele două brațe Panda
  atașate programatic. Singurul modul care importă `mujoco`.

Consecința practică: teste din `tests/unit/` pot importa primele două module în `.venv`-ul
proiectului, unde `mujoco` nu este instalat (ADR 0012), iar scena se verifică din venviul
geamănului, cu `view.py --check`.
"""

from __future__ import annotations

from pathlib import Path

# Directorul `simulation/mujoco/`, adică rădăcina livrabilului.
MUJOCO_DIR = Path(__file__).resolve().parent.parent

# Rădăcina repo-ului: `simulation/mujoco/cncdt_mj/__init__.py` -> sus de trei ori.
REPO_ROOT = MUJOCO_DIR.parent.parent
