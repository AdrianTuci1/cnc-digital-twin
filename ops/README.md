# Operațiuni

Materiale pentru situații reale: ce verifici, în ce ordine, și ce faci când ceva rămâne
în urmă.

| Director | Conține |
| --- | --- |
| `runbooks/` | Proceduri pas cu pas, verificate în ordinea în care se execută. |
| `dashboards/` | Copii exportate de dashboard-uri. Dashboard-urile CloudWatch sunt definite în Terraform, în `infra/terraform/modules/observability`; aici se păstrează doar JSON-ul exportat din consolă, pentru comparație. |

## Dashboard-uri

Modulul `observability` creează două dashboard-uri, separate pe audiență (ADR 0008). Ambele
folosesc namespace-ul de metrice `CNCDT/Telemetry`, plus `CNCDT/Simulation` pentru GPU.

| Dashboard | Nume implicit | Ce arată | Cine se uită |
| --- | --- | --- | --- |
| Infrastructură | `<prefix>-infra` | starea alarmelor de serviciu, ingestia IoT și Firehose, metricele Lambda, endpoint-urile SageMaker, sănătatea pipeline-ului de date, GPU-ul simulării, erorile recente | cine ține platforma |
| Fabrică | `<prefix>-factory` | starea celulei (alarme de degradare și de RUL), producția pe oră (bune/rebut), rata de rebut, câte un gauge per element degradat, minutele până la pragul de avarie, anomaliile modelului | operatorul de fabrică |

Numele exacte ies din stiva de platformă:

```bash
terraform -chdir=infra/terraform/stacks/platform output -json dashboard_names
terraform -chdir=infra/terraform/stacks/platform output -json dashboard_urls
```

Exportul din consolă (CloudWatch → Dashboards → Actions → View/Edit source → Copy) se pune în
`dashboards/`, ca punct de comparație cu ce produce Terraform. Regula rămâne: Terraform este
sursa de adevăr, consola doar pentru investigație.

## Alarme și notificări

Alarmele de fabrică (`DegradationIndex`, `RULMinutes`, cu dimensiunea `Element`) intră în
`ALARM` cu notificare pe SNS și trec singure în `OK` după reparația din panoul Isaac Sim
(`replace_tool`, `repair_joints`, `repair_all`), care resetează indexul. Subiectul SNS
`<prefix>-alarms` primește abonamente de e-mail și, opțional, de SMS
(`alarm_email_endpoints`, `alarm_sms_numbers`). Contul SNS pornește în sandbox, deci până la
accesul de producție SMS-ul ajunge doar la numere verificate.

## Runbook-uri

| Runbook | Când se folosește |
| --- | --- |
| [access.md](runbooks/access.md) | La prima configurare a contului, când schimbi credențialele sau când verifici cine a creat o resursă în CloudTrail. |
| [audit.md](runbooks/audit.md) | După fiecare sesiune de lucru, înainte de `make down` și oricând costul pare mai mare decât estimarea. |

Regula generală: `make audit` înainte de `make down`, ca să știi dinainte ce va rămâne
în cont după ștergere.
