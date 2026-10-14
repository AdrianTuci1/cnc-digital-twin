# Feature engineering

Rezumat al deciziei; detaliile operaționale sunt în
[`ml/feature_engineering/README.md`](../ml/feature_engineering/README.md), iar motivarea în
[ADR 0003](adr/0003-data-wrangler-pentru-feature-engineering.md).

## Hot path

Rulează ca transformare Firehose, pe microbatch-ul de 60 de secunde, în
`lambdas/feature_engineering/`:

- `core.py` — logica pură: decodare, grupare pe ferestre, agregare, contract, metrice, replay;
- `handler.py` — legătura cu AWS: endpoint, CloudWatch, S3 pentru replay, răspuns Firehose.

Caracteristicile calculate sunt cele care încap într-o fereastră: medie, p95, deviație
standard, RMS, vârf, plus scorul de anomalie. Când endpoint-ul nu este disponibil, scorul vine
din praguri absolute (`core.threshold_score`), iar înregistrarea este marcată
`model_version = "thresholds"`.

## Offline

Pentru antrenare sunt necesare ferestre glisante, decalaje și etichete. Aici intră în scenă
Data Wrangler:

- fluxul se construiește vizual în Studio, pe baza rețetei generate din cod;
- fluxul exportat (`.flow`) se comite în `ml/feature_engineering/flows/`;
- rularea se face ca job de procesare, cu `ml/feature_engineering/run_flow.py`;
- pasul de ferestre glisante rulează cu `build_features.py`, care folosește
  `cncdt_ml/offline.py`, ca rezultatul să fie identic în CI și în producție.

## Cod partajat

`ml/src/cncdt_ml/` este copiat în arhiva Lambda-ului de feature engineering
(`source_dirs` în stack-ul de platformă), deci hot path-ul și antrenarea folosesc aceleași
definiții:

| Modul | Ce conține |
| --- | --- |
| `schema.py` | Contractul: coloanele, cheile de partiționare, filtrarea câmpurilor în plus |
| `features.py` | Agregările pe fereastră (aceleași formule ca în extensia Isaac Sim) |
| `windows.py` | Alinierea și gruparea ferestrelor |
| `offline.py` | Ferestre glisante, decalaje, rapoarte, rețeta completă |
| `labels.py` | Etichetele de sănătate, severitatea, proxy-ul de viață rămasă |

## Verificări

| Test | Ce apără |
| --- | --- |
| `test_feature_parity.py` | Extensia Isaac Sim și `cncdt_ml` dau valori identice |
| `test_cncdt_ml.py` | Contractul din cod, Terraform și schemele JSON coincid |
| `test_feature_engineering_lambda.py` | Răspunsul Firehose acoperă fiecare `recordId` și doar coloanele din contract |
| `test_feature_engineering.py` | Rețeta comisă este cea generată din cod; `build_features.py` produce coloanele promise |

## Metrice publicate

Fiecare fereastră publică în `CNCDT/Telemetry` un set mic, în ritmul microbatch-ului de 60 s
(o cerere pe minut, nu 10 Hz, ca să țină costul sub control):

| Metrică | Dimensiuni | Cine o publică | Ce spune |
| --- | --- | --- | --- |
| `AnomalyScore` | `DeviceId`, `CellId` | Lambda-ul, direct | cât de anormală este fereastra |
| `SamplesAggregated` | `DeviceId`, `CellId` | Lambda-ul, direct | eșantioanele adunate în fereastră |
| `WindowsProcessed` | `Stage = aggregate` | Lambda-ul, direct | câte ferestre a închis invocarea |
| `EndpointFailures` | fără dimensiuni | Lambda-ul, direct, doar când este pozitiv | apeluri eșuate către endpoint-ul de anomalii |
| `DegradationIndex` | `DeviceId`, `CellId`, **`Element`** (`cnc`, `robot`) | Lambda-ul, direct | indexul de uzură, 0–1 |
| `RULMinutes` | `DeviceId`, `CellId`, **`Element`** | Lambda-ul, direct | minute de operare până la pragul de avarie |
| `AnomalyDetectedWindows` | fără dimensiuni | filtru de metrice pe log group | câte ferestre au fost marcate anomale |

Ultimul nu vine din `PutMetricData`, ci dintr-un `aws_cloudwatch_log_metric_filter` care numără
liniile de jurnal cu `event = "anomaly_detected"`; de aceea handlerul scrie un astfel de
eveniment pentru fiecare fereastră marcată de model, iar testul
`test_handler_logs_anomaly_events_for_the_metric_filter` apără acest acord.

Caracteristicile brute ale ferestrei **nu** se publică implicit: nu le citește niciun widget
sau alarmă, iar fiecare metrică custom costă lunar. `PUBLISH_FEATURE_METRICS=true` le pornește
pentru analiză exploratorie în CloudWatch.

`RULMinutes` este `(prag − index) / (rată × 60)`, calculat doar când rata este pozitivă: ratele
vin din `TOOL_WEAR_RATE` și `JOINT_FRICTION_RATE`, aceleași valori ca `CNCDT_TOOL_WEAR_RATE` și
`CNCDT_JOINT_FRICTION_RATE` din `.env` (ADR 0007). Pragurile de alarmă și gauge-urile din
dashboard-ul de fabrică se declară în `degradation_elements` (ADR 0008). Contoarele de
producție (`PartsGood`, `PartsScrap`) vin din Lambda-ul de inspecție, nu de aici.

## Etichete: limită asumată

Etichetele sunt derivate din semnalele simulării (`tool_wear_index`, `joint_friction_index`,
`is_synthetic_defect`, temperatură, debit de răcire). Un model antrenat pe ele reproduce
regulile din `cncdt_ml/labels.py`. Este suficient pentru un demo cap-coadă și pentru validarea
pipeline-ului. Cu date reale de mentenanță, etichetele se înlocuiesc și versiunea rețetei
crește.
