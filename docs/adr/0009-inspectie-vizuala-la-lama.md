# ADR 0009: Inspecție vizuală la lama/piston, cu poartă de sortare și rută de rezervă

- **Status:** acceptat
- **Data:** 2026-10-05
- **Decis de:** echipa de proiect
- **Pornește de la:** ADR 0005 (vizualizare), ADR 0007 (ceas real)

## Context

Până acum fotografia QA se făcea la ieșirea din mașină (`/World/QA/Camera`), pe piesa care
tocmai fusese tăiată, iar verdictul împingea un piston. Două lucruri lipseau:

1. **poziția camerei nu era acolo unde se decide ruta.** Lama și opritorul stau la capătul
   benzii de inspecție (`DIVERTER_X`), nu la ieșirea din mașină. Un cadru luat prea devreme
   poate rata un defect apărut la manipulare sau poate arăta alt unghi decât cel care decide;
2. **endpoint-ul primea doar vectorul de caracteristici**, iar PNG-ul rămânea în S3. Un model
   de viziune antrenat pe imagini nu are ce face cu un vector tabular, iar unul antrenat pe
   caracteristici nu are nevoie de poză.

În plus, oprirea piesei la lamă ridică o întrebare care nu exista înainte: ce se întâmplă cu o
piesă pentru care verdictul nu sosește niciodată. Vechiul flux aplica o rută locală optimistă,
iar piesa ajungea în lada care pleacă la client.

## Decizie

1. **A doua cameră, la zona lamei/piston.** `build-scene.py` creează
   `/World/QA/DiverterCamera` (obiectiv 18 mm, lângă `DIVERTER_X`), lângă lama care deviază
   rebutul. Calea este în `CANDIDATE_PATHS["diverter_camera"]` din `sensors.py`.
2. **Cadrul care decide ruta vine din camera de sortare.** Cu poarta pornită, captura QA
   folosește `diverter_camera` și scrie `camera_role = "sorting"`; camera de inspecție de la
   ieșirea din mașină rămâne rezervă, cu `camera_role = "inspection"`.
3. **Fotografia ajunge efectiv la endpoint.** Lambda-ul de inspecție citește PNG-ul din S3
   și îl trimite inline, în base64, împreună cu vectorul de caracteristici și metadatele. Un
   singur payload, un singur apel `InvokeEndpoint`.
4. **Limita de mărime este explicită.** `InvokeEndpoint` acceptă 6 MB, iar base64 umflă datele
   cu 4/3, deci pragul intern este 4 MB (`IMAGE_PAYLOAD_LIMIT_BYTES`). Un cadru mai mare este
   sărit și jurnalizat; cererea continuă cu vectorul de caracteristici.
5. **O poartă de sortare ține piesa la lamă până sosește verdictul.** Piesa este oprită de
   opritor, fereastra de verdict se deschide, iar verdictul cloud închide așteptarea și
   aplică ruta. Clasa este `DiverterGate` din `cncdt/cnc_cell/diverter.py`.
6. **Ruta de rezervă este rebutul.** Când nici cloudul, nici previziunea locală (dacă
   `CNCDT_SORT_ON_LOCAL=true`) nu răspund, piesa urmează `diverter_fail_safe`, implicit
   `scrap`. O piesă fără verdict nu are cum să fie declarată bună: lada de piese bune este cea
   care ajunge la client, iar rebutul se poate reexamina.
7. **Un verdict întârziat nu mișcă lama sub altă piesă.** Un verdict pentru o piesă care a
   plecat deja este numărat (`stale_verdicts`) și jurnalizat, fără să acționeze opritorul.
8. **Contractul de cadru urcă la 1.1.0.** `camera_role` devine câmp obligatoriu în
   `telemetry/schemas/cnc.qa.snapshot.v1.json`, cu valori `sorting` și `inspection`.

## Consecințe

- Decizia de rută este luată pe cadrul din locul în care se decide, nu pe unul de la ieșirea
  din mașină.
- Modelul de viziune primește imaginea și caracteristicile în același apel; un al doilea drum
  prin S3 dispare.
- O piesă oprită la lamă blochează banda până la verdict. Fără un verdict, o oprește până la
  sfârșitul ciclului, apoi o eliberează pe ruta de rezervă. Latura de siguranță este rebutul.
- Debandarea este vizibilă: `waits`, `cloud_routes`, `local_routes`, `fail_safe_routes` și
  `stale_verdicts` sunt numărate și afișate în panou; butonul **Eliberează poarta** golește
  poarta manual la nevoie.
- Politica IAM existentă acoperă `s3:GetObject` pe `raw/qa-snapshots/*`, deci citirea PNG-ului
  nu a cerut o schimbare de permisiuni.
- Cadrul rămâne în S3 ca urmă de audit, chiar dacă endpoint-ul nu are nevoie de el.
- Dacă Replicator nu este disponibil, captura cade pe viewport-ul activ, iar dacă nici acesta
  nu reușește, se scrie doar JSON-ul; pipeline-ul rămâne testabil.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Doar camera de la ieșirea din mașină | Poate rata defecte apărute la manipulare și arată alt unghi decât locul în care se decide ruta. |
| Poză doar în S3, endpoint-ul primește vectorul | Un model de viziune nu are ce face cu un vector tabular; ar cere un al doilea drum de citire la fiecare verdict. |
| Poză separată, într-un al doilea apel | Dublează latența și numărul de invocări; un singur payload este mai simplu și mai ieftin. |
| Poză necondiționat, fără prag de mărime | Un cadru peste limita `InvokeEndpoint` face apelul să eșueze; pragul intern degradează elegant la caracteristici. |
| Rută de rezervă către piese bune | Trimite la client o piesă neinspectată; rebutul este latura sigură și se poate reexamina. |
| Aplicarea verdictului întârziat pe piesa din poartă | Ar mișca lama sub o piesă greșită, exact defectul pe care numărătoarea de verdicte întârziate îl previne. |
