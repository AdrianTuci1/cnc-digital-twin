"""Datasetul de inspecție construit din cadrele celulei MuJoCo.

Sesiunile scrise de ``run.py`` au un `frames.jsonl` cu toate punctele de vedere. Pentru capul de
inspecție interesează un singur cadru: ``arm2_wrist`` la ``belt2_pick``, adică exact poza pe care
o vede brațul cînd decide ruta. Eticheta vine din `session.json`, nu din numele fișierului.

Un ciclu cu o prindere ratată nu are o poză validă a piesei, deci se aruncă. Splitul se face pe
``variant_id``, nu pe ``part_id``: identificatorul piesei se repetă în fiecare sesiune
(``part-000001``), iar geometria aceleiași variante nu are voie să apară în două seturi.

Modulul este pur (fără MuJoCo, fără AWS), ca ieșirile să poată fi verificate în ``make test``.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

#: Cadrul care decide ruta, din `cncdt_mj/povs.py`.
SORTING_POV = "arm2_wrist"
SORTING_STAGE = "belt2_pick"

SPLITS = ("train", "val", "test")

#: Împărțirea pe variante; fiecare familie primește toate cele trei seturi.
RATIOS = {"train": 0.70, "val": 0.15, "test": 0.15}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Rîndurile unui fișier JSON Lines, ignorînd liniile goale."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def cycle_success(session_path: Path) -> dict[str, bool]:
    """Starea prinderilor pe ciclu, din `session.json`.

    ``grasp_success`` este cîmpul scris de sesiune; dacă lipsește, se citește lista de prinderi.
    O sesiune fără niciuna dintre ele nu raportează prinderi, deci ciclul se ia ca reușit:
    cadrele de sortare ajung în fișier doar pentru ciclurile care au trecut de preluare.
    """
    session = json.loads(session_path.read_text(encoding="utf-8"))
    result: dict[str, bool] = {}
    for cycle in session.get("cycles", []):
        cycle_id = str(cycle.get("cycle_id", ""))
        if not cycle_id:
            continue
        success = cycle.get("grasp_success")
        if success is None:
            grasps = cycle.get("grasps") or []
            success = all(bool(item.get("success")) for item in grasps) if grasps else True
        result[cycle_id] = bool(success)
    return result


def sorting_frames(session_dir: Path, *, require_success: bool = True) -> list[dict[str, Any]]:
    """Cadrele de sortare ale unei sesiuni, cu eticheta și calea absolută a imaginii."""
    success = cycle_success(session_dir / "session.json")
    rows: list[dict[str, Any]] = []
    for row in read_jsonl(session_dir / "frames.jsonl"):
        if row.get("pov") != SORTING_POV or row.get("stage") != SORTING_STAGE:
            continue
        cycle_id = str(row.get("cycle_id", ""))
        if require_success and not success.get(cycle_id, False):
            continue
        image = session_dir / str(row.get("path", ""))
        if not image.exists():
            continue
        item = dict(row)
        item["source"] = str(image)
        item["session"] = session_dir.name
        # Identitatea variantei se compune cu sesiunea: fiecare sesiune își numerotează piesele
        # de la același prefix, iar dedublarea pe variantă ar amesteca sesiuni diferite în
        # același split. Așa, o singură geometrie nu ajunge și în antrenare, și în test.
        item["variant_id"] = f"{session_dir.name}-{row.get('variant_id') or image.stem}"
        rows.append(item)
    return rows


def collect(run_dir: Path, *, require_success: bool = True) -> list[dict[str, Any]]:
    """Toate cadrele de sortare din directorul unei rulări, sesiune cu sesiune."""
    rows: list[dict[str, Any]] = []
    for session_dir in sorted(path for path in run_dir.iterdir() if path.is_dir()):
        if not (session_dir / "session.json").exists():
            continue
        rows.extend(sorting_frames(session_dir, require_success=require_success))
    return rows


def split_variants(rows: Iterable[Mapping[str, Any]], *, seed: int = 7,
                   ratios: Mapping[str, float] = RATIOS) -> dict[str, list[dict[str, Any]]]:
    """Împarte pe variante, cu fiecare familie prezentă în toate seturile."""
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    for row in rows:
        variant = str(row.get("variant_id") or row.get("path"))
        if variant in seen:
            continue
        seen.add(variant)
        by_family[str(row.get("family", ""))].append(dict(row))

    rng = random.Random(seed)
    result: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLITS}
    for family in sorted(by_family):
        items = by_family[family]
        rng.shuffle(items)
        count = len(items)
        if count == 0:
            continue
        # Cu puține variante, fiecare set primește cel puțin una, ca raportul să nu golească val/test.
        n_val = max(1, round(count * ratios["val"])) if count >= 3 else 0
        n_test = max(1, round(count * ratios["test"])) if count >= 3 else 0
        if n_val + n_test >= count:
            n_val = max(1, count // 3)
            n_test = max(1, count // 3)
        n_train = count - n_val - n_test
        result["train"].extend(items[:n_train])
        result["val"].extend(items[n_train:n_train + n_val])
        result["test"].extend(items[n_train + n_val:])
    return result


def write_dataset(rows_by_split: Mapping[str, Iterable[Mapping[str, Any]]],
                  out_dir: Path) -> dict[str, int]:
    """Copiază imaginile și scrie indexurile pe care le citește `train.py`.

    Numele fișierelor sînt cele din contractul de antrenare: ``images-train.jsonl`` etc., iar
    ``path`` este relativ la directorul datasetului.
    """
    images = out_dir / "images"
    images.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for split in SPLITS:
        rows = list(rows_by_split.get(split, []))
        index = out_dir / f"images-{split}.jsonl"
        with index.open("w", encoding="utf-8") as handle:
            for row in rows:
                variant = str(row.get("variant_id") or Path(str(row["path"])).stem)
                target = images / f"{variant}.png"
                if not target.exists():
                    shutil.copyfile(str(row["source"]), target)
                item = {
                    "path": f"images/{target.name}",
                    "part_id": row.get("part_id"),
                    "variant_id": variant,
                    "family": row.get("family"),
                    "defect": row.get("defect"),
                    "verdict": row.get("verdict"),
                    "cycle_id": row.get("cycle_id"),
                    "session": row.get("session"),
                }
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        counts[split] = len(rows)
    return counts


def summary(rows_by_split: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, Any]:
    """Numărul de imagini, familiile și verdictul, pe fiecare set."""
    result: dict[str, Any] = {}
    for split in SPLITS:
        rows = list(rows_by_split.get(split, []))
        families = sorted({str(row.get("family")) for row in rows})
        verdicts = {name: sum(1 for row in rows if row.get("verdict") == name)
                    for name in ("PASS", "REJECT")}
        result[split] = {"images": len(rows), "families": families, "verdicts": verdicts}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Construiește datasetul de inspecție din rulări")
    parser.add_argument("--runs", required=True, type=Path, help="directorul rulării MuJoCo")
    parser.add_argument("--out", required=True, type=Path, help="directorul datasetului")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--include-failed-grasps", action="store_true")
    args = parser.parse_args()

    rows = collect(args.runs, require_success=not args.include_failed_grasps)
    splits = split_variants(rows, seed=args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    counts = write_dataset(splits, args.out)
    report = summary(splits)
    (args.out / "dataset.json").write_text(
        json.dumps({"run": args.runs.name, "seed": args.seed, "splits": report},
                   indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"counts": counts, "splits": report}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
