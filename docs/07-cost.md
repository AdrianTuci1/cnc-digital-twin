# Costuri

Obiectivul proiectului în `dev` este să coste aproape zero când nu este folosit. Resursele care
costă bani sunt cele care rulează: instanța GPU, endpoint-urile și joburile de procesare.

## Estimări (us-east-1, octombrie 2026)

> Proiectul rulează implicit în `eu-central-1` (Frankfurt). Tarifele de acolo diferă de cele
> de mai jos, care sunt estimări pentru `us-east-1`; verificați pagina de prețuri AWS pentru
> regiunea folosită înainte de a vă baza pe aceste cifre.

| Resursă | Preț aproximativ | Când costă |
| --- | --- | --- |
| `g5.2xlarge` (1× A10G, 8 vCPU, 32 GiB) | 1.01 USD/oră | cât timp instanța este `running` |
| `g5.2xlarge` pe spot, în `eu-central-1` | 0.74–0.90 USD/oră (măsurat 2026-10-05, pe zone) | cât timp instanța rulează; ~50% din on-demand |
| Volum gp3 300 GB | 0.08 USD/GB-lună ≈ 24 USD/lună (0.0952 în `eu-central-1`) | cât timp volumul există |
| Volumul rădăcină al simulării, 200 GB gp3 | 0.0952 USD/GB-lună ≈ 19 USD/lună | cât timp instanța există; se șterge la terminare |
| Volumul de date persistent al simulării, 200 GB gp3 | 0.0952 USD/GB-lună ≈ 19 USD/lună | cât timp volumul există, chiar dacă nu rulează nicio instanță (ADR 0011) |
| Snapshot manual al volumului de date | 0.05 USD/GB-lună, doar pe blocurile modificate | cât timp snapshot-ul există |
| Snapshot EBS (standard, incremental) | 0.05 USD/GB-lună, doar pe datele unice | cât timp snapshot-ul există |
| Endpoint Client VPN, o subrețea asociată | 0.10 USD/oră ≈ 72 USD/lună, conectat sau nu; + 0.05 USD/oră per client conectat | cât timp endpoint-ul există |
| Elastic IP, atașat unei instanțe pornite | 0 USD | cât timp este atașat |
| Instanță oprită (`stopped`) | doar EBS | `twinctl stop` |
| Endpoint serverless | 0 USD când nu este chemat; se plătește pe invocare și pe durata de calcul | la fiecare invocare |
| Endpoint provisioned `ml.m5.large` | ≈ 0.115 USD/oră | cât timp este `InService` |
| Job de procesare `ml.m5.4xlarge` | ≈ 0.92 USD/oră | cât timp rulează jobul |
| Data Wrangler în Studio | pe instanța kernel a domeniului | doar cu aplicația deschisă |
| S3 (date lake) | 0.023 USD/GB-lună, tranziție la IA/Glacier prin lifecycle | pe volum stocat |
| S3 (bucket-ul corpusului de antrenament) | 0.023 USD/GB-lună, fără tranziție (se citește la fiecare antrenare) | pe volum stocat |
| Modal, CPU | 0.04716 USD/nucleu fizic-oră (0.0000131 USD/nucleu-secundă) | cât rulează containerul |
| Modal, memorie | 0.008 USD/GiB-oră (0.00000222 USD/GiB-secundă) | cât rulează containerul |
| Modal, GPU T4 | 0.59 USD/oră (0.000164 USD/secundă) | cât rulează containerul cu cadre |
| Modal, ieșire de rețea | 0.04 USD/GiB, primul TiB pe lună gratuit | pe datele scrise din containere |
| Firehose | ≈ 0.029 USD/GB ingestat + conversie Parquet ≈ 0.018 USD/GB | pe volum |
| Lambda | în limita gratuită pentru volumul de demo | pe invocări și GB-s |
| Metrică custom CloudWatch | 0.30 USD/lună per metrică + 0.01 USD per 1000 cereri `PutMetricData` | pe metrice publicate și pe cereri |
| Dashboard CloudWatch | 3 USD/lună per dashboard (primele trei sunt gratuite) | cât timp dashboard-ul există |
| Notificare SNS e-mail | 0.00 USD (primele 1000 de notificări e-mail sunt gratuite) | pe notificare |
| Notificare SNS SMS în `eu-central-1` | ≈ 0.06–0.08 USD per mesaj, în funcție de destinație | pe mesaj trimis |
| EFS de home Studio (Standard) | ≈ 0.30 USD/GB-lună | cât timp file system-ul există, inclusiv după `make down` |
| AWS Backup pentru EFS (incremental) | ≈ 0.05 USD/GB-lună | pe volumul punctelor de restaurare |
| Buget lunar AWS | 0 USD pentru primele două bugete | serviciul de bugete este gratuit în acest volum |

