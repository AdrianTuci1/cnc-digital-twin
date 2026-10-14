# Lambda: inspecție vizuală (QA)

Declanșată de scrierea unui cadru QA în `raw/qa-snapshots/*.json` (notificarea S3 configurată
în stack-ul de platformă). Trimite **fotografia** și metadatele către endpoint-ul de viziune,
transformă răspunsul într-un verdict `PASS` / `REJECT`, îl arhivează în data lake și îl
publică pe topicul IoT de control, de unde simularea acționează lama ori pistonul de rejecție.

Cadrul care decide ruta este cel de la zona lamei/piston (`camera_role = "sorting"`), scris de
`QaSnapshotWriter` la intrarea în inspecție.

## Fișiere

| Fișier | Rol |
| --- | --- |
| `handler.py` | Legătura cu AWS: citire S3 (metadate plus PNG), invocare endpoint, scriere verdict, publicare IoT, metrice |
| `qa_vision_core.py` | Logica pură: notificarea S3, validarea cadrului, caracteristici, cererea către endpoint, verdict, chei, metrice |

`qa_vision_core.py` nu importă boto3. Numele modulului este unic pentru că toate directoarele
de Lambda ajung pe același `sys.path` în teste, iar `core` este deja folosit de
feature engineering.

## Flux

1. **Notificare S3** — se citesc toate obiectele din `Records`, cu `key` decodat URL.
2. **Citire cadru** — `GetObject` pe fiecare cheie; un cadru invalid este sărit și numărat,
   nu oprește lotul (o excepție ar face S3 să retrimită toate notificările).
3. **Fotografie** — `GetObject` pe `image_key` (același nume, extensia `.png`). O poză lipsă
   ori prea mare este sărită cu un log, iar verdictul rămâne posibil pe caracteristici.
4. **Scor** — dacă `SAGEMAKER_ENDPOINT_NAME` este configurat, se cheamă endpoint-ul; altfel,
   sau dacă apelul eșuează, se folosește `heuristic_probability`.
5. **Verdict** — `REJECT` când probabilitatea ≥ `DEFECT_THRESHOLD`.
6. **Arhivare** — verdictul se scrie în `curated/qa-verdicts/dt=<zi>/<ciclu>-<piesă>.json`.
7. **Publicare** — verdictul ajunge pe `CONTROL_TOPIC` (contractul `cnc.control.v1`).

## Contractul cererii către endpoint

Fotografia pleacă inline, în base64, în corpul cererii, alături de vectorul de caracteristici
(util unui model tabular ori ca rezervă):

```json
{
  "contract": "cnc.qa.snapshot.v1",
  "feature_names": ["tool_wear_index", "joint_friction_index", "surface_quality", "image_available"],
  "instances": [
    {
      "part_id": "part-000012",
      "cycle_id": "cyc-000034",
      "camera_role": "sorting",
      "features": [0.1, 0.2, 0.95, 1.0],
      "image": { "format": "png", "encoding": "base64", "data": "iVBORw0..." }
    }
  ]
}
```

Câmpul `image` lipsește când cadrul nu a putut fi capturat. Răspunsul acceptat rămâne
`{"predictions": [...]}`, `{"outputs": [...]}` ori o listă; fiecare element poate fi un număr
sau un dicționar cu `defect_probability` / `score`. InvokeEndpoint acceptă cel mult 6 MB, iar
base64 umflă datele cu o treime, deci pragul intern este `IMAGE_PAYLOAD_LIMIT_BYTES = 4 MB`.

## Fallback pe praguri

Fără endpoint, scorul combină `tool_wear_index`, `joint_friction_index` și `surface_quality`.
`is_synthetic_defect = true` forțează un scor de cel puțin 0,9, iar un cadru fără imagine
(`image_captured = false`) este limitat la 0,49: nu se rejecționează o piesă doar pentru că
lipsește captura.

## Variabile de mediu

| Variabilă | Implicit | Rol |
| --- | --- | --- |
| `TELEMETRY_NAMESPACE` | `CNCDT/Telemetry` | Namespace-ul metricilor CloudWatch |
| `SAGEMAKER_ENDPOINT_NAME` | gol | Endpoint-ul de viziune; gol înseamnă praguri |
| `MODEL_VERSION` | `unversioned` | Valoarea scrisă în verdict |
| `CONTROL_TOPIC` | gol | Topicul IoT pe care se publică verdictul |
| `DEFECT_THRESHOLD` | `0.5` | Pragul peste care verdictul este `REJECT` |
| `DATA_LAKE_BUCKET` | gol | Bucket-ul pentru arhivarea verdictelor |
| `VERDICT_PREFIX` | `curated/qa-verdicts` | Prefixul cheilor de verdict |
| `SEND_IMAGE_TO_ENDPOINT` | `true` | Trimite fotografia în cerere; `false` trimite doar caracteristicile |
| `IOT_DATA_ENDPOINT` | gol | Endpoint-ul ATS IoT Core; gol folosește endpoint-ul implicit boto3 |
| `LOG_LEVEL` | `INFO` | Nivelul de jurnalizare |

## Limite

- Verdictul se publică și dacă arhivarea eșuează: bucla de control are prioritate față de
  urma de audit, care se loghează ca eroare.
- Modelul este opțional. În `dev` endpoint-urile pornesc dezactivate, deci verdictul vine din
  praguri până la prima antrenare.
- Fotografia nu este recomprimată: un cadru peste pragul de 4 MB este sărit, nu micșorat.
