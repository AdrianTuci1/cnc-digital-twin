"""Contractul pur al containerului de inferență vizuală.

Aici stau numai decodarea cererii și formatarea răspunsului, ca testele să ruleze fără
``torch``. ``inference.py`` păstrează partea grea: încărcarea modelului și trecerea prin
rețea.

Cererea este cea construită de ``lambdas/qa_vision_inference/qa_vision_core.endpoint_body``:
un obiect cu ``instances``, fiecare cu identificatorii cadrului și, când există, imaginea
PNG în base64. Răspunsul are aceeași lungime și ordine, fiindcă Lambda-ul asociază
probabilitatea cu cadrul după poziție.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from typing import Any

#: Probabilitatea de umplutură pentru un cadru fără imagine. Nu este o judecată: răspunsul
#: poartă `judged: false`, iar Lambda-ul nu publică verdict, deci piesa urmează ruta de rezervă.
UNJUDGED_PROBABILITY = 0.5


class RequestError(ValueError):
    """Cererea nu respectă contractul endpoint-ului."""


def parse_instances(payload: Any) -> list[dict[str, Any]]:
    """Lista de intrări din corpul cererii, validată ca formă."""
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RequestError(f"corpul cererii nu este JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise RequestError("corpul cererii nu este un obiect JSON")
    instances = payload.get("instances")
    if not isinstance(instances, list) or not instances:
        raise RequestError("cererea nu conține lista 'instances'")
    parsed: list[dict[str, Any]] = []
    for item in instances:
        if not isinstance(item, Mapping):
            raise RequestError("fiecare intrare trebuie să fie un obiect JSON")
        parsed.append(dict(item))
    return parsed


def image_bytes(instance: Mapping[str, Any]) -> bytes | None:
    """Octeții imaginii base64, sau ``None`` când cadrul nu are fotografie."""
    image = instance.get("image")
    if not isinstance(image, Mapping):
        return None
    if image.get("encoding") != "base64":
        return None
    data = image.get("data")
    if not isinstance(data, str) or not data:
        return None
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return None


def prediction(part_id: str, probability: float, judged: bool = True, *,
               family: str | None = None,
               family_probability: float | None = None) -> dict[str, Any]:
    """O predicție, în forma pe care o citește Lambda-ul de inspecție.

    ``family`` și ``family_probability`` apar doar când modelul a văzut imaginea și are un
    cap de familie; un cadru nejugat rămâne fără ele, ca Lambda-ul să nu inventeze o rută.
    """
    item = {
        "part_id": part_id,
        "defect_probability": round(min(max(float(probability), 0.0), 1.0), 6),
        "judged": judged,
    }
    if judged and family is not None and family_probability is not None:
        item["family"] = str(family)
        item["family_probability"] = round(
            min(max(float(family_probability), 0.0), 1.0), 6)
    return item


def response(predictions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Răspunsul endpoint-ului, cu aceeași listă pe care o așteaptă Lambda-ul."""
    return {"predictions": [dict(item) for item in predictions]}