## Controale implementate

1. **Instanța GPU este efemeră.** `twinctl down` șterge instanța, discul rădăcină, security
   group-ul, cheia și EIP-ul. Instanța rulează pe piața spot, cu cerere `one-time`: la o
   întrerupere AWS anunță cu două minute și termină instanța, fără să repornească nimic
   automat (ADR 0010), iar `twinctl stop` oprește facturarea compute în aproximativ un minut,
   păstrând discul. Volumul de date persistent are alt stack și alt state, deci `sim-down` nu
   îl atinge.
2. **Volumul de date se șterge doar manual.** Stă în `stacks/sim-data` (state local separat) și
   dispare exclusiv prin `make sim-data-down`, care cere confirmare și, implicit, face întâi un
   snapshot. Snapshot-urile se creează doar cu `make sim-data-snapshot`; nu există politică
   automată de ciclu de viață (ADR 0011).
3. **Oprirea automată a Studio.** Domeniul are `app_lifecycle_management.idle_settings` activ,
   cu `idle_timeout_in_minutes` configurabil (implicit 60). Aplicațiile se închid singure.
4. **Tipuri de instanță aprobate.** Politica `DenyUnapprovedTrainingInstances` refuză orice
   cerere de antrenare sau procesare cu un tip care nu apare în
   `sagemaker_training_instance_types`. Nu se poate porni accidental o instanță GPU.
5. **Endpoint serverless implicit.** `ml_endpoint_deployment_mode` este `serverless` în `dev`,
   unde traficul este zero între demonstrații.
6. **Lifecycle pe S3.** Datele brute trec în Standard-IA după 30 de zile și în Glacier Instant
   Retrieval după 180; versiunile vechi expiră.
7. **Alarme de buget tehnic.** Alarma compusă `pipeline_degraded` și alarma de status-check a
   instanței anunță pe SNS, ca o instanță uitată pornită să fie observată.
8. **Rezervare de concurență pe Lambda.** Lambda-ul de feature engineering are concurență
   rezervată, deci nu poate consuma tot bugetul de concurență al contului.
9. **Buget lunar cu notificări.** Modulul `budget` creează un buget lunar la
   `monthly_budget_usd`, cu praguri la 50% și 80% din cheltuiala reală și la 100% din cea
   prognozată. Este singurul control care prinde cheltuiala scăpată de alarmele tehnice.
10. **Backup pentru EFS-ul Studio.** Planul din `modules/backup` salvează zilnic EFS-ul de
    home și alertează când un job eșuează (`backup-jobs-failed`). În `dev`,
    `studio_backup_force_destroy = true` lasă `terraform destroy` să golească seiful; în
    `prod` rămâne `false`, deci punctele de restaurare se șterg manual.
11. **Audit de igienă.** `make audit` rulează `terraform plan` pe toate stack-urile (sim,
    sim-data, platformă), compară resursele etichetate `ManagedBy=terraform` cu state-ul și
    listă resturile care costă. Pașii de curățare:
    [`ops/runbooks/audit.md`](../ops/runbooks/audit.md).
12. **Metricele custom se publică la o cerere pe minut.** Feature engineering publică
    `DegradationIndex` și `RULMinutes` per element, iar inspecția publică contoarele de
    producție; toate ajung la CloudWatch grupat, la ritmul microbatch-ului de 60 s, nu la
    10 Hz. Fiecare metrică custom costă 0,30 USD/lună, deci setul se ține mic (ADR 0008), iar
    caracteristicile brute ale ferestrei se publică doar cu `PUBLISH_FEATURE_METRICS=true`.
13. **Două dashboard-uri, nu mai multe.** Costul este 3 USD/lună per dashboard; primele trei
    sunt gratuite în cont, dar disciplina rămâne: infrastructură și fabrică (ADR 0008).
14. **SMS doar pe alarmele de fabrică.** Notificarea pe telefon este calea rapidă, dar se
    plătește per mesaj; e-mailul rămâne calea ieftină cu toate detaliile. Contul SNS pornește
    în sandbox, deci până la accesul de producție SMS-ul ajunge doar la numere verificate.

## Estimare lunară pentru `dev`, folosit corect

