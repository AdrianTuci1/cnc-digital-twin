"""Planul imaginilor de calitate pentru piesele generate procedural.

Datasetul de clasificare are nevoie de cadre curate, de sus, cu piesa întreagă în imagine și cu
unghiul de randare independent de geometrie. Aici stă partea **pură**: ce unghiuri primește fiecare
variantă, la ce distanță stă camera ca piesa să umple o fracțiune fixă din cadru, cum se numesc
fișierele și ce scrie în indexul care leagă imaginea de etichetă. Randarea propriu-zisă stă în
`cncdt_mj/part_render.py`, singurul modul nou care importă `mujoco`.

Indexul este pentru antrenare, nu pentru inferență: el poartă familia, defectul și verdictul, exact
ca manifestul de etichete. Percepția nu primește niciunul dintre ele, doar imaginea.

Unghiurile se trag dintr-un flux propriu, pornit din `render_seed` și din poziția variantei în
manifest, nu din sămânța geometriei. Așa condițiile de randare nu se leagă accidental de clasă:
dacă același seed ar alege și forma, și unghiul, modelul ar putea învăța unghiul în locul
defectului (`docs/10-dataset-piese.md`).
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: Sub ce director se scriu cadrele, față de rădăcina datasetului de piese.
IMAGE_DIR = "images"

#: Indexul complet; pe lângă el se scriu și câte un index pe split, dacă manifestul are split-uri.
INDEX_FILE = "images.jsonl"

#: Câte cadre primește fiecare variantă implicit: destule ca unghiul să nu se lege de clasă.
DEFAULT_VIEWS = 4

#: Latura cadrului, în pixeli. La Ø100 mm piesa umple ~560 px, adică ~5,6 px/mm, peste rezoluția
#: camerei de pe flanșă (3,6 px/mm), deci cel mai fin defect din catalog rămâne vizibil.
DEFAULT_SIZE = 640

#: Câmpul vizual al camerei de randare, în grade.
FOVY_DEG = 45.0

#: Cât din înălțimea cadrului ocupă piesa. Peste 0,9, vârfurile dinților ating marginea la unele
#: rotații; sub 0,8, rezoluția pe milimetru scade fără folos.
FILL_FRACTION = 0.88

#: Înclinarea maximă față de verticală. Cadrul rămâne aproape de sus, ca alezajul, găurile și dinții
#: să se vadă; o înclinare mare ar ascunde tocmai defectele de siluetă.
MAX_TILT_DEG = 6.0

#: Split-urile pe care le poate scrie generatorul de piese.
SPLITS = ("train", "val", "test")


@dataclass(frozen=True)
class View:
    """Un cadru al unei piese: unghiul camerei și direcția luminii, trase independent."""

    index: int
    azimuth_deg: float
    elevation_deg: float
    light_azimuth_deg: float
    light_elevation_deg: float


def plan_views(ordinal: int, count: int = DEFAULT_VIEWS,
               render_seed: int = 0) -> tuple[View, ...]:
    """Unghiurile celor ``count`` cadre ale variantei de pe poziția ``ordinal``.

    ``ordinal`` este poziția în manifest, nu sămânța: fluxul de randare rămâne astfel separat de
    fluxul geometriei. Cu aceleași argumente rezultatul este identic, deci un dataset se poate
    regenera.
    """
    if count < 1:
        raise ValueError("o variantă are nevoie de cel puțin un cadru")
    rng = random.Random(f"render:{render_seed}:{ordinal}")
    return tuple(
        View(
            index=index,
            azimuth_deg=round(rng.uniform(0.0, 360.0), 3),
            elevation_deg=round(-90.0 + rng.uniform(0.0, MAX_TILT_DEG), 3),
            light_azimuth_deg=round(rng.uniform(0.0, 360.0), 3),
            light_elevation_deg=round(rng.uniform(45.0, 70.0), 3),
        )
        for index in range(count)
    )


def camera_distance_m(outer_radius_mm: float,
                      fill_fraction: float = FILL_FRACTION,
                      fovy_deg: float = FOVY_DEG) -> float:
    """Distanța camerei ca diametrul piesei să ocupe ``fill_fraction`` din înălțimea cadrului.

    Distanța crește cu familia: o piesă Ø100 are nevoie de un cadru mai larg decât una Ø56. Nu se
    păstrează aceeași distanță pentru toate familiile, fiindcă atunci piesa mică ar ieși la
    jumătatea rezoluției, iar cea mare ar atinge marginile.
    """
    if outer_radius_mm <= 0.0:
        raise ValueError("raza piesei trebuie să fie pozitivă")
    if not 0.0 < fill_fraction < 1.0:
        raise ValueError("fracțiunea din cadru trebuie să fie între 0 și 1")
    diameter_m = 2.0 * float(outer_radius_mm) / 1000.0
    half_angle = math.radians(float(fovy_deg) / 2.0)
    return diameter_m / (2.0 * math.tan(half_angle) * float(fill_fraction))


def image_relpath(record: Any, view: View) -> str:
    """Calea relativă a unui cadru: familie, clasă, apoi identificatorul și numărul cadrului.

    Familia și clasa sunt identificatori controlați de generator, deci nu au caractere care să
    ceară escape. Numele variantei este deja prefixat cu ``part-``.
    """
    return (f"{IMAGE_DIR}/{record.family}/{record.defect}/"
            f"{record.part_id}-v{view.index:02d}.png")


def split_map(directory: str | Path) -> dict[str, str]:
    """Identificatorul piesei -> split, citit din ``parts-<split>.jsonl`` când fișierele există.

    Generatorul scrie split-urile doar cu ``--split``. Fără ele, toate cadrele intră în split-ul
    ``all``; indexul spune asta explicit, ca antrenarea să nu ghicească.
    """
    found: dict[str, str] = {}
    for split in SPLITS:
        path = Path(directory) / f"parts-{split}.jsonl"
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                part_id = str(json.loads(line)["part_id"])
                if part_id in found:
                    raise ValueError(
                        f"{part_id} apare în două split-uri: {found[part_id]} și {split}")
                found[part_id] = split
    return found


def split_of(record: Any, splits: Mapping[str, str]) -> str:
    """Split-ul variantei; ``all`` când manifestul nu a fost împărțit."""
    return splits.get(record.part_id, "all")


def index_row(record: Any, view: View, path: str, split: str,
              width: int = DEFAULT_SIZE, height: int = DEFAULT_SIZE,
              distance_m: float | None = None) -> dict[str, Any]:
    """Un rând din index: imaginea, condițiile de randare și eticheta ei de antrenare."""
    distance = (camera_distance_m(float(record.params["outer_radius"]))
                if distance_m is None else float(distance_m))
    return {
        "part_id": str(record.part_id),
        "seed": int(record.seed),
        "family": str(record.family),
        "defect": str(record.defect),
        "verdict": str(record.verdict),
        "split": split,
        "view": int(view.index),
        "path": path,
        "camera": "top",
        "width": int(width),
        "height": int(height),
        "fovy_deg": float(FOVY_DEG),
        "distance_m": round(distance, 6),
        "azimuth_deg": float(view.azimuth_deg),
        "elevation_deg": float(view.elevation_deg),
        "light_azimuth_deg": float(view.light_azimuth_deg),
        "light_elevation_deg": float(view.light_elevation_deg),
    }


def write_index(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> int:
    """Scrie un index JSON Lines, câte un rând pe imagine. Întoarce numărul de rânduri."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            count += 1
    return count


