# ADR 0005: Dashboard-uri custom CloudWatch pentru vizualizare, fără QuickSight

- **Status:** acceptat
- **Data:** 2026-10-04
- **Decis de:** echipa de proiect

## Context

Proiectul are nevoie de o singură suprafață de vizualizare, folosită în demo și în operare
zilnică: starea pipeline-ului, debitul de ingestie, metricele Lambda-urilor, latența și
erorile endpoint-urilor, GPU-ul instanței de simulare și starea backup-ului.

Datele disponibile sunt de două feluri:

1. **Metrice și loguri operaționale**, produse continuu de servicii (Firehose, Lambda,
   SageMaker, EC2, AWS Backup) și de codul propriu în namespace-ul `CNCDT/Telemetry`.
2. **Date istorice în S3**, în Parquet partiționat pe oră, bune pentru analiză și antrenare.

Prima versiune a documentației amintea QuickSight ca instrument de consum lângă Athena.
Alegerea nu corespunde modului în care este folosit sistemul: nu există dataset-uri, analize
sau utilizatori QuickSight, iar o parte însemnată din ce trebuie urmărit (alarme, metrici de
serviciu, loguri) nu trăiește în S3.

## Decizie

1. **Toate vizualizările sunt dashboard-uri custom CloudWatch.** Sunt definite în
   `infra/terraform/modules/observability` (`aws_cloudwatch_dashboard`), deci sunt versionate,
   trec prin review și se recreează identic la fiecare `apply`.
2. **QuickSight nu se folosește.** Nu se creează dataset-uri, analize, dashboard-uri sau
   subscripții QuickSight, iar infrastructura nu conține nicio resursă QuickSight.
3. **Athena rămâne pentru interogări ad-hoc** peste Parquet-ul din data lake, din consolă.
   Rezultatele care merită urmărite recurent se transformă în widget-uri de dashboard sau în
   alarme, nu în rapoarte separate.
4. **Alertele rămân pe CloudWatch + SNS.** Dashboard-ul este pentru citire; notificarea
   proactivă vine din alarme (inclusiv alarma compusă `pipeline-degraded`).

## Consecințe

- Un singur loc de căutat când ceva pare rupt, cu aceleași nume de metrice și dimensiuni pe
  care le folosesc și alarmele.
- Cost zero pentru vizualizare: dashboard-urile CloudWatch nu se plătesc, în timp ce
  QuickSight are cost per utilizator și per analiză.
- Widget-urile care citesc date istorice din S3 folosesc metrici publicate de cod
  (`CNCDT/Telemetry`), nu interogări Athena cu `StartQueryExecution`, ca dashboard-ul să
  rămână ieftin și rapid.
- Analiza exploratorie rămâne posibilă, dar în afara demo-ului: notebook în SageMaker Studio
  sau interogare Athena din consolă.
- `dashboard_anomaly_widget_link` din stack-ul de platformă permite legarea unui dashboard de
  un notebook de analiză, fără a introduce un al doilea instrument de vizualizare.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Amazon QuickSight | Cost per utilizator și per analiză; ar cere dataset-uri și un strat separat de modelare. Metricele operaționale nu sunt în S3, deci nu le-ar putea afișa. |
| Amazon Managed Grafana | Al doilea sistem de operat, cu propriile surse de date și propriul cost; CloudWatch Dashboard acoperă aceleași metrice pentru acest volum. |
| Grafana sau Kibana auto-găzduit | Cost de operare continuu pentru un demo care trebuie să coste aproape zero în repaus. |
| Rapoarte exportate din Athena în S3 | Statice, fără stare de alarmă; nu răspund la întrebarea „ce se întâmplă acum". |

## Completări ulterioare

- **ADR 0008 (2026-10-05):** dashboard-ul unic s-a împărțit în două, unul de infrastructură și
  unul de fabrică, iar peste el au apărut alarme de degradare cu notificare pe e-mail și SMS,
  care se sting singure după reparație. Nota de cost de mai sus se citește corectat:
  dashboard-urile CloudWatch sunt gratuite doar în limita primelor trei din cont; peste ea,
  fiecare dashboard costă 3 USD/lună.
