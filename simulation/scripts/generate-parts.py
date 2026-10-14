#!/usr/bin/env python3
"""Generează în lot variantele roții dințate, împreună cu manifestul de etichete.

Regimul de lot este cel care produce datasetul de antrenare: pentru fiecare seed rezultă o
geometrie și o etichetă, ambele din aceeași decizie. În celulă, exact aceeași funcție este
apelată o dată per ciclu, cu un seed aleator.

Exemple::

    # 220 de variante, clase echilibrate, cu împărțire train/val/test
    ./simulation/scripts/generate-parts.py --count 220 --balanced --split

    # doar manifestul, cu eșantionare aleatoare (PASS 35%, restul egal)
    ./simulation/scripts/generate-parts.py --count 500 --out ml/artifacts/parts

    # 40 de variante din clasa TOOTH_MISSING, ca să completezi un set dezechilibrat
    ./simulation/scripts/generate-parts.py --count 40 --defect TOOTH_MISSING --seed0 100000

Ieșirea este ignorată de git (``ml/artifacts/``). Manifestul este sursa de adevăr pentru
etichete și nu are ce căuta pe calea de inferență.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTENSION_ROOT = ROOT / "simulation" / "extensions" / "cncdt.cnc_cell"
if str(EXTENSION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXTENSION_ROOT))

from cncdt.cnc_cell.parts.gear import (  # noqa: E402
    DEFECT_CLASSES,
    DEFECT_PASS,
    build_variant,
)
from cncdt.cnc_cell.parts.manifest import LabelRecord, write_manifest  # noqa: E402

SPLIT_FRACTIONS = (("train", 0.70), ("val", 0.15), ("test", 0.15))


def _label_of(variant) -> LabelRecord:
    return LabelRecord(
        part_id=variant.part_id,
        seed=variant.seed,
        verdict=variant.verdict,
        defect=variant.defect,
        params=variant.params.as_dict(),
    )


def _plan(count: int, seed0: int, balanced: bool, defect: str | None, rng: random.Random):
    """Perechile (seed, clasă). Cu ``balanced`` clasele se amestecă față de ordinea seed-urilor.

    Amestecul contează: dacă clasa ar fi legată de poziția seed-ului, o împărțire pe intervale
    de seed-uri ar băga clase diferite în split-uri diferite.
    """
    seeds = [seed0 + index for index in range(count)]
    if defect is not None:
        return [(seed, defect) for seed in seeds]
    if balanced:
        classes = [DEFECT_CLASSES[index % len(DEFECT_CLASSES)] for index in range(count)]
        rng.shuffle(classes)
        return list(zip(seeds, classes))
    return [(seed, None) for seed in seeds]


def _split(records: list[LabelRecord], rng: random.Random) -> dict[str, list[LabelRecord]]:
    """Împarte pe seed-uri, stratificat pe clase.

    Stratificarea contează: o împărțire pur aleatoare a unui set mic poate lăsa o clasă
    (de obicei PASS) aproape absentă din test, iar atunci metrica nu spune nimic. Fiecare
    clasă se împarte separat, cu aceleași proporții, și abia apoi se combină.
    """
    by_class: dict[str, list[LabelRecord]] = {}
    for record in records:
        by_class.setdefault(record.defect, []).append(record)

    parts: dict[str, list[LabelRecord]] = {name: [] for name, _ in SPLIT_FRACTIONS}
    for defect in sorted(by_class):
        order = list(by_class[defect])
        rng.shuffle(order)
        total = len(order)
        train_end = max(1, int(round(total * SPLIT_FRACTIONS[0][1])))
        val_end = train_end + max(1, int(round(total * SPLIT_FRACTIONS[1][1]))) if total >= 3 else train_end
        val_end = min(val_end, total)
        parts["train"].extend(order[:train_end])
        parts["val"].extend(order[train_end:val_end])
        parts["test"].extend(order[val_end:])
    for name in parts:
        rng.shuffle(parts[name])
    return parts


def _summary(records: list[LabelRecord]) -> str:
    counts = Counter(record.defect for record in records)
    passes = counts.get(DEFECT_PASS, 0)
    rejects = len(records) - passes
    classes = len(counts)
    return f"{len(records):>5} piese, {passes} PASS, {rejects} REJECT, {classes} clase"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generatorul de piese al celulei CNC.")
    parser.add_argument("--count", type=int, default=100, help="câte variante să genereze")
    parser.add_argument("--seed0", type=int, default=0, help="primul seed")
    parser.add_argument(
        "--out",
        default="ml/artifacts/parts",
        help="directorul de ieșire (manifestul ajunge în <out>/parts.jsonl)",
    )
    parser.add_argument(
        "--balanced",
        action="store_true",
        help="clase echilibrate, amestecate față de ordinea seed-urilor",
    )
    parser.add_argument(
        "--split",
        action="store_true",
        help="scrie și parts-train/val/test.jsonl, împărțite pe seed-uri",
    )
    parser.add_argument(
        "--usd",
        action="store_true",
        help="scrie și câte un fișier USD per variantă (cere pxr)",
    )
    parser.add_argument(
        "--defect",
        default=None,
        choices=DEFECT_CLASSES,
        help="forțează o singură clasă (implicit: eșantionare aleatoare, PASS cu pondere)",
    )
    parser.add_argument(
        "--shuffle-seed",
        type=int,
        default=0,
        help="seed-ul amestecului, ca datasetul să fie reproductibil",
    )
    args = parser.parse_args(argv)

    if args.count < 1:
        print("--count trebuie să fie cel puțin 1", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    usd_dir = out_dir / "usd"
    out_dir.mkdir(parents=True, exist_ok=True)

    writer = None
    if args.usd:
        from cncdt.cnc_cell.parts.usd import write_variant_to_file

        usd_dir.mkdir(parents=True, exist_ok=True)
        writer = write_variant_to_file

    plan_rng = random.Random(f"plan:{args.shuffle_seed}")
    split_rng = random.Random(f"split:{args.shuffle_seed}")

    records: list[LabelRecord] = []
    triangles = 0
    for seed, defect in _plan(args.count, args.seed0, args.balanced, args.defect, plan_rng):
        variant = build_variant(seed, defect=defect)
        records.append(_label_of(variant))
        triangles += len(variant.faces)
        if writer is not None:
            writer(variant, str(usd_dir / f"{variant.part_id}.usda"))

    write_manifest(out_dir / "parts.jsonl", records)
    print(f"variante: {len(records)}  (medie {triangles / len(records):.0f} triunghiuri)")
    print(f"manifest: {out_dir / 'parts.jsonl'}  —  {_summary(records)}")

    if args.split:
        parts = _split(records, split_rng)
        seen: set[str] = set()
        for name, items in parts.items():
            overlap = seen & {item.part_id for item in items}
            if overlap:
                print(f"suprapunere între split-uri: {sorted(overlap)[:3]}", file=sys.stderr)
                return 1
            seen |= {item.part_id for item in items}
            path = out_dir / f"parts-{name}.jsonl"
            write_manifest(path, items)
            print(f"  {name:<5} {_summary(items)}  ->  {path.name}")

    if args.usd:
        print(f"USD:      {usd_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