def write_indexes(directory: str | Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Path]:
    """Scrie indexul complet și câte unul pe fiecare split prezent în rânduri.

    Antrenarea citește split-ul direct, fără să mai filtreze; indexul complet rămâne pentru
    verificarea că toate cadrele declarate există.
    """
    root = Path(directory)
    written = {"all": root / INDEX_FILE}
    write_index(written["all"], rows)
    for split in sorted({str(row["split"]) for row in rows}):
        if split == "all":
            continue
        name = f"images-{split}.jsonl"
        written[split] = root / name
        write_index(written[split], [row for row in rows if str(row["split"]) == split])
    return written


def split_rows(rows: Sequence[Mapping[str, Any]], split: str) -> list[Mapping[str, Any]]:
    """Rândurile unui split, în ordinea din index."""
    return [row for row in rows if str(row["split"]) == split]


def read_index(path: str | Path) -> list[dict[str, Any]]:
    """Citește un index JSON Lines, sărind liniile goale."""
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def merge_index(directory: str | Path) -> list[dict[str, Any]]:
    """Adună indexul unei rulări, dintr-un singur fișier ori din felii paralele.

    O rulare pe Modal scrie cîte un `images-shard-NN.jsonl` pe container. Aici se unesc, se
    verifică faptul că nicio pereche (piesă, cadru) nu apare de două ori și se așază într-o
    ordine stabilă, ca manifestul să nu depindă de ordinea în care s-au terminat containerele.
    """
    root = Path(directory)
    complete = root / INDEX_FILE
    if complete.is_file():
        rows = read_index(complete)
    else:
        shards = sorted(root.glob("images-shard-*.jsonl"))
        if not shards:
            raise ValueError(f"niciun index de imagini în {root}")
        rows = []
        for path in shards:
            rows.extend(read_index(path))
    seen: dict[tuple[str, int], str] = {}
    for row in rows:
        key = (str(row["part_id"]), int(row["view"]))
        path = str(row["path"])
        if key in seen:
            raise ValueError(f"{key[0]} cadrul {key[1]} apare de două ori: "
                             f"{seen[key]} și {path}")
        seen[key] = path
    return sorted(rows, key=lambda row: (str(row["part_id"]), int(row["view"])))
