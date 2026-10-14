"""Configurare comună pentru teste.

Testele importă trei familii de module, care în producție trăiesc în locuri diferite:

- ``cncdt_ml`` — pachetul comun, copiat în arhiva Lambda și folosit de antrenare
  (``ml/src``);
- ``core``, ``handler``, ``_shared`` — Lambda-ul de feature engineering; în arhiva de funcție
  acestea stau la rădăcină, deci testele le importă cu aceleași nume (``lambdas``,
  ``lambdas/feature_engineering``);
- ``cncdt.cnc_cell`` — extensia Isaac Sim (``simulation/extensions/cncdt.cnc_cell``);
  modulele pure nu cer Isaac Sim;
- ``cncdt_mj`` — geamănul MuJoCo (``simulation/mujoco``); părțile pure (``layout``, ``part``,
  ``checks``) nu cer ``mujoco``, iar scena se validează din venv-ul geamănului, cu
  ``make mj-check``.

Numele generice (``core``, ``handler``) sunt intenționate: reproduc exact ceea ce vede
runtime-ul Lambda. Directoarele celorlalte Lambda-uri **nu** se adaugă la ``sys.path``: au și
ele un ``handler.py``, care ar umbri modulul de feature engineering. Testele lor încarcă
modulele prin cale, cu nume unice, și înregistrează modulul pur în ``sys.modules``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

SEARCH_PATHS = (
    "ml/src",
    "ml/feature_engineering",
    "lambdas",
    "lambdas/feature_engineering",
    "simulation/extensions/cncdt.cnc_cell",
    "simulation/mujoco",
)

for _relative in SEARCH_PATHS:
    _path = ROOT / _relative
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
