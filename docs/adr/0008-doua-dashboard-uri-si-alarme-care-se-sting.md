# ADR 0008: Două dashboard-uri CloudWatch și alarme de degradare care se sting la reparație

- **Status:** acceptat; punctul 3 amendat de ADR 0026 (panourile de producție au ieșit din dashboard-ul de fabrică)
- **Data:** 2026-10-05
- **Decis de:** echipa de proiect
- **Pornește de la:** ADR 0005 (CloudWatch ca singur instrument de vizualizare)

## Context

ADR 0005 a stabilit că toate vizualizările sunt dashboard-uri custom CloudWatch, definite în
Terraform. Prima versiune avea un singur dashboard, care amesteca două audiențe:

1. **starea infrastructurii** — livrarea Firehose, erorile Lambda, latența endpoint-urilor,
   GPU-ul instanței, backup-ul, erorile recente din loguri;
2. **starea fabricii virtuale** — cât produce celula, cât rebut, cât de uzată este scula, cât
   mai merge până la avarie.

Un singur dashboard înseamnă că operatorul de fabrică derulează printre widget-uri de Firehose
ca să ajungă la producție, iar cine caută o pană de pipeline trece peste grafice de producție.
În plus, lipseau exact lucrurile cerute pentru operare: o alarmă per element degradat, una
care spune „câte minute până la degradare", o notificare pe telefon și o cale de stingere
după reparație.

## Decizie

1. **Două dashboard-uri, separate pe audiență.** Modulul `observability` creează
   `aws_cloudwatch_dashboard.infra` (`<nume>-infra`) și `aws_cloudwatch_dashboard.factory`
   (`<nume>-factory`). Split-ul este pe dashboard și pe widget-uri, namespace-ul de metrice
   rămâne comun (`CNCDT/Telemetry`).
2. **Dashboard-ul de infrastructură** ține: starea alarmelor de serviciu, ingestia IoT și
   Firehose, metricele Lambda-urilor, endpoint-urile SageMaker, sănătatea pipeline-ului de
   date, GPU-ul instanței de simulare, tabelul de erori recente din loguri.
3. **Dashboard-ul de fabrică** ține: sănătatea celulei (alarmele de degradare și de RUL),
   producția pe oră (`PartsGood`, `PartsScrap`), rata de rebut, câte un indicator tip gauge per
   element degradat, timpul până la pragul de avarie și anomalii detectate de model.
4. **O alarmă per element degradat, pe prag de index.** `DegradationIndex` cu dimensiunea
   `Element` (`cnc`, `arm1`, `arm2`), `GreaterThanOrEqualToThreshold`, statistică `Maximum`,
   perioadă 300 s, o singură evaluare. Elementele și pragurile se declară în
   `degradation_elements`, nu se hardcodează.
5. **O alarmă de tip „câte minute până la degradare".** `RULMinutes` cu dimensiunea `Element`,
   `LessThanThreshold`, statistică `Minimum`, perioadă 300 s. Traduce indexul într-o
   propoziție: „Arm 1: 6 minute până la degradare".
6. **Notificarea are două căi.** Subiectul SNS primește abonamente `email` (urma cu detalii)
   și `sms` (ajunge pe telefon în secunde), din `alarm_email_endpoints` și
   `alarm_sms_numbers`.
7. **Alarma se stinge la reparație.** `alarm_actions` notifică la intrarea în alarmă,
   `ok_actions` notifică la revenire. Reparația din panoul Isaac Sim (`replace_tool`,
   `repair_joints`, `repair_all`) resetează indexul, Lambda-ul publică valoarea mică la
   următoarea fereastră, iar alarma trece singură în `OK` în una-două perioade. Nu se folosește
   `SetAlarmState`, care nu este durabil.
8. **Alarmele de fabrică nu se declanșează pe date lipsă.** `treat_missing_data` este
   `notBreaching` pentru alarmele de fabrică și `breaching` pentru cele de pipeline, ca o
   celulă oprită să nu sune ca o pană.

## Consecințe

- Operatorul de fabrică are un singur ecran de citit; cine caută o pană de pipeline are altul,
  fără să deruleze printre widget-uri irelevante.
- Alarma de RUL răspunde direct la întrebarea „cât mai merge până la degradare", în minute de
  operare.
- Notificarea pe SMS este rapidă, dar contul SNS pornește în **sandbox**: până la obținerea
  accesului de producție, SMS-ul ajunge doar la numere verificate.
- Costul crește controlat: al doilea dashboard înseamnă încă ≈ 3 USD/lună, iar fiecare metrică
  custom înseamnă 0,30 USD/lună plus 0,01 USD per 1000 de cereri `PutMetricData`. De aceea
  ratele se publică la o cerere pe minut, nu la 10 Hz.
- Un element nou degradat se adaugă printr-o singură intrare în `degradation_elements`; gauge-ul,
  alarma de index și linia de RUL apar singure.
- Dacă `create_sns_topic` este fals, alarmele rămân fără acțiuni (`alarm_actions = []`), deci
  se pot rula în dev fără să trimită nimic.
- Subiectul SNS este criptat cu o cheie KMS proprie, cu politică pentru CloudWatch și SNS.
  `alias/aws/sns` nu poate primi o politică, iar CloudWatch nu poate publica în topicul criptat
  cu ea: alarma intră în `ALARM`, dar acțiunea eșuează cu „CloudWatch Alarms does not have
  authorization to access the SNS topic encryption key". Cheia proprie adaugă ≈ 1 USD/lună.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Un singur dashboard cu toate widget-urile | Amestecă două audiențe; operatorul de fabrică derulează printre widget-uri de infrastructură. |
| Alarme doar pe metricele de serviciu | Nu acoperă degradarea, care este mărimea de interes a fabricii; un prag pe index și unul pe RUL sunt exact ce trebuie urmărit. |
| Stingerea manuală a alarmei din consolă | `SetAlarmState` nu este durabil, iar alarma ar reveni la următoarea evaluare; reparația care resetează indexul este calea reală. |
| Doar e-mail, fără SMS | E-mailul se pierde între altele; o oprire de celulă cere o notificare care ajunge în secunde. |
| Alarme de fabrică cu `breaching` pe date lipsă | O celulă oprită noaptea ar suna degeaba; lipsa datelor nu este o avarie de fabrică. |
