#!/usr/bin/env python3
"""Randează imaginile de calitate ale pieselor, din manifestul de etichete.

Fiecare variantă din manifest primește cîteva cadre de sus, cu unghiuri trase independent de
geometrie. Ieșirea este un director de imagini plus un index JSON Lines care leagă fiecare PNG de
familia, defectul și verdictul piesei. Indexul este pentru antrenare; inferența primește doar
imaginea.

Rularea cere venv-ul geamănului, singurul care are `mujoco` (ADR 0012). Pe Linux fără afișaj,
inclusiv în containerele Modal, se pornește cu `MUJOCO_GL=egl`.

Exemple::

    # manifestul există deja (generate-parts.py --split), 4 cadre pe piesă
    simulation/mujoco/.venv/bin/python simulation/mujoco/render_parts.py

    # probă scurtă, 20 de piese, un singur cadru
    simulation/mujoco/.venv/bin/python simulation/mujoco/render_parts.py \
        --limit 20 --views 1 --width 320 --height 320 --out /tmp/parts

    # o felie dintr-o rulare paralelă (Modal): fiecare container ia un bloc contiguu
    simulation/mujoco/.venv/bin/python simulation/mujoco/render_parts.py \
        --out /corpus/parts/run-... --shard 3 --shards 8
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

MUJOCO_DIR = Path(__file__).resolve().parent
REPO_ROOT = MUJOCO_DIR.parent.parent
for path in (MUJOCO_DIR, REPO_ROOT / "simulation" / "extensions" / "cncdt.cnc_cell"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cncdt.cnc_cell.parts.gear import build_variant, build_variant_from_params  # noqa: E402
from cncdt.cnc_cell.parts.manifest import read_manifest  # noqa: E402
from cncdt_mj import part_images, part_render  # noqa: E402


def build_variant_of(record: Any) -> Any:
    """Reconstruiește geometria din manifest; fără parametri, din sămânță și clasă.

    Manifestul curent poartă toți parametrii, deci reconstrucția este exactă. Un rând vechi,
    fără ei, se poate regenera din sămânță, fiindcă generatorul este determinist.
    """
    if record.params:
        return build_variant_from_params(
            record.seed, record.defect, record.params, record.family)
    return build_variant(record.seed, defect=record.defect, family=record.family)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", default=str(REPO_ROOT / "ml" / "artifacts" / "parts"
                                                  / "parts.jsonl"),
                        help="manifestul de etichete (implicit ml/artifacts/parts/parts.jsonl)")
    parser.add_argument("--out", default=None,
                        help="directorul de ieșire (implicit directorul manifestului)")
    parser.add_argument("--views", type=int, default=part_images.DEFAULT_VIEWS,
                        help=f"câte cadre pe piesă (implicit {part_images.DEFAULT_VIEWS})")
    parser.add_argument("--width", type=int, default=part_images.DEFAULT_SIZE,
                        help=f"lățimea cadrului (implicit {part_images.DEFAULT_SIZE})")
    parser.add_argument("--height", type=int, default=part_images.DEFAULT_SIZE,
                        help=f"înălțimea cadrului (implicit {part_images.DEFAULT_SIZE})")
    parser.add_argument("--render-seed", type=int, default=0,
                        help="sămânța condițiilor de randare, independentă de geometrie")
    parser.add_argument("--shard", type=int, default=0,
                        help="indexul feliei, pentru rularea în containere paralele")
    parser.add_argument("--shards", type=int, default=1,
                        help="câte felii are rularea (implicit 1, tot manifestul)")
    parser.add_argument("--overwrite", action="store_true",
                        help="reserie cadrele care există deja")
    parser.add_argument("--limit", type=int, default=None,
                        help="randează doar primele N piese (probă scurtă)")
    parser.add_argument("--family", action="append", default=None,
                        help="limitează la una ori mai multe familii")
    parser.add_argument("--defect", action="append", default=None,
                        help="limitează la una ori mai multe clase")
    parser.add_argument("--plan", action="store_true",
                        help="tipărește câte cadre ar ieși, fără să randeze")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = Path(args.manifest)
    if not manifest.is_file():
        print(f"lipsește manifestul {manifest}; rulează întîi generate-parts.py", file=sys.stderr)
        return 1

    records = read_manifest(manifest)
    if args.family:
        wanted = set(args.family)
        records = [record for record in records if record.family in wanted]
    if args.defect:
        wanted = set(args.defect)
        records = [record for record in records if record.defect in wanted]
    if args.limit is not None:
        records = records[:max(0, args.limit)]
    if not records:
        print("niciun rînd după filtre", file=sys.stderr)
        return 1

    start, count = part_images.shard_range(len(records), args.shard, args.shards)
    expected = count * args.views
    out_dir = Path(args.out) if args.out else manifest.parent
    print(f"Piese: {len(records)} din {manifest} | felia {args.shard}/{args.shards}: "
          f"[{start}, {start + count}) | {args.views} cadre/piesă | {args.width}×{args.height}")
    if args.plan:
        print(f"Ar randa {expected} imagini în {out_dir}")
        return 0

    splits = part_images.split_map(manifest.parent)
    started = time.monotonic()
    rows = part_render.render_records(
        records, out_dir, build_variant_of, views_per_record=args.views,
        render_seed=args.render_seed, width=args.width, height=args.height,
        overwrite=args.overwrite, start=start, count=count, splits=splits)

    if args.shards == 1:
        part_images.write_indexes(out_dir, rows)
        index_path = out_dir / part_images.INDEX_FILE
    else:
        index_path = out_dir / f"images-shard-{args.shard:02d}.jsonl"
        part_images.write_index(index_path, rows)

    families = Counter(row["family"] for row in rows)
    classes = Counter(row["defect"] for row in rows)
    elapsed = time.monotonic() - started
    print(f"Imagini: {len(rows)} în {elapsed:.1f} s "
          f"({len(rows) / elapsed:.1f} img/s)" if elapsed > 0 else f"Imagini: {len(rows)}")
    print("  familii: " + ", ".join(f"{name}:{count}" for name, count in sorted(families.items())))
    print("  clase:   " + ", ".join(f"{name}:{count}" for name, count in sorted(classes.items())))
    print(f"  index:   {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