| Scenariu | Cost |
| --- | --- |
| Proiect inactiv (doar S3, câteva sute de MB, fără volum de date) | sub 1 USD/lună |
| Proiect inactiv cu volumul de date de 200 GB păstrat | ≈ 20 USD/lună |
| Două sesiuni de simulare de 4 ore, pe spot | ≈ 6 USD (≈ 12 USD la tarif on-demand) |
| Zece antrenări de câte 20 de minute pe `ml.m5.4xlarge` | ≈ 3 USD |
| Total tipic | ≈ 29 USD/lună |

Volumul de date se plătește permanent, deci costul de repaus nu mai este zero: ≈19 USD/lună
cât timp există. În schimb, oglinda de conținut Isaac Sim (~10 GB) și cache-ul Kit nu se mai
descarcă și nu se mai reconstruiesc la fiecare instanță, ceea ce scurtează sesiunile. Dacă
proiectul intră într-o pauză lungă, `make sim-data-snapshot` urmat de `make sim-data-down`
lasă doar snapshot-ul, facturat pe blocurile modificate; următorul `make sim-up` recreează
volumul și reia automat cel mai nou snapshot.

## Ce crește costul fără să observi

- O instanță lăsată pornită peste noapte: 24 × 1.01 ≈ 24 USD on-demand, ori 24 × 0.78 ≈ 19 USD
  pe spot.
- **O întrerupere spot termină instanța și discul rădăcină.** Nu costă nimic în plus, dar
  sesiunea se oprește acolo; volumul de date se detașează și se reatașează la următorul
  `make sim-up`. Ce nu e pe volum, în S3 sau într-un AMI golden se reface; `make sim-freeze`
  după fiecare instalare nouă ține AMI-ul la zi.
- **O încercare de a opri instanța spot.** AWS acceptă oprirea doar pentru cererile
  `persistent`, iar cererea de aici este `one-time`, deci `make sim-stop` refuză și nu
  facturează nimic în plus. Pentru o pauză, `make sim-down` termină instanța (compute-ul se
  oprește imediat, iar volumul de date rămâne), iar `make sim-up` o reia pe spot.
- **Volumul rădăcină al unei instanțe lăsate pornite degeaba.** 200 GB gp3 ≈ 19 USD/lună, pe
  lângă costul de GPU. Discul rădăcină se șterge la terminare, deci `make sim-down` oprește
  ambele.
- **Volumul de date persistent, chiar când nu lucrează nimeni: ≈ 19 USD/lună** pentru 200 GB
  gp3. Este singurul cost care nu se oprește cu `make sim-idle`; se oprește doar cu
  `make sim-data-down` (după un snapshot, dacă datele contează).
- **Endpoint-ul Client VPN lăsat pornit: ≈ 72 USD/lună**, chiar cu zero clienți conectați
  (0.10 USD/oră per subrețea asociată). Se stinge cu `make sim-vpn-off` și se readuce cu
  `make sim-vpn-on`; ambele lucrează doar pe modulul VPN, fără să atingă instanța.
- Un endpoint provisioned uitat: ≈ 83 USD/lună.
- **Un SMS care se repetă la fiecare evaluare.** O alarmă rămasă în `ALARM` nu retrimite
  notificări (CloudWatch anunță doar la schimbarea stării), dar o alarmă care oscilează
  `OK ↔ ALARM` trimite un mesaj pe fiecare tranziție. Pragul și perioada se aleg ca starea să
  fie stabilă; reparația care resetează indexul oprește oscilația.
- **Metrice custom adăugate fără socoteală.** Fiecare nume și combinație de dimensiuni costă
  0,30 USD/lună; o dimensiune cu multe valori (de exemplu `part_id`) multiplică numărul de
  metrice fără să aducă informație de urmărit recurent.
- EFS-ul de home al Studio, rămas după ștergerea domeniului: 20 GB ≈ 6 USD/lună, plus
  backup-ul aferent. Nu dispare cu `make down`; `make audit` îl semnalează.
- Un flux Data Wrangler programat des peste date brute nefiltrate: costul citirii din S3 și
  durata jobului cresc cu volumul; filtrați cu Athena înainte.

Verificarea costului instanței se face cu `./tools/twinctl/twinctl cost`, care calculează
orele de funcționare din `LaunchTime` și folosește prețul spot curent, citit din istoricul AWS,
ori tariful on-demand când instanța nu e pe spot. Pentru imaginea completă (derivă, resturi,
buget), rulați `make audit`.
