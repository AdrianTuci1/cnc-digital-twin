# ADR 0026: Panouri și alarme CloudWatch pe dimensiunile publicate

- **Status:** acceptat; amendează punctul 3 din ADR 0008
- **Data:** 2026-10-26
- **Decis de:** echipa de proiect
- **Pornește de la:** ADR 0005 (CloudWatch ca singur instrument de vizualizare),
  ADR 0008 (două dashboard-uri și alarme care se sting), ADR 0013 (eticheta nu intră pe calea
  de inferență), [05-feature-engineering.md](../05-feature-engineering.md)

## Context

Dashboard-urile și alarmele au fost scrise pe numele metricilor, cu dimensiunea `Element`
atunci cînd era nevoie de separare pe element. Metricele se publică însă cu un set mai larg:
`DegradationIndex`, `RULMinutes` și `SamplesAggregated` poartă `DeviceId`, `CellId` (și
`Element`), iar `WindowsProcessed` poartă `Stage = aggregate`.

CloudWatch identifică o metrică prin combinația completă de dimensiuni, deci o interogare cu
un set parțial caută o altă serie. Verificarea în cont, pe o fereastră de șase ore, a arătat:

| Interogare | Puncte |
| --- | --- |
| `DegradationIndex` cu `Element = cnc` | 0 |
| `DegradationIndex` cu `DeviceId`, `CellId`, `Element` | 12 |
| `WindowsProcessed` fără dimensiuni | 0 |
| `WindowsProcessed` cu `Stage = aggregate` | 12 |
| `SamplesAggregated` fără dimensiuni | 0 |
| `SamplesAggregated` cu `DeviceId`, `CellId` | 12 |

Datele ajungeau (Lambda-ul de feature engineering era invocat de 14–16 ori pe oră), dar
panourile și alarmele nu le găseau. Consecința cea mai gravă: alarmele de degradare
(`*-cnc-degradation`, `*-robot-degradation`, `*-cnc-rul-low`, `*-robot-rul-low`, apoi perechile
`arm1` și `arm2`) stăteau în `OK` fiindcă nu aveau date, iar
`treat_missing_data = notBreaching` ascundea asta. Scenariul
central al demonstrației — injectez uzură, vine alarma, repar, alarma se stinge — nu se putea
întîmpla.

În plus, dashboard-ul de fabrică avea două panouri de producție (`PartsGood`, `PartsScrap`) la
`period = 3600`. Contoarele sunt publicate de Lambda-ul de inspecție, care se declanșează din
notificarea S3 pe `raw/qa-snapshots/`. Geamănul MuJoCo cheamă endpoint-ul de viziune direct, ca
să decidă ruta în buclă, deci în fluxul demonstrativ nu se urcă niciun cadru în S3, Lambda-ul de
inspecție nu rulează, iar contoarele nu apar niciodată. Chiar dacă ar apărea, un punct pe oră
nu spune nimic într-un clip de trei minute.

## Decizie

1. **Orice widget și orice alarmă pe `CNCDT/Telemetry` poartă setul complet de dimensiuni
   publicat.** `DegradationIndex` și `RULMinutes`: `DeviceId`, `CellId`, `Element`.
   `SamplesAggregated` și `AnomalyScore`: `DeviceId`, `CellId`. `WindowsProcessed`:
   `Stage = aggregate`. Metricele fără dimensiuni (`EndpointFailures`, `QaEndpointFailures`,
   `QaModelMissing`, `AnomalyDetectedWindows`) rămîn fără dimensiuni.
2. **Identitatea de telemetrie devine configurație explicită.** `DeviceId` și `CellId` se
   declară în stiva `platform` (`telemetry_device_id`, `telemetry_cell_id`) și se pasează
   modulului `observability`. Valorile lor implicite sînt cele cu care pornește geamănul
   (`CNCDT_DEVICE_ID=cnc-cell-01`, `CNCDT_CELL_ID=cell-a`); dacă cineva schimbă variabilele din
   `.env` ale simulării, trebuie să schimbe și valorile Terraform, altfel panourile se golesc
   din nou.
3. **Dashboard-ul de fabrică arată mărimi care există în fluxul demonstrativ:** starea
   alarmelor, cîte eșantioane și ferestre ajung pe minut, indicele de degradare per element,
   minutele pînă la prag, ferestrele anomale cu scorul lor. Panourile de producție pe oră și
   rata de rebut ies din dashboard pînă cînd există un contor de piese alimentat de hot path.
4. **Pragul alarmei de anomalii se scrie în titlu, nu ca linie de prag.** Alarma adună 10
   minute (`anomaly_alarm_threshold = 3`), iar panoul rămîne la 60 s ca să reacționeze imediat
   în demo. O linie desenată la 3 pe un grafic pe minut ar sugera un prag de șase ori mai mic.
5. **Degradarea brațelor se publică per braț.** Contractul `1.1.0` adaugă
   `joint_friction_index_arm1` și `joint_friction_index_arm2`, iar Lambda-ul publică elementele
   `arm1` și `arm2`, fiecare cu `DegradationIndex`, `RULMinutes`, gauge și cele două alarme.
   `joint_friction_index` rămîne agregatul (brațul mai rău) în contract, dar panourile și
   alarmele citesc elementele separate. Panoul MuJoCo injectează și repară fiecare braț cu
   `inject_wear` / `repair` pe componenta lui.

## Consecințe

- Alarmele de degradare și de RUL primesc date și pot trece în `ALARM`; reparația din panoul
  MuJoCo le readuce în `OK` la evaluarea următoare, ca în ADR 0008. Fiecare braț are perechea
  lui de alarme, deci o reparație pe un singur braț stinge doar alarma acelui braț.
- Dashboard-ul de fabrică se umple în 1–2 minute de la pornirea geamănului, fiindcă toate
  panourile au o sursă în fluxul real.
- `PartsGood` și `PartsScrap` rămîn publicate de Lambda-ul de inspecție pentru calea reală
  (S3 → Lambda), dar nu mai sînt afișate. Un contor de piese pentru demo cere o sursă în hot
  path (de exemplu cicluri închise numărate în fereastră), nu o copie a contoarelor de
  inspecție.
- Orice metrică nouă cu dimensiuni trebuie adăugată în widget-uri și alarme cu exact aceleași
  dimensiuni; altfel se repetă tăcut aceeași eroare.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Păstrarea panourilor de producție și urcarea cadrelor în S3 pentru Lambda | Adaugă latență în bucla de decizie și o a doua cale de verdict, pe lîngă apelul direct al endpoint-ului. |
| Dimensiuni parțiale, cu speranța că CloudWatch potrivește prefixul | CloudWatch cere setul complet; seria nu există și panoul rămîne gol fără eroare. |
| Expresii `SEARCH` în locul dimensiunilor explicite | Pentru gauge-uri aduce toate elementele în același ac, iar etichetele depind de ordinea dimensiunilor; configurația explicită este verificabilă. |
| `DeviceId`/`CellId` hardcodate în widget-uri | Dublură a identității din simulare; schimbarea lor într-un singur loc trebuie să ajungă în toate panourile. |
| Producție pe oră la `period = 3600` | Un punct pe oră nu spune nimic într-un demo de trei minute. |
