# Runbook — audit de igienă și curățare

Răspunde la două întrebări care apar des: mai coincide infrastructura din AWS cu codul
Terraform, și a rămas ceva în urmă care costă bani.

## Când se rulează

1. După fiecare sesiune în care ai deschis SageMaker Studio sau ai lăsat instanța GPU pornită.
2. Înainte de `make down`, ca să știi dinainte ce **nu** va șterge.
3. Când factura depășește estimarea din [`docs/07-cost.md`](../../docs/07-cost.md).

## Comanda

```bash
make audit          # sau ./scripts/audit.sh
```

Cerințe: `terraform` mereu; `aws` (CLI v2) și `jq` pentru verificările din AWS. Fără
credențiale, auditul rulează parțial și spune explicit ce a sărit.

| Cod de ieșire | Înseamnă | Ce faci |
| --- | --- | --- |
| 0 | Curat: nicio derivă, niciun rest | Nimic. |
| 1 | Există elemente de verificat | Parcurge secțiunile de mai jos, în ordine. |
| 2 | Audit parțial: lipsesc unelte sau credențiale | Instalează AWS CLI v2 și `jq`, apoi autentifică-te. |

## Ce verifică

| Secțiune | Întrebarea | Rezultat bun |
| --- | --- | --- |
| Derivă Terraform | `terraform plan` mai propune modificări? | „nicio schimbare față de cod" pentru `platform`, `sim` și `sim-data` |
| Inventar | Există resurse etichetate `ManagedBy=terraform` care nu apar în state? | „fiecare resursă etichetată apare în state" |
| Resturi care costă | EFS Studio, aplicații Studio, endpoint-uri, instanțe, volumul de date, buget | Fără EFS rămas, nicio aplicație deschisă, nicio instanță pornită, volumul de date este cel așteptat |

## Ce rămâne în urmă prin design

| Resursă | De ce rămâne | Cost |
| --- | --- | --- |
| EFS-ul de home al Studio | Domeniul are `retention_policy.home_efs_file_system = "Retain"`, ca o ștergere a domeniului să nu ia notebook-urile | ~0.30 USD/GB-lună, plus backup-ul |
| Bucket-ul de state Terraform | Creat de `bootstrap`, intenționat în afara stack-ului de platformă | câțiva bani |
| Seiful AWS Backup | Creat de planul de backup; `force_destroy` decide dacă `terraform destroy` îl poate goli | storage-ul punctelor de restaurare |
| Puncte de restaurare | În `prod` `studio_backup_force_destroy = false`, deci `terraform destroy` cere ștergere manuală | storage incremental |
| Volumul de date al simulării | Stă în `stacks/sim-data`, cu state separat, ca `make sim-down` să nu îl poată șterge (ADR 0011) | ≈ 19 USD/lună pentru 200 GB gp3 |
| Snapshot-urile volumului de date | Se fac doar manual (`make sim-data-snapshot`); următorul `sim-up` reia automat cel mai nou | storage incremental, doar pe blocurile modificate |

Restul resurselor sunt în state-ul Terraform, deci `make down` le șterge.

## Curățare, pas cu pas

### 1. Derivă Terraform

```bash
make platform-plan     # citește lista completă de modificări propuse
make platform-up       # readuce realitatea la forma din cod
```

Dacă o resursă nu mai este dorită, scoate-o din cod; planul o va propune spre ștergere.
Nu șterge manual o resursă care este încă în state: următorul `apply` va eșua sau o va recrea.

### 2. Resurse etichetate care nu apar în state

Auditul le listează cu ARN-ul complet. Verifică-le în consolă înainte de orice ștergere.
Cauze frecvente: resurse create manual de un operator, resurse rămase după un `destroy`
întrerupt, sau resurse ale unui alt mediu etichetate cu același `Project`.

```bash
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=ManagedBy,Values=terraform Key=Project,Values=cncdt
```

### 3. Aplicații SageMaker Studio deschise

Blochează ștergerea domeniului și consumă compute. Oprirea automată le închide după
`idle_timeout_in_minutes` (implicit 60).

```bash
aws sagemaker list-apps --query 'Apps[?Status==`InService`]'
```

Închiderea manuală se face din consolă (Studio → File → Shut Down) sau cu
`aws sagemaker delete-app` pentru fiecare combinație domeniu / profil / tip / nume.

### 4. Endpoint-uri SageMaker

