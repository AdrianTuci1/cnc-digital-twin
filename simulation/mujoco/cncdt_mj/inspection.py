"""Clientul de inspecție al geamănului: cererea către endpoint și citirea verdictului.

Geamănul nu are `boto3` în venv (ADR 0012), iar apelul către endpoint se face prin AWS CLI,
cu credențialele rolului de operator. Modulul ține împreună trei lucruri, ca geamănul să aibă
un singur loc de citit:

* **cererea** — un cadru PNG în base64, plus identificatorii postului de inspecție. Eticheta
  piesei nu intră niciodată în cerere: `cnc.qa.snapshot.v1.json` este schemă închisă, iar
  modelul trebuie să vadă măsurători, nu răspunsul (ADR 0013);
* **răspunsul** — probabilitatea de defect și capul de familie, validate ca formă;
* **ruta** — unde ajunge piesa. Un cadru nejugat, un verdict de rebut ori o familie sub prag
  aleg rebutul, latura de siguranță din ADR 0009.

Partea fără rețea (`request_body`, `parse_response`, `Inspection`) este pură și se testează
fără AWS. `invoke` este subțire intenționat: o comandă, un fișier de răspuns, nicio retragere.
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Tipul cererii; containerul de inferență citește JSON.
CONTENT_TYPE = "application/json"

#: Formatul imaginii din cerere. PNG este ce scrie `camera.write_png`.
IMAGE_FORMAT = "png"

VERDICT_PASS = "PASS"
VERDICT_REJECT = "REJECT"

#: Destinațiile din celulă: compartimentul familiei ori lada de rebut.
ROUTE_RACK = "RACK"
ROUTE_SCRAP = "SCRAP"

#: Rolul cadrului în contractul de inspecție; camera brațului 2 este cea care decide ruta.
SORTING_ROLE = "sorting"

DEFAULT_REGION = "eu-central-1"

#: Pragul de defect din `deployment.json`. Endpoint-ul întoarce probabilitatea, deci pragul
#: călătorește cu clientul; dacă modelul se schimbă, valoarea se schimbă în același loc.
#: 0,91 este pragul cu care e configurat `cncdt-dev-vision-qa` (`vision_qa_defect_threshold`
#: din `stacks/platform/envs/dev.tfvars`), ca geamănul să judece la fel cu Lambda-ul de inspecție.
DEFAULT_THRESHOLD = 0.91

#: Sub acest nivel, numele familiei este o ghicitoare, iar piesa merge la rebut.
DEFAULT_FAMILY_THRESHOLD = 0.80

#: Serverless pornește containerul în zeci de secunde, până la câteva minute.
DEFAULT_TIMEOUT_S = 300


class ResponseError(ValueError):
    """Răspunsul endpoint-ului nu respectă contractul ori apelul a eșuat."""


@dataclass(frozen=True)
class Inspection:
    """Verdictul și ruta unei singure piese, așa cum ies din răspunsul modelului."""

    part_id: str
    defect_probability: float
    judged: bool
    family: str | None = None
    family_probability: float | None = None
    threshold: float = DEFAULT_THRESHOLD
    family_threshold: float = DEFAULT_FAMILY_THRESHOLD

    @property
    def verdict(self) -> str:
        """PASS doar când modelul a judecat cadrul și probabilitatea e sub prag."""
        if not self.judged:
            return VERDICT_REJECT
        return VERDICT_REJECT if self.defect_probability >= self.threshold else VERDICT_PASS

    @property
    def route(self) -> str:
        """Compartimentul familiei ori rebutul; necunoscutul merge la rebut."""
        if self.verdict == VERDICT_REJECT:
            return ROUTE_SCRAP
        if self.family is None or self.family_probability is None:
            return ROUTE_SCRAP
        if self.family_probability < self.family_threshold:
            return ROUTE_SCRAP
        return ROUTE_RACK

    @property
    def reason(self) -> str:
        """De ce a ieșit ruta; se scrie în jurnalul sesiunii."""
        if not self.judged:
            return "unjudged"
        if self.defect_probability >= self.threshold:
            return "verdict"
        if self.family is None or self.family_probability is None:
            return "family_missing"
        if self.family_probability < self.family_threshold:
            return "family_uncertain"
        return "model"


def request_body(*, part_id: str, cycle_id: str, image: bytes, camera: str,
                 resolution: tuple[int, int],
                 camera_role: str = SORTING_ROLE) -> bytes:
    """Corpul cererii pentru un cadru: identificatori, cameră și PNG-ul în base64.

    Funcția primește numai măsurători. Eticheta piesei (`family`, `defect`, `verdict`) nu are
    parametru aici, ca o scurgere să fie o schimbare de semnătură, nu o distragere.
    """
    instance: dict[str, Any] = {
        "part_id": str(part_id),
        "cycle_id": str(cycle_id),
        "camera_role": camera_role,
        "camera": camera,
        "resolution": [int(resolution[0]), int(resolution[1])],
        "image_captured": True,
        "image": {
            "format": IMAGE_FORMAT,
            "encoding": "base64",
            "data": base64.b64encode(image).decode("ascii"),
        },
    }
    return json.dumps({"instances": [instance]}, ensure_ascii=False).encode("utf-8")


def parse_response(payload: bytes | str | Mapping[str, Any], *,
                   threshold: float = DEFAULT_THRESHOLD,
                   family_threshold: float = DEFAULT_FAMILY_THRESHOLD,
                   expected_part_id: str | None = None) -> Inspection:
    """Validează răspunsul endpoint-ului și îl întoarce ca :class:`Inspection`.

    Un răspuns pentru altă piesă este refuzat: ruta se decide pe identificatorul cererii, iar
    o potrivire greșită ar trimite piesa altui client în compartiment.
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ResponseError(f"răspunsul nu este JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ResponseError("răspunsul nu este un obiect JSON")

    predictions = payload.get("predictions")
    if not isinstance(predictions, list) or len(predictions) != 1:
        raise ResponseError("răspunsul trebuie să conțină exact o predicție")
    item = predictions[0]
    if not isinstance(item, Mapping):
        raise ResponseError("predicția nu este un obiect JSON")

    part_id = str(item.get("part_id", ""))
    if expected_part_id is not None and part_id != str(expected_part_id):
        raise ResponseError(
            f"răspunsul este pentru piesa {part_id or '-'}, cererea pentru {expected_part_id}")

    judged = item.get("judged", True)
    if not isinstance(judged, bool):
        raise ResponseError("câmpul 'judged' trebuie să fie boolean")

    probability = _probability(item.get("defect_probability"), "defect_probability")
    family = item.get("family")
    family_probability = item.get("family_probability")
    if family is not None and not isinstance(family, str):
        raise ResponseError("câmpul 'family' trebuie să fie text")
    if family_probability is not None:
        family_probability = _probability(family_probability, "family_probability")

    return Inspection(
        part_id=part_id,
        defect_probability=probability,
        judged=judged,
        family=family,
        family_probability=family_probability,
        threshold=float(threshold),
        family_threshold=float(family_threshold),
    )


def invoke(endpoint: str, body: bytes, *, region: str = DEFAULT_REGION,
           timeout_s: int = DEFAULT_TIMEOUT_S,
           runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> bytes:
    """Trimite corpul la endpoint și întoarce răspunsul brut.

    `aws sagemaker-runtime invoke-endpoint` scrie corpul răspunsului într-un fișier, iar
    metadatele pe stdout. Fișierele stau într-un director temporar, ca două apeluri paralele
    să nu se calce. `runner` este injectabil pentru teste.
    """
    with tempfile.TemporaryDirectory(prefix="cncdt-qa-") as directory:
        request = Path(directory) / "request.json"
        response = Path(directory) / "response.json"
        request.write_bytes(body)
        command = [
            "aws", "sagemaker-runtime", "invoke-endpoint",
            "--endpoint-name", endpoint,
            "--content-type", CONTENT_TYPE,
            "--body", f"fileb://{request}",
            "--region", region,
            str(response),
        ]
        completed = runner(command, capture_output=True, text=True, timeout=timeout_s)
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip()
            raise ResponseError(f"apelul endpoint-ului a eșuat: {detail or completed.returncode}")
        if not response.exists():
            raise ResponseError("endpoint-ul nu a scris niciun răspuns")
        return response.read_bytes()


def inspect_frame(frame: Path, *, endpoint: str, region: str = DEFAULT_REGION,
                  part_id: str, cycle_id: str, camera: str,
                  resolution: tuple[int, int],
                  threshold: float = DEFAULT_THRESHOLD,
                  family_threshold: float = DEFAULT_FAMILY_THRESHOLD) -> Inspection:
    """Trimite cadrul de sortare endpoint-ului și întoarce verdictul validat.

    Punctul comun al sesiunii de colectare (`run.py --route vision`) și al viewer-ului
    (`view.py --view --demo --route vision`): cererea duce cadrul și identificatorii, iar
    răspunsul este verificat înainte să ajungă la braț.
    """
    body = request_body(part_id=part_id, cycle_id=cycle_id, image=frame.read_bytes(),
                        camera=camera, resolution=resolution)
    answer = invoke(endpoint, body, region=region)
    return parse_response(answer, threshold=threshold,
                          family_threshold=family_threshold,
                          expected_part_id=part_id)


def _probability(value: Any, field: str) -> float:
    """Un număr în [0, 1], cu eroare explicită pentru orice altceva."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResponseError(f"câmpul '{field}' trebuie să fie număr")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ResponseError(f"câmpul '{field}' iese din [0, 1]: {number}")
    return number
