# Card de model: pm-rcf-v3

- **Stare:** candidat pentru hot path; endpoint serverless, prag 1,58 cu alarmă pe două
  ferestre consecutive.
- **Data:** 2026-10-25
- **Job:** `cncdt-pm-rcf-20261025-023313`, `ml.m5.large` spot, container built-in Random Cut
  Forest
- **Artefact:** `s3://cncdt-dev-datalake-864981730944/models/predictive-maintenance/pm-rcf-v3/model.tar.gz`

## Ce face

Un Random Cut Forest antrenat **doar pe ferestre sănătoase** din geamănul MuJoCo. Scorul lui
crește când o fereastră de 60 s iese din distribuția învățată; pragul transformă scorul în
alarmă. Modelul nu prezice uzura: semnalează o fereastră care nu seamănă cu mersul normal.

## Date

| Rulare | Rol | Ferestre | Degradate |
| --- | --- | --- | --- |
| `run-20261018T230709` | antrenare (sănătos) | 474 | 0 |
| `run-20261024T230425` | test (rate 5) | 362 | 77 |

Fiecare fereastră are 60 s și intră în model doar dacă are cel puțin 30 de eșantioane în
starea `CUTTING` (3 s la 10 Hz), exact regula din Lambda (`MIN_CUTTING_SAMPLES`). Statisticile
se calculează **pe eșantioanele de așchiere**, nu pe toată fereastra: altfel proporția de
repaus acoperă semnalul de uzură, iar modelul învață încărcarea celulei în loc de sănătatea
sculei.

Vectorul modelului, în ordinea din `MODEL_FEATURE_ORDER`:
`spindle_load_mean`, `spindle_load_p95`, `spindle_load_std`, `vibration_rms`, `vibration_peak`,
`vibration_std`, `coolant_flow_lpm`, `temp_c_mean`, `axis_feed_mean`.

Eticheta de evaluare (nu intră în model): `tool_wear_index_max >= 0,50` sau
`joint_friction_index_max >= 0,50` sau `temp_c_max >= 78 °C`.

## Metrice pe test

- prag 1,58 (percentila 97,5 pe ferestrele de antrenare), alarmă pe **două ferestre
  consecutive**;
- detecție 0,74 (57 din 77 de ferestre degradate), precizie 1,00, **0 alarme false pe oră**;
- preaviz minim 21 min de la începutul sesiunii până la prima fereastră degradată;
- AP 0,665 pe scorul continuu.

Ploturile sunt în `models/predictive-maintenance/pm-rcf-v3/plots/`: cronologie pe sesiune,
histograma sănătos/degradat, curba precizie-recall și compromisul prag–alarme false.

## Limite

- Scorul RCF se deplasează între rulări: pragul este calibrat pe o celulă pornită curat și
  ține pentru o demonstrație care începe cu scula nouă.
- Rularea de test pornește deja cu sculă parțial uzată, deci „sănătos” acolo înseamnă uzură sub
  0,50; rata de alarme false de 0/ oră este măsurată pe această rulare.
- Etichetele vin din uzura simulată, nu din drift real de senzor. Un model pentru linia reală
  cere date reale și recalibrare.
- Percentila 99 (1,65) dă doar 9% detecție: două ferestre consecutive peste ea apar rar.