Serverless costă doar la invocare. `provisioned` costă în repaus (≈ 0.115 USD/oră pentru
`ml.m5.large`), deci verifică modul:

```bash
aws sagemaker describe-endpoint --endpoint-name cncdt-dev-vision-qa \
  --query 'ProductionVariants[].{Model:ModelName,Instance:CurrentInstanceCount,Serverless:ServerlessConfig}'
```

Dezactivează-le din `envs/<mediu>.tfvars` (`vision_qa_endpoint_enabled = false`), apoi
rulează `make platform-up`.

### 5. Instanțe GPU

```bash
make sim-status        # starea și orele de funcționare
make sim-stop          # oprește compute-ul, păstrează discul rădăcină
make sim-down          # șterge instanța, discul rădăcină, SG, cheia, VPN-ul
make sim-data          # starea volumului de date persistent
make sim-data-down     # ȘTERGE volumul de date (face întâi un snapshot; SNAPSHOT=0 sare peste)
```

Volumul de date nu dispare cu `make sim-down`: este în alt stack și are alt state. Se șterge
doar cu `make sim-data-down`, iar snapshot-urile rămase sunt reluate automat de următorul
`make sim-up`.

### 6. EFS-ul de home al Studio

Este singurul rest cu cost recurent care nu dispare cu `make down`. Șterge-l doar după ce
ai confirmat că backup-ul a reușit (secțiunea următoare).

```bash
aws efs describe-file-systems --query 'FileSystems[].FileSystemId'
aws efs describe-mount-targets --file-system-id fs-XXXXXXXX   # câte unul per subnet
aws efs delete-mount-target --mount-target-id fsmt-XXXXXXXX
aws efs delete-file-system --file-system-id fs-XXXXXXXX
```

`delete-file-system` cere întâi ștergerea mount target-urilor. Ștergerea EFS-ului este
ireversibilă.

### 7. Seif de backup și puncte de restaurare (`prod`)

```bash
aws backup list-recovery-points-by-backup-vault --backup-vault-name cncdt-prod-studio-home-efs
aws backup delete-recovery-point --backup-vault-name cncdt-prod-studio-home-efs \
  --recovery-point-arn <arn>
```

După golire, `terraform destroy` poate șterge seiful.

## Verificarea backup-ului

```bash
aws backup list-backup-jobs --by-backup-vault-name cncdt-dev-studio-home-efs \
  --query 'BackupJobs[].{State:State,Resource:ResourceArn,Start:StartTime}'
```

Un job reușit are `State = COMPLETED`. Eșecurile ajung și în CloudWatch: alarma
`<proiect>-<mediu>-backup-jobs-failed` (metrică `AWS/Backup` →
`NumberOfBackupJobsFailed`, prag 1 pe 24 de ore) publică pe subiectul SNS al alarmelor.

## Restaurarea notebook-urilor

Backup-ul creează un file system nou, nu îl înlocuiește pe cel al domeniului: domeniul
SageMaker nu poate primi un alt EFS de home. Procedura este:

1. Pornește un job de restaurare către un EFS nou:
   `aws backup start-restore-job --metadata '{"file-system-id":"fs-XXXXXXXX","newFileSystem":true,...}'`
   cu `recovery-point-arn` și rolul din `studio_backup_role_arn`.
2. Montează EFS-ul restaurat pe o instanță EC2 din același VPC.
3. Copiază datele în home-ul domeniului curent (montat pe aceeași instanță) cu `rsync`.

## Limite cunoscute

- `resourcegroupstaggingapi` întoarce doar resurse care acceptă etichete, în regiunea
  curentă. Resursele globale (IAM, S3 pentru unele tipuri) pot lipsi din inventar.
- Comparația „etichetat vs. state" este textuală, pe ARN-uri extrase din state. Un ARN pe
  care providerul Terraform nu îl expune ca atribut poate apărea ca rest; verifică-l înainte
  de a-l șterge.
- EFS-ul Studio este creat de SageMaker, cu o politică de file system proprie. Dacă jobul
  de backup eșuează cu `AccessDenied`, adaugă în politica EFS-ului o instrucțiune care
  permite rolului `studio_backup_role_arn` acțiunile de citire folosite de AWS Backup.
- `terraform plan` pe stack-ul `sim` poate raporta derivă când IP-ul public al mașinii
  curente se schimbă și `ssh_allowed_cidrs` conține `auto`. Este așteptat.
