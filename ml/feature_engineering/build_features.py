#!/usr/bin/env python3
"""Construiește setul de antrenare din telemetria din data lake.

Acesta este pasul determinist al feature engineering-ului: aceleași funcții din
``cncdt_ml.offline`` pe care le folosește CI-ul rulează și aici, deci rezultatul nu depinde de
cine a apăsat ultima dată „Run" în Studio.

Rulare locală (JSON Lines, fără dependențe)::

    python3 build_features.py --input sample.jsonl --output features.jsonl

Ca job de procesare SageMaker, cu directoarele montate::

    python3 build_features.py --input /opt/ml/processing/input \\
        --output /opt/ml/processing/output --format parquet

Folderele de intrare sunt citite recursiv: toate fișierele ``*.jsonl``, ``*.csv`` și
``*.parquet`` (ultimele cer ``pyarrow``).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterator

_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE.parent / "src", _HERE):
    if (_candidate / "cncdt_ml").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from cncdt_ml.offline import DEFAULT_SPEC, OfflineFeatureSpec, build_offline_features  # noqa: E402
from cncdt_ml.schema import ENRICHED_COLUMNS  # noqa: E402

SUPPORTED_SUFFIXES = (".jsonl", ".ndjson", ".csv", ".parquet")


def log(message: str) -> None:
    print(f"[features] {message}", flush=True)


def die(message: str, code: int = 2) -> None:
    print(f"[features][eroare] {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


# ---------------------------------------------------------------- citire
def _coerce(value: str) -> Any:
    """Convertește valorile CSV în numere, booleeni sau șiruri."""
    text = value.strip()
    if text == "":
        return None
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return value


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                log(f"linia {number} din {path} este invalidă, o sar: {exc}")
                continue
            if isinstance(payload, dict):
                yield payload


def read_csv_file(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {key: _coerce(value) for key, value in row.items() if key is not None}


def read_parquet(path: Path) -> Iterator[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        die("citirea Parquet cere pyarrow: pip install pyarrow")
    table = pq.read_table(str(path))
    for row in table.to_pylist():
        yield row


def read_one(path: Path) -> Iterator[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in (".jsonl", ".ndjson"):
        yield from read_jsonl(path)
    elif suffix == ".csv":
        yield from read_csv_file(path)
    elif suffix == ".parquet":
        yield from read_parquet(path)
    else:
        log(f"ignor {path} (extensie neacceptată)")


def read_input(path: Path) -> list[dict[str, Any]]:
    """Citește un fișier sau un director (recursiv)."""
    if not path.exists():
        die(f"intrarea nu există: {path}")
    if path.is_file():
        return list(read_one(path))

    rows: list[dict[str, Any]] = []
    for candidate in sorted(path.rglob("*")):
        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES:
            rows.extend(read_one(candidate))
    if not rows:
        die(f"niciun fișier cu date în {path} (acceptate: {', '.join(SUPPORTED_SUFFIXES)})")
    return rows


# ---------------------------------------------------------------- scriere
def column_order(rows: list[dict[str, Any]]) -> list[str]:
    """Coloanele din contract, în ordinea lor, apoi restul în ordine alfabetică."""
    present = {key for row in rows for key in row}
    ordered = [column for column in ENRICHED_COLUMNS if column in present]
    return ordered + sorted(present - set(ordered))


def write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def write_csv_file(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = column_order(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    try:
        import pyarrow as pa  # type: ignore
        import pyarrow.parquet as pq  # type: ignore
    except ImportError:
        die("scrierea Parquet cere pyarrow: pip install pyarrow")
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = column_order(rows)
    normalized = [{column: row.get(column) for column in columns} for row in rows]
    pq.write_table(pa.Table.from_pylist(normalized), str(path), compression="snappy")


def resolve_output(output: Path, fmt: str) -> Path:
    """Într-un director (montaj de job) se scrie un fișier cu nume fix."""
    if output.suffix:
        return output
    name = "features.parquet" if fmt == "parquet" else "features.jsonl" if fmt == "jsonl" else "features.csv"
    return output / name


def emit(rows: list[dict[str, Any]], output: Path, fmt: str) -> Path:
    target = resolve_output(output, fmt)
    if fmt == "jsonl":
        write_jsonl(rows, target)
    elif fmt == "csv":
        write_csv_file(rows, target)
    elif fmt == "parquet":
        write_parquet(rows, target)
    else:  # pragma: no cover - argparse limitează valorile
        die(f"format necunoscut: {fmt}")
    return target


# ---------------------------------------------------------------- argumente
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Construiește caracteristicile offline pentru antrenare.")
    parser.add_argument("--input", required=False, default="", help="Fișier sau director cu telemetrie")
    parser.add_argument("--output", required=False, default="", help="Fișier sau director de ieșire")
    parser.add_argument("--format", choices=("jsonl", "csv", "parquet"), default="")
    parser.add_argument("--spec-version", default=DEFAULT_SPEC.version)
    parser.add_argument("--wear-rate-per-hour", type=float, default=0.05)
    parser.add_argument("--limit", type=int, default=0, help="Limitează numărul de rânduri citite")
    parser.add_argument("--print-recipe", action="store_true", help="Tipărește rețeta Data Wrangler și iese")
    parser.add_argument("--dry-run", action="store_true", help="Raportează ce s-ar scrie, fără să scrie")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.print_recipe:
        from recipe import render_recipe_json  # import local: există doar în acest director

        print(render_recipe_json(), end="")
        return 0

    if not args.input or not args.output:
        die("--input și --output sunt obligatorii (sau folosiți --print-recipe)")

    if args.spec_version != DEFAULT_SPEC.version:
        die(
            f"versiunea de rețetă cerută ({args.spec_version}) nu este cea din cod "
            f"({DEFAULT_SPEC.version}). Actualizați codul sau pipeline-ul, nu ignorați diferența."
        )

    rows = read_input(Path(args.input))
    if args.limit > 0:
        rows = rows[: args.limit]
    log(f"citite {len(rows)} ferestre")

    spec: OfflineFeatureSpec = DEFAULT_SPEC
    features = build_offline_features(rows, spec, wear_rate_per_hour=args.wear_rate_per_hour)
    columns = column_order(features)
    log(f"calculate {len(features)} rânduri x {len(columns)} coloane")
    log(f"etichete: {_label_summary_text(features)}")

    if args.dry_run:
        print(json.dumps({"rows": len(features), "columns": columns}, indent=2, ensure_ascii=False))
        log("dry-run: nu s-a scris nimic")
        return 0

    fmt = args.format or _infer_format(Path(args.output))
    target = emit(features, Path(args.output), fmt)
    log(f"scris {target} ({fmt})")
    return 0


def _infer_format(output: Path) -> str:
    suffix = output.suffix.lower()
    if suffix == ".parquet":
        return "parquet"
    if suffix == ".csv":
        return "csv"
    if suffix in (".jsonl", ".ndjson"):
        return "jsonl"
    # Director de job de procesare: Parquet, formatul cerut de antrenare.
    return "parquet"


def _label_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in rows:
        label = str(row.get("health_label", "necunoscut"))
        summary[label] = summary.get(label, 0) + 1
    return dict(sorted(summary.items()))


def _label_summary_text(rows: list[dict[str, Any]]) -> str:
    """Rezumat lizibil. Se evită acoladele, ca ieșirea să rămână ușor de parsat."""
    summary = _label_summary(rows)
    return ", ".join(f"{label}={count}" for label, count in summary.items()) or "niciuna"


if __name__ == "__main__":
    raise SystemExit(main())
