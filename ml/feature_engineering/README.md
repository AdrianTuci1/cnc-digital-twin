# Feature engineering

Feature engineering-ul are două căi, cu roluri diferite. Confuzia între ele este cea mai
scumpă greșeală într-un proiect cu hot path: caracteristicile calculate offline nu pot rula în
60 de secunde de Lambda.

| Cale | Unde rulează | Ce calculează | Responsabil |
| --- | --- | --- | --- |
| **Hot path** | Lambda de feature engineering, la fiecare microbatch de 60 s | Agregări de bază: medie, p95, deviație standard, RMS, vârf, scor de anomalie | `lambdas/feature_engineering/core.py` |
| **Offline** | Job de procesare SageMaker, pornit din Studio, pipeline sau CI | Ferestre glisante, decalaje temporale, rapoarte între senzori, etichete | **Data Wrangler** (autorat) + `cncdt_ml/offline.py` (execuție) |

Glue apare în proiect **doar ca registru de schemă**: ține tabelul cu coloanele telemetriei,
ca Firehose să poată scrie Parquet. Nu există job-uri Glue, crawlere sau transformări Glue.

## Cum se folosește Data Wrangler

Data Wrangler este locul unde se **construiește vizual** setul de caracteristici: se încarcă
un eșantion din data lake, se profilează coloanele, se văd histogramele și corelațiile, se
primesc avertismente de scurgere de informație (target leakage) și se încearcă un model rapid
înainte de a scrie cod. Rezultatul este un fișier `.flow`.

### Pași în Studio

1. Deschideți SageMaker Studio și porniți aplicația Data Wrangler.
2. `Import data` → `Amazon S3` → `s3://<datalake-bucket>/raw/telemetry/` (Parquet, partiții
   `dt_year/dt_month/dt_day/dt_hour`). Pentru explorare este suficient un eșantion.
3. Adăugați pașii din `flows/predictive_maintenance.recipe.json`, în ordine. Fișierul este
   generat din cod, deci conține exact coloanele care trebuie să iasă.
4. La pasul 9, inspectați profilul și graficul etichetei `health_label`. Dacă distribuția este
   dezechilibrată, ajustați pragurile în `cncdt_ml/labels.py` (ele sunt într-un singur loc).
5. `Export to` → `S3` → `s3://<datalake-bucket>/features/predictive_maintenance/v1/`, Parquet.
6. `File` → `Export flow` și salvați fișierul în `flows/predictive_maintenance.flow`.
   Comiteți-l: fluxul este un artefact versionat, nu un draft personal.

> `flows/predictive_maintenance.recipe.json` este **generat** din cod
> (`build_features.py --print-recipe`) și verificat în teste. Nu îl editați manual.
> `flows/predictive_maintenance.flow` este **exportul din Studio** și se editează doar în Studio.

### Rularea fluxului ca job

```bash
# 1. Verificați cererea, fără apeluri AWS
python3 ml/feature_engineering/run_flow.py \
  --flow ml/feature_engineering/flows/predictive_maintenance.flow \
  --bucket cncdt-dev-datalake-<account-id> --dry-run

# 2. Porniți jobul și așteptați rezultatul
python3 ml/feature_engineering/run_flow.py \
  --flow ml/feature_engineering/flows/predictive_maintenance.flow \
  --bucket cncdt-dev-datalake-<account-id> \
  --role-arn arn:aws:iam::<account-id>:role/cncdt-dev-studio-role \
  --instance-type ml.m5.4xlarge --wait
```

Imaginea containerului se rezolvă din SDK-ul SageMaker. Dacă SDK-ul nu este instalat,
transmiteți `--image-uri` sau `CNCDT_DATA_WRANGLER_IMAGE`. Scriptul refuză să ghicească un URI,
pentru că acesta diferă între regiuni și versiuni.

## De ce ferestrele glisante rămân în cod

