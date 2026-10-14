# ADR 0003: Data Wrangler pentru feature engineering, fără transformări Glue

- **Status:** acceptat
- **Data:** 2026-10-04
- **Decis de:** echipa de proiect

## Context

P pipeline-ul are nevoie de două seturi de caracteristici, cu cerințe diferite:

1. **Hot path** — la fiecare microbatch de 60 de secunde, Lambda-ul trebuie să producă
   agregările de bază pe fereastră (medie, p95, deviație standard, RMS, vârf) și un scor de
   anomalie. Bugetul este de 120 de secunde per invocare.
2. **Offline** — pentru antrenare sunt necesare ferestre glisante pe mai multe orizonturi,
   decalaje temporale, rapoarte între senzori și etichete. Aici contează explorarea,
   profilarea coloanelor și viteza de iterare.

Prima variantă luată în calcul folosea AWS Glue pentru transformări. Ea a fost respinsă de
echipă: Glue rămâne necesar doar ca registru de schemă pentru conversia JSON → Parquet din
Firehose, nu ca motor de transformare.

## Decizie

1. **Glue este folosit exclusiv ca registru de schemă.** Nu există job-uri Glue, crawlere,
   `DynamicFrame` sau transformări Glue în proiect. Tabelul `telemetry_enriched` definește
   coloanele pe care Firehose le scrie în Parquet și este singurul loc unde schema este
   declarată în afara codului.
2. **Feature engineering-ul offline se face cu Data Wrangler din SageMaker Studio.**
   Fluxurile se construiesc vizual, se exportă ca `.flow`, se comit în
   `ml/feature_engineering/flows/` și se rulează ca joburi de procesare.
3. **Transformările pe timp stau în cod partajat** (`ml/src/cncdt_ml/offline.py`), pentru că
   au nevoie de ordine temporală și de stare între rânduri, iar codul trebuie să ruleze
   identic în CI, în pipeline și în Lambda.

## Consecințe

- Hot path-ul și antrenarea folosesc aceleași definiții: `cncdt_ml/features.py` este copiat în
  arhiva Lambda-ului, iar testul `tests/unit/test_feature_parity.py` verifică paritatea cu
  extensia Isaac Sim.
- Contractul de coloane este verificat automat în trei locuri: tabelul din Terraform, schema
  JSON și `cncdt_ml/schema.py`. O schimbare într-un singur loc face testele să cadă.
- Rețeta de caracteristici este generată din cod (`flows/*.recipe.json`), deci nu poate
  rămâne în urma implementării.
- Costul unui job de procesare este controlat prin politica IAM a domeniului
  (`DenyUnapprovedTrainingInstances`) și prin oprirea automată pe inactivitate a Studio.
- Iterarea vizuală rămâne posibilă: se lucrează în Data Wrangler, dar rezultatul care intră în
  antrenare este produs de cod, nu de un clic nereproductibil.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Job-uri Glue de transformare | Necesită `DynamicFrame` și un catalog separat; echipa a decis să nu folosească Glue pentru transformări. |
| Caracteristici calculate în notebook-uri | Nereproductibile; nu pot rula în CI și nu pot fi comparate între rulări. |
| Ferestre glisante în Lambda | Depășesc bugetul de 120 s și ar duplica logica din antrenare. |
| Întregul lanț de transformări în Data Wrangler, inclusiv ferestrele | Ar cere un `Custom transform` pandas duplicat față de codul care rulează în CI; rețeta marchează pasul, iar referința este `cncdt_ml.offline`. |
