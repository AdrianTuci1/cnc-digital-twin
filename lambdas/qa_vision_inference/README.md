# Lambda: inspecție vizuală (QA)

Declanșată de scrierea unui cadru QA în `raw/qa-snapshots/*.json` (notificarea S3 configurată
în stack-ul de platformă). Trimite **fotografia** către endpoint-ul de viziune, transformă
răspunsul într-un verdict `PASS` / `REJECT`, îl arhivează în data lake și îl publică pe topicul
IoT de control, de unde simularea acționează lama ori pistonul de rejecție.

Verdictul vine **exclusiv din modelul antrenat**. Cadrul QA nu conține nimic din ce a decis
geometria piesei: fără etichetă, Lambda-ul nu are cu ce să ghicească, deci nici nu încearcă.

Cadrul care decide ruta este cel de la zona lamei/piston (`camera_role = "sorting"`), scris de
`QaSnapshotWriter` la intrarea în inspecție.

## Fișiere

| Fișier | Rol |
| --- | --- |
| `handler.py` | Legătura cu AWS: citire S3 (metadate plus PNG), invocare endpoint, scriere verdict, publicare IoT, metrice |
| `qa_vision_core.py` | Logica pură: notificarea S3, validarea cadrului, cererea către endpoint, verdict, chei, metrice |

`qa_vision_core.py` nu importă boto3. Numele modulului este unic pentru că toate directoarele
de Lambda ajung pe același `sys.path` în teste, iar `core` este deja folosit de
feature engineering.

## Flux

1. **Notificare S3** — se citesc toate obiectele din `Records`, cu `key` decodat URL.
2. **Citire cadru** — `GetObject` pe fiecare cheie. Un cadru invalid ori unul care poartă un
   câmp din `LABEL_FIELDS` este sărit și numărat, nu oprește lotul (o excepție ar face S3 să
   retrimită toate notificările).
3. **Fotografie** — `GetObject` pe `image_key` (același nume, extensia `.png`). O poză lipsă
   ori prea mare este sărită cu un log, iar cadrul pleacă totuși, cu `image_captured: false`.
4. **Scor** — se cheamă endpoint-ul din `SAGEMAKER_ENDPOINT_NAME`.
5. **Verdict** — `REJECT` când probabilitatea ≥ `DEFECT_THRESHOLD`.
6. **Arhivare** — verdictul se scrie în `curated/qa-verdicts/dt=<zi>/<ciclu>-<piesă>.json`.
7. **Publicare** — verdictul ajunge pe `CONTROL_TOPIC` (contractul `cnc.control.v1`).

## Fără model nu se inventează un verdict

Dacă `SAGEMAKER_ENDPOINT_NAME` este gol, ori apelul eșuează, ori numărul de predicții nu se
potrivește cu numărul de cadre, Lambda-ul **nu publică nimic**. Numără cadrele
(`QaModelMissing`, respectiv `QaEndpointFailures`), publică metricele și se întoarce. Piesa
rămâne în seama rutei de rezervă din simulare (ADR 0009), iar dashboard-ul arată că modelul
lipsește.

Comportamentul este deliberat: o rezervă pe praguri citită din metadate ar produce verdicte în
`dev` care par că vin dintr-un model. Vezi ADR 0013 pentru separarea dintre colectare și
demonstrație.

## Contractul cererii către endpoint

Fotografia pleacă inline, în base64, în corpul cererii, alături de identificatorii cadrului:

```json
{
  "contract": "cnc.qa.snapshot.v1",
  "instances": [
    {
      "part_id": "part-000012",
      "cycle_id": "cyc-000034",
      "camera_role": "sorting",
      "camera": "/World/QA/InspectionCamera",
      "resolution": [1280, 960],
      "image_captured": true,
      "image": { "format": "png", "encoding": "base64", "data": "iVBORw0..." }
    }
  ]
}
```

Câmpul `image` lipsește când cadrul nu a putut fi capturat. Răspunsul acceptat este
`{"predictions": [...]}`, `{"outputs": [...]}` ori o listă; fiecare element poate fi un număr
sau un dicționar cu `defect_probability` / `score`. InvokeEndpoint acceptă cel mult 6 MB, iar
base64 umflă datele cu o treime, deci pragul intern este `IMAGE_PAYLOAD_LIMIT_BYTES = 4 MB`.

## Câmpuri interzise în cerere

`LABEL_FIELDS` (`is_synthetic_defect`, `tool_wear_index`, `joint_friction_index`,
`surface_quality`, `family`, `defect`, `verdict`) sunt o scurgere de etichetă, nu o diferență
de stil. Aceleași nume sunt păzite de toți cei care ating cadrul: `parse_snapshot` refuză un
cadru care le conține, iar `instance_payload` refuză să le compună. Indiciile de uzură ale
simulării (`1 - h`) sunt parametrul intern al motorului de uzură, nu o măsurătoare: pe linia
reală coloanele nu există.

## Variabile de mediu

| Variabilă | Implicit | Rol |
| --- | --- | --- |
| `TELEMETRY_NAMESPACE` | `CNCDT/Telemetry` | Namespace-ul metricilor CloudWatch |
| `SAGEMAKER_ENDPOINT_NAME` | gol | Endpoint-ul de viziune; gol înseamnă zero verdicte, nu praguri |
| `MODEL_VERSION` | `unversioned` | Valoarea scrisă în verdict |
| `CONTROL_TOPIC` | gol | Topicul IoT pe care se publică verdictul |
| `DEFECT_THRESHOLD` | `0.5` | Pragul peste care verdictul este `REJECT` |
| `DATA_LAKE_BUCKET` | gol | Bucket-ul pentru arhivarea verdictelor |
| `VERDICT_PREFIX` | `curated/qa-verdicts` | Prefixul cheilor de verdict |
| `SEND_IMAGE_TO_ENDPOINT` | `true` | Trimite fotografia în cerere; `false` trimite doar identificatorii |
| `IOT_DATA_ENDPOINT` | gol | Endpoint-ul ATS IoT Core; gol folosește endpoint-ul implicit boto3 |
| `LOG_LEVEL` | `INFO` | Nivelul de jurnalizare |

## Limite

- Verdictul se publică și dacă arhivarea eșuează: bucla de control are prioritate față de
  urma de audit, care se loghează ca eroare.
- Modelul este obligatoriu pentru orice verdict. În `dev` endpoint-urile pornesc dezactivate,
  deci inspecția numără cadrele în `QaModelMissing` până la prima antrenare și primul endpoint.
- Fotografia nu este recomprimată: un cadru peste pragul de 4 MB este sărit, nu micșorat.
- Clasificarea pe familie și pe clasa de defect nu este încă în verdict: contractul de control
  are un singur câmp `verdict`, iar cele două capete de etichetă din `docs/10` cer o extindere
  de contract când modelul există.
