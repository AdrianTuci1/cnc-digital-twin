# ADR 0013: Colectare cu etichete, demonstrație cu model

- **Status:** acceptat
- **Data:** 2026-10-15
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0009 (inspecție vizuală), ADR 0012 (geamănul MuJoCo), `docs/10`

## Context

Geamănul produce două lucruri diferite din aceeași rulare:

1. **un corpus etichetat**, pentru antrenare: `frames.jsonl` leagă fiecare cadru de familia,
   defectul și verdictul piesei, iar `session.json` ține eticheta fiecărui ciclu;
2. **o rulare de demonstrație**, în care celula trebuie să se comporte ca o linie reală.

Eticheta este gratuită și exactă în geamăn — geometria și eticheta ies din aceeași decizie —,
iar asta este chiar valoarea lui. Dar exact de aici vine și riscul: dacă demonstrația are
acces la etichetă, verdictul nu mai vine din model, ci din parametrul intern al simulării, iar
rezultatul arată ca o inspecție care funcționează.

Scurgerea exista în trei locuri, toate găsite la citirea codului:

- `qa_vision_core.heuristic_probability` combina `tool_wear_index`, `joint_friction_index` și
  `surface_quality` și forța scorul la ≥ 0,9 când `is_synthetic_defect` era adevărat. Rezerva
  se activa ori de câte ori endpoint-ul nu era configurat, adică implicit în `dev`;
- cadrul de inspecție scris de geamăn (`qa_snapshot`) și de `QaSnapshotWriter` din Isaac Sim
  ducea în S3 exact acele patru câmpuri, iar fișierul JSON este cel care pornește Lambda-ul;
- `tool_wear_index` și `joint_friction_index` sunt `1 - h`, adică starea internă a motorului de
  uzură, nu o citire de senzor. Ajungeau în vectorul de intrare al modelului de la hot path
  (`MODEL_FEATURE_ORDER`), deci detectorul de uzură avea răspunsul în intrare.

## Decizie

1. **Două moduri, o singură cale de inferență.** Modul de colectare scrie etichetele în
   artefacte separate (`frames.jsonl`, `session.json`, manifestul de etichete). Modul de
   demonstrație ia fiecare verdict din modelul antrenat. Inferența nu citește niciodată
   eticheta, în niciun mod.
2. **Cadrul de inspecție nu poartă eticheta.** Contractul urcă la **1.2.0** și scoate
   `tool_wear_index`, `joint_friction_index`, `surface_quality` și `is_synthetic_defect`; schema
   se închide (`additionalProperties: false`). Cadrul este identic între cele două moduri, deci
   `snapshots.jsonl` nu se schimbă în funcție de ce urmează să se întâmple cu datele.
3. **`LABEL_FIELDS` este păzit de fiecare scriitor și cititor** al cadrului:
   `cncdt_mj/telemetry.py`, `cnc_cell/telemetry/qa_camera.py` (Isaac Sim) și
   `lambdas/qa_vision_inference/qa_vision_core.py`. Geamănul nu are parametru pentru ele,
   `QaSnapshotWriter` ridică `ValueError`, iar `parse_snapshot` ridică `LeakedLabelError`.
   Geamănul și extensia nu împart cod, deci regula se verifică printr-un test care compară cele
   trei liste.
4. **Fără model nu se fabrică verdict.** Când endpoint-ul lipsește, apelul eșuează ori numărul
   de predicții nu se potrivește, Lambda-ul publică zero verdicte și numără cadrele
   (`QaModelMissing`, `QaEndpointFailures`). Piesa urmează ruta de rezervă din ADR 0009.
5. **Vectorul modelului de uzură conține doar măsurători.** `MODEL_FEATURE_ORDER` scoate
   `tool_wear_index` și `joint_friction_index`. Cele două coloane rămân în contract și hrănesc
   dashboard-ul de degradare (`DEGRADATION_ELEMENTS`), fiindcă acolo arată starea simulată a
   celulei, nu o intrare de model.
6. **Eticheta se citește pe `part_id`.** Legătura dintre imagine și etichetă se face la
   antrenare, prin `part_id`/`cycle_id`, din artefactele de colectare. Nu se copiază în cadru.

## Consecințe

- În `dev`, fără endpoint, inspecția nu mai produce verdicte. Comportamentul este vizibil în
  dashboard (`QaModelMissing`) și în metricele Lambda-ului, în loc să pară că funcționează.
- Modelul devine obligatoriu pentru demonstrație. Ordinea corectă a lucrului este: colectare,
  antrenare, endpoint, abia apoi demonstrație care sortează.
- Un cadru scris de o versiune veche, cu etichete, este refuzat și numărat la `skipped`, fără
  să blocheze lotul.
- Randarea pentru datasetul de piese iese din geamănul MuJoCo. Cu Isaac Sim scos din uz,
  bucla de randare peste manifest (pasul 1 din `docs/10`) se scrie pe renderer-ul MuJoCo, deci
  nu mai depinde de un GPU în cloud pentru colectare.
- Corpusul rămâne onest față de o linie reală: ce intră în model sunt mărimi care ar exista și
  pe o mașină adevărată.
- Rețeta offline (`ml/feature_engineering/flows/`) păstrează coloanele de uzură, fiindcă din ele
  se **derivă etichete** (`wear_severity`, `rul_proxy`). Scriptul de antrenare, când se scrie,
  trebuie să aleagă intrările după `MODEL_FEATURE_ORDER`, nu după coloanele rețetei.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Rezervă pe praguri când lipsește modelul | Produce verdicte în `dev` care par a veni din model; exact scurgerea pe care o închide acest ADR. |
| Eticheta în cadru, „doar pentru evaluare" | Fișierul care pornește inferența este cel mai rău loc pentru ea; evaluarea se face offline, pe artefactele de colectare. |
| Mod separat de colectare, cu schema lui de cadru | Două contracte pentru același fișier, care se dezsincronizează; cadrul rămâne unul singur. |
| Păstrarea `1 - h` ca intrare a modelului | Modelul învață parametrul intern al simulării; pe linia reală coloana nu există. |
| Verdict local în geamăn până există model | Reintroduce exact răspunsul în bucla de decizie, doar mai departe de Lambda. |
