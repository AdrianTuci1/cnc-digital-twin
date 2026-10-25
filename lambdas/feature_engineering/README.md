# Lambda: feature engineering

Transformare Firehose care rulează pe microbatch-ul de 60 de secunde. Agregă eșantioanele
brute de la simulare în câte o înregistrare per fereastră, cere scorul de anomalie modelului
și întoarce rezultatul către Firehose, care îl scrie în Parquet, partiționat pe oră.

## Fișiere

| Fișier | Rol |
| --- | --- |
| `handler.py` | Legătura cu AWS: endpoint, metrice CloudWatch, replay în S3, răspuns Firehose |
| `core.py` | Logica pură: decodare, grupare pe ferestre, agregare, contract, metrice, replay |

`core.py` nu importă boto3. Testele îl exercită direct, cu loturi JSON construite în test.

## Contractul de intrare

Fiecare înregistrare primită de la Firehose este un eșantion publicat de simulare, codificat
base64 + JSON. Câmpurile citite:

`schema_version`, `device_id`, `cell_id`, `topic`, `ts_ms`, `machine_state`, `cycle_id`,
`part_id`, `spindle_load`, `vibration`, `temp_c`, `coolant_flow_lpm`, `joint_effort`,
`axis_feed`, `tool_wear_index`, `joint_friction_index`, `is_synthetic_defect`.

Cheia de grupare este `(device_id, cell_id, fereastra)`; fereastra este aliniată la epoch, la
multipli de 60 de secunde.

## Contractul de ieșire

Pentru fiecare fereastră se emite **o singură** înregistrare `Ok`, cu coloanele din
`cncdt_ml.schema.ENRICHED_COLUMNS` și cu `metadata.partitionKeys`
(`dt_year`, `dt_month`, `dt_day`, `dt_hour`). Celelalte înregistrări din fereastră primesc
`Dropped`; Firehose cere un rezultat pentru fiecare `recordId` primit.

Conversia JSON → Parquet din Firehose eșuează dacă înregistrarea are câmpuri în plus față de
schema din catalog. De aceea `core.assemble_response` trece fiecare înregistrare prin
`cncdt_ml.schema.conform`, care elimină câmpurile cunoscute în plus (`spindle_load_max`,
`vibration_crest`, `temp_c_max`, `joint_effort_*`, `axis_feed_*`).

## Ce intră în model

`core.MODEL_FEATURE_ORDER` este singura listă care decide intrarea modelului, iar ea conține
doar măsurători: `spindle_load_*`, `vibration_*`, `coolant_flow_lpm`, `temp_c_mean`,
`axis_feed_mean`. `tool_wear_index` și `joint_friction_index` rămân în contract și hrănesc
dashboard-ul de degradare, dar nu intră în vector: sunt `1 - h`, adică starea internă a
motorului de uzură, iar un model care le primește are răspunsul în intrare (ADR 0013).

Endpoint-ul poate fi un `inference.py` propriu (`{"predictions": [...]}`) ori containerul
built-in Random Cut Forest (`{"scores": [{"score": ...}]}`). Ambele forme ajung la același
`anomaly_score`; pragul RCF se copiază din `metrics.json` (percentila 97,5 pe ferestre
sănătoase, cu alarmă pe două ferestre consecutive), fiindcă scorul RCF nu este o probabilitate
între 0 și 1.

Statisticile ferestrei se calculează pe eșantioanele din starea `CUTTING`, nu pe toate: într-o
fereastră de 60 s intră și repaus, iar proporția de așchiere variază de la o fereastră la alta.
Amestecate, variația de încărcare a celulei acoperă semnalul de uzură. O fereastră cu mai puțin
de `core.MIN_CUTTING_SAMPLES` eșantioane de așchiere nu ajunge la model; rămâne cu pragurile
absolute, ca să nu consume un apel de endpoint fără semnal.

## Fallback pe praguri

Dacă `SAGEMAKER_ENDPOINT_NAME` este gol sau invocarea eșuează, scorul vine din
`core.threshold_score`: raportul maxim față de pragurile absolute, normalizat la 1.2.
Înregistrarea primește `model_version = "thresholds"` și, dacă este marcată ca anomalie,
este scrisă și în `s3://<datalake>/replay/manual/dt=<zi>/` pentru reluare ulterioară.

## Variabile de mediu

| Variabilă | Implicit | Rol |
| --- | --- | --- |
| `TELEMETRY_NAMESPACE` | `CNCDT/Telemetry` | Namespace-ul metricilor CloudWatch |
| `SAGEMAKER_ENDPOINT_NAME` | gol | Endpoint-ul de anomalii; gol înseamnă praguri |
| `MODEL_VERSION` | `unversioned` | Valoarea scrisă în coloana `model_version` |
| `ANOMALY_SCORE_THRESHOLD` | `0.65` | Pragul scorului modelului; pentru RCF se copiază percentila 99 din `metrics.json`, fiindcă scorul nu este o probabilitate |
| `DATA_LAKE_BUCKET` | gol | Bucket-ul pentru replay |
| `WINDOW_SECONDS` | `60` | Durata ferestrei |
| `LOG_LEVEL` | `INFO` | Nivelul de jurnalizare |

## Limite

- Cel mult `core.MAX_ENDPOINT_CALLS` ferestre per invocare ajung la model; restul folosesc
  pragurile. Motivul este limita de 120 de secunde a funcției.
- Agregarea presupune că un microbatch conține eșantioanele unei singure ferestre. Dacă
  Firehose livrează la granița dintre ferestre, ambele ferestre apar în același lot — sunt
  tratate separat, corect, dar prima și ultima fereastră din lot pot fi incomplete.
- Funcția nu are dependențe externe: `boto3` vine din runtime.
