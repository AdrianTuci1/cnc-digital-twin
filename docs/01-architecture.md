# Arhitectura

Bucla închisă: simularea produce date, AWS le transformă în decizii, deciziile se întorc în
simulare și acționează pistonul de rejecție. Fiecare verigă are un contract explicit.

```text
   Isaac Sim (EC2 GPU)                  AWS (Terraform)                        Consumatori
   ───────────────────                  ───────────────                        ───────────
   celula CNC + panou omni.ui
        │  MQTT mTLS (10 Hz)
        ▼
   AWS IoT Core ──regulă──▶ Firehose ──Lambda 60s──▶ Parquet în S3
                              │      (agregare fereastră)   │
                              │                             ├─▶ Athena (interogări ad-hoc)
                              │                             └─▶ Data Wrangler (offline)
                              │                                       │
                              │                                       ▼
                              │                            antrenare SageMaker
                              │                                       │
                              ▼                                       ▼
                        CloudWatch ◀── metrice            endpoint serverless
                        └─ dashboards custom + alarme SNS
                                                                      │
   piston de rejecție ◀── IoT command/qa-verdict ◀── verdict ◀────────┘
```

## Componente

| Componentă | Unde este definită | Rol |
| --- | --- | --- |
| Instanța GPU | `infra/terraform/stacks/sim`, modulul `sim-instance` | Rulează Isaac Sim; efemeră, cumpărată de pe piața spot |
| Volumul de date | `infra/terraform/stacks/sim-data` | EBS persistent, montat la `/opt/cncdt`: oglinda de conținut, cache-ul, modelele, `simulation/`. Se șterge doar manual (ADR 0011) |
| Ingestie | modulul `iot-ingestion` | Thing, certificat mTLS, topic, regulă, Firehose, tabel de schemă |
| Stocare | modulul `platform-storage` | Patru bucket-uri: data lake, assets, logs, rezultate Athena |
| Hot path | `lambdas/feature_engineering` | Agregare pe fereastră, scor de anomalie, metrice, replay |
| Inspecție | `lambdas/qa_vision_inference` | Verdict pe cadrul QA (foto + caracteristici într-un apel), publicat pe topicul de control |
| Comenzi | `lambdas/control_dispatcher` | Consumă coada SQS și publică pe topicul de comandă |
| ML | `ml/`, modulul `sagemaker-studio`, modulul `sagemaker-endpoint` | Autorat, antrenare, endpoint serverless |
| Observabilitate | modulul `observability`, modulul `budget` | Alarme, alarmă compusă, dashboard-uri custom CloudWatch, buget de cost |
| Backup | modulul `backup` | Salvează zilnic EFS-ul de home al Studio |

## Fluxul de date, pas cu pas

1. **Producție.** Extensia Isaac Sim publică un eșantion la ~10 Hz pe
   `cncdt/<env>/cnc/<cell>/telemetry`, cu degradarea inclusă în câmpurile publicate.
2. **Ingestie.** Regula IoT Core selectează tot mesajul, adaugă topicul și marcajul de timp MQTT
   și îl trimite către Firehose.
3. **Agregare.** Lambda-ul de feature engineering rulează ca transformare Firehose, pe un
   microbatch de 60 de secunde: grupează pe `(device_id, cell_id, fereastră)`, calculează
   caracteristicile, cere scorul de anomalie endpoint-ului și întoarce **o singură**
   înregistrare per fereastră.
4. **Stocare.** Firehose convertește JSON în Parquet (Snappy) folosind schema din registru și
   scrie partiționat pe oră: `raw/telemetry/dt_year=.../dt_month=.../dt_day=.../dt_hour=.../`.
5. **Consum.** Athena răspunde interogărilor ad-hoc peste Parquet-ul din S3. Offline, Data
   Wrangler citește aceleași date pentru feature engineering, iar setul rezultat ajunge în
   `features/predictive_maintenance/<versiune>/`.
6. **Vizualizare.** Tot ce se afișează stă în dashboard-uri custom CloudWatch, definite în
   `modules/observability` și versionate ca orice altă resursă. Sunt două, separate pe
   audiență (ADR 0008): unul de infrastructură (alarme de serviciu, ingestie Firehose,
   metricele Lambda-urilor, erorile din loguri, GPU-ul instanței de simulare) și unul de
   fabrică (eșantioane și ferestre procesate pe minut, degradare per element, minute până la
   pragul de avarie, anomalii). Proiectul nu folosește QuickSight sau alt instrument BI
   (ADR 0005).
7. **Antrenare.** Un pipeline SageMaker antrenează modelul, scrie metricele în Experiments și
   artefactul în `models/<nume>/<versiune>/`.
8. **Inferență.** Endpoint-ul serverless este chemat de Lambda pe hot path (anomalii) și de
   Lambda-ul de inspecție (defecte vizuale), cu captură de date pentru monitorizare.
9. **Bucla închisă.** Verdictul de inspecție ajunge înapoi pe topicul `qa-verdict`; piesa este
   oprită la lamă până atunci, iar verdictul aplică ruta (rebut ori mai departe). Dacă
   verdictul nu sosește, intervine ruta de rezervă, implicit rebutul (ADR 0009).

## Două căi de feature engineering

Distincția este structurală, nu stilistică: hot path-ul are 120 de secunde per invocare și
privește o singură fereastră, offline-ul privește istoricul.

| | Hot path | Offline |
| --- | --- | --- |
| Rulează în | Lambda, transformare Firehose | Job de procesare SageMaker |
| Frecvență | 60 s | la cerere, din pipeline sau CI |
| Caracteristici | medie, p95, deviație, RMS, vârf | ferestre glisante 3/5/15, decalaje, rapoarte, etichete |
| Cod | `lambdas/feature_engineering/core.py` | `ml/src/cncdt_ml/offline.py` |
| Autorat | — | Data Wrangler în Studio |

Ambele folosesc `cncdt_ml/features.py` pentru agregările de bază, iar paritatea cu simularea
este verificată în teste. Detalii: [05-feature-engineering.md](05-feature-engineering.md).

## Mediile

| Mediu | State Terraform | Endpoint ML | Instanță de simulare |
| --- | --- | --- | --- |
| `dev` | `envs/dev.backend.hcl` | serverless | pornită la cerere |
| `staging` | `envs/staging.backend.hcl` | serverless | pornită la cerere |
| `prod` | `envs/prod.backend.hcl` | serverless sau provisioned cu autoscaling | nu are instanță de simulare |

Toate resursele primesc prefixul `${project_name}-${environment}` și etichetele `Project`,
`Environment`, `ManagedBy=terraform`.

## Ce nu este în arhitectură

- **Fără job-uri Glue, fără crawlere.** Glue este doar registru de schemă (ADR 0002).
- **Fără procesare în streaming continuu.** Agregarea este pe microbatch, nu pe fereastră
  glisantă continuă (Kinesis Data Analytics).
- **Fără API Gateway pentru comenzi.** Comenzile intră prin SQS, ca să existe reîncercări și
  coadă de mesaje moarte.
- **Fără QuickSight sau alt instrument BI.** Toate vizualizările sunt dashboard-uri custom
  CloudWatch, definite în Terraform (ADR 0005). Interogările ad-hoc se fac cu Athena, direct
  din consolă.