Pașii 1–5 și 7–9 din rețetă sunt operatori pe rând: curățare, formule, codificare, split.
Pasul 6 (ferestre glisante și decalaje) are nevoie de ordine temporală și de stare între
rânduri. Data Wrangler poate face asta printr-un `Custom transform` cu pandas, dar atunci
logica ar exista în două locuri: în fluxul vizual și în codul care trebuie să ruleze în CI.

Din acest motiv, pasul 6 rulează ca **al doilea job de procesare**, cu `build_features.py`,
care folosește `cncdt_ml/offline.py`. Fluxul Data Wrangler rămâne locul de autorat și de
inspectat vizual; codul rămâne sursa de adevăr pentru ce ajunge în antrenare.

Dacă echipa preferă totuși un `Custom transform` în flux, rețeta marchează pasul cu
`requires_custom_transform: true`, iar funcțiile de referință sunt
`cncdt_ml.offline.add_rolling_features` și `add_lag_features`.

## Calea deterministă

```bash
# JSON Lines, fără nicio dependență
python3 ml/feature_engineering/build_features.py \
  --input data/telemetry.jsonl --output /tmp/features.jsonl --format jsonl

# Ca job de procesare (directoarele montate de SageMaker)
python3 ml/feature_engineering/build_features.py \
  --input /opt/ml/processing/input --output /opt/ml/processing/output --format parquet

# Ce s-ar scrie, fără să scrie
python3 ml/feature_engineering/build_features.py \
  --input data/telemetry.jsonl --output /tmp/x.jsonl --dry-run
```

`--spec-version` se compară cu versiunea din cod. Dacă pipeline-ul cere o versiune care nu mai
există, scriptul se oprește: o rețetă diferită produce un model care nu poate fi comparat cu
cel din producție.

## Corespondența pas cu cod

| Pas | Rețetă Data Wrangler | Cod echivalent |
| --- | --- | --- |
| 1 | Import from S3 | — (Firehose scrie, Data Wrangler citește) |
| 2 | Drop columns | `cncdt_ml.schema.ENRICHED_COLUMNS` |
| 3 | Parse / Cast | `cncdt_ml.windows.window_key` |
| 4 | Sort | `cncdt_ml.windows.sort_by_timestamp` |
| 5 | Formula | `cncdt_ml.offline.derived_ratios` |
| 6 | Custom transform | `cncdt_ml.offline.add_rolling_features`, `add_lag_features` |
| 7 | Encode categorical | (în codul de antrenare, `ml/training/`) |
| 8 | Formula (etichete) | `cncdt_ml.labels` |
| 9 | Split data | planificat, vezi `ml/pipelines/README.md` |
| 10 | Export to S3 | `build_features.py --format parquet` |

## Costuri

- Aplicația Data Wrangler din Studio rulează pe instanța kernel; domeniul are oprire automată
  pe inactivitate (`enable_studio_idle_shutdown`), deci nu plătește noaptea.
- Un job de procesare `ml.m5.4xlarge` costă aproximativ 0.9 USD/oră în `us-east-1`. Un set de
  câteva milioane de ferestre se procesează în mai puțin de o oră.
- Instanțele de procesare sunt restricționate de politica IAM a domeniului
  (`DenyUnapprovedTrainingInstances`), deci nu se poate porni din greșeală o instanță GPU
  scumpă pentru un pas de transformare.

## Limite asumate

- **Nu rulați un flux Data Wrangler în hot path.** Containerul pornește lent și este făcut
  pentru loturi. Hot path-ul este Lambda-ul.
- **Data Wrangler lucrează pe un singur nod.** Peste ~100 GB de telemetrie brută, filtrați
  întâi cu Athena și dați fluxului doar coloanele necesare.
- **Fluxurile nu au programare proprie.** Se pornesc ca joburi de procesare, din pipeline sau
  din CI. Programarea aparține pipeline-ului, nu fluxului.
- **Pragurile de etichete sunt reguli, nu adevăr.** Un model antrenat pe ele reproduce
  regulile din `cncdt_ml/labels.py`. Când există date reale de mentenanță, etichetele se
  înlocuiesc și versiunea rețetei crește.
