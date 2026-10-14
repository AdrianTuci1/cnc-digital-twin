# Pipeline-uri SageMaker

**Stare: planificat.** Nu există încă un pipeline executabil; pașii de mai jos sunt planul,
iar componentele lui există deja în repository.

## De ce un pipeline

În prezent pașii se rulează manual: fluxul Data Wrangler, apoi `build_features.py`, apoi
antrenarea. Un pipeline îi leagă într-o singură execuție cu nume, parametri și rezultate
trasabile, ceea ce face posibilă compararea a două antrenări și promovarea unui model.

## Pașii

| Pas | Tip | Ce rulează | Intrare | Ieșire |
| --- | --- | --- | --- | --- |
| 1 | `ProcessingStep` | Fluxul Data Wrangler (`run_flow.py` pentru rulare manuală) | `s3://<datalake>/raw/telemetry/` | `s3://<datalake>/features/predictive_maintenance/<versiune>/row-level/` |
| 2 | `ProcessingStep` | `ml/feature_engineering/build_features.py` cu `cncdt_ml/offline.py` | ieșirea pasului 1 | `.../<versiune>/training/` |
| 3 | `TrainingStep` | `ml/training/predictive_maintenance/train.py` | ieșirea pasului 2 | `s3://<datalake>/models/predictive-maintenance/<versiune>/` |
| 4 | `ProcessingStep` | evaluare pe setul de validare | model + date | raport de metrice |
| 5 | `ConditionStep` | prag de metrice | raport | continuare sau oprire |
| 6 | `RegisterModel` | înregistrarea modelului | model + raport | model versionat în registry |

Aceleași șase etape se repetă pentru `vision_qa`, cu altă sursă (cadrele de inspecție) și alt
container de antrenare.

## Parametri

- `SpecVersion` — versiunea rețetei (`pm-features-v1`). Pasul 2 refuză o versiune necunoscută,
  ca un pipeline vechi să nu antreneze pe caracteristici noi.
- `InstanceType` — restricționat de politica domeniului (`DenyUnapprovedTrainingInstances`).
- `ModelApprovalStatus` — `PendingManualApproval`; promovarea în producție cere aprobarea cuiva.
- `DataCapturePrefix` — prefixul de captură folosit la compararea distribuțiilor.

## Ce lipsește înainte de implementare

1. `ml/training/predictive_maintenance/train.py` (script-mode SageMaker).
2. `ml/inference/predictive_maintenance/inference.py` (contractul de inferență).
3. Definirea pipeline-ului cu SDK-ul SageMaker și un test care îl validează fără a-l porni
   (`pipeline.definition()` serializează în JSON, deci poate fi verificat offline).
4. Un loc de rulare: manual din Studio, apoi din CI cu `StartPipelineExecution`.

Până atunci, calea verificată este `make features-verify` plus `tests/e2e/run_closed_loop.sh`.
