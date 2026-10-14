#!/usr/bin/env bash
# Buclă completă, fără AWS și fără Isaac Sim.
#
# Verifică lanțul: eșantioane sintetice -> transformarea Firehose a Lambda-ului ->
# înregistrare agregată conformă cu contractul -> caracteristici offline -> răspuns Firehose.
#
# Rulare:  tests/e2e/run_closed_loop.sh
# Cod de ieșire: 0 dacă toate verificările trec, 1 altfel.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${PYTHON:-python3}"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cncdt-e2e.XXXXXX")"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "==> director de lucru: $TMP_DIR"

export PYTHONPATH="$ROOT/ml/src:$ROOT/lambdas:$ROOT/lambdas/feature_engineering:$ROOT/simulation/extensions/cncdt.cnc_cell:${PYTHONPATH:-}"

# ---------------------------------------------------------------- pasul 1: lot Firehose
echo "==> pasul 1/4: generez lotul de eșantioane brute"
"$PY" - "$TMP_DIR" <<'PYTHON'
import base64, json, sys
from pathlib import Path

from cncdt.cnc_cell.degradation.model import DegradationState
from cncdt.cnc_cell.telemetry.synthetic import SyntheticCell

target = Path(sys.argv[1]) / "event.json"
degradation = DegradationState()
cell = SyntheticCell(degradation=degradation)
cell.set_state("CUTTING")

records = []
base_ms = 1_767_225_600_000
for index, sample in enumerate(cell.sample_window(600, 0.1)):
    sample.update(
        {
            "ts_ms": base_ms + index * 100,
            "schema_version": "1.0.0",
            "cycle_id": degradation.cycle_id,
            "part_id": degradation.part_id,
            "topic": "cncdt/dev/cnc/cell-a/telemetry",
        }
    )
    records.append(
        {
            "recordId": f"rec-{index}",
            "approximateArrivalTimestamp": base_ms,
            "data": base64.b64encode(json.dumps(sample).encode("utf-8")).decode("ascii"),
        }
    )

event = {
    "invocationId": "e2e",
    "deliveryStreamArn": "arn:aws:firehose:us-east-1:000000000000:deliverystream/cncdt-dev-iot-ingestion",
    "region": "us-east-1",
    "records": records,
}
target.write_text(json.dumps(event), encoding="utf-8")
print(f"    {len(records)} eșantioane brute, o singură fereastră de 60 s")
PYTHON

# ---------------------------------------------------------------- pasul 2: transformare
echo "==> pasul 2/4: rulez transformarea Lambda (fără AWS)"
"$PY" - "$TMP_DIR" <<'PYTHON'
import base64, json, sys
from pathlib import Path

import handler
from cncdt_ml.schema import ENRICHED_COLUMNS, PARTITION_KEYS

work = Path(sys.argv[1])
event = json.loads((work / "event.json").read_text(encoding="utf-8"))
response = handler.lambda_handler(event, context=None)
(work / "response.json").write_text(json.dumps(response), encoding="utf-8")

sent_ids = {record["recordId"] for record in event["records"]}
got_ids = {record["recordId"] for record in response["records"]}
assert sent_ids == got_ids, f"răspuns incomplet: lipsesc {sent_ids - got_ids}"

ok = [record for record in response["records"] if record["result"] == "Ok"]
assert len(ok) == 1, f"aștept exact o înregistrare Ok, am {len(ok)}"
body = json.loads(base64.b64decode(ok[0]["data"]))
assert tuple(body) == ENRICHED_COLUMNS, "coloanele nu corespund contractului"
assert body["sample_count"] == 600, f"sample_count greșit: {body['sample_count']}"
assert set(ok[0]["metadata"]["partitionKeys"]) == set(PARTITION_KEYS)
assert body["model_version"] == "thresholds", "fără endpoint, decizia vine din praguri"

dropped = [record for record in response["records"] if record["result"] == "Dropped"]
assert len(dropped) == 599, f"aștept 599 de înregistrări Dropped, am {len(dropped)}"
print(f"    o înregistrare agregată, {len(dropped)} aruncate, partiții {ok[0]['metadata']['partitionKeys']}")
PYTHON

# ---------------------------------------------------------------- pasul 3: caracteristici
echo "==> pasul 3/4: construiesc caracteristicile offline"
"$PY" "$ROOT/ml/feature_engineering/build_features.py" \
  --input "$ROOT/tests/data/telemetry.jsonl" \
  --output "$TMP_DIR/features.jsonl" --format jsonl >/dev/null

"$PY" - "$TMP_DIR" <<'PYTHON'
import json, sys
from pathlib import Path

from cncdt_ml.offline import feature_columns

rows = [
    json.loads(line)
    for line in (Path(sys.argv[1]) / "features.jsonl").read_text(encoding="utf-8").splitlines()
]
assert rows, "setul de caracteristici este gol"
for column in feature_columns():
    assert column in rows[0], f"lipsește coloana {column}"

labels = {}
for row in rows:
    labels[row["health_label"]] = labels.get(row["health_label"], 0) + 1
assert "healthy" in labels, "eticheta healthy lipsește din setul de probă"
print(f"    {len(rows)} rânduri, etichete: {', '.join(f'{k}={v}' for k, v in sorted(labels.items()))}")
PYTHON

# ---------------------------------------------------------------- pasul 4: publicare locală
echo "==> pasul 4/4: publicare în fișier (fără broker)"
CNCDT_TELEMETRY_LOG="$TMP_DIR/telemetry.jsonl" CNCDT_AUTOSTART_CYCLE=true "$PY" - <<'PYTHON'
import json, os, time

from cncdt.cnc_cell.config import CellConfig
from cncdt.cnc_cell.sim_control import CellController

controller = CellController(config=CellConfig())
controller.start(autostart_cycle=True)
time.sleep(1.0)
controller.stop()

path = os.environ["CNCDT_TELEMETRY_LOG"]
lines = [json.loads(line) for line in open(path, encoding="utf-8").read().splitlines()]
assert lines, "publicatorul nu a scris nimic"
required = {"ts_ms", "schema_version", "spindle_load", "vibration", "cell_id", "device_id"}
missing = [name for name in required if name not in lines[0]]
assert not missing, f"câmpuri lipsă în telemetrie: {missing}"
print(f"    {len(lines)} eșantioane publicate local, contract respectat")
PYTHON

echo
echo "REZULTAT: bucla completă a trecut (fără AWS, fără Isaac Sim)."
