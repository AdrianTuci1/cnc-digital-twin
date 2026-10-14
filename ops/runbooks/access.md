# Runbook — rolul de operator și credențialele

Explică de unde vin credențialele care lansează resursele, cum se verifică atribuirea în
CloudTrail și cum se revocă accesul.

## De ce un rol, fără utilizator nou

Rolul nu are cheie de acces. Se asumă din identitatea de admin pe care o ai deja, iar sesiunea
primită trăiește cel mult câteva ore. Trei consecințe practice:

- Resursele apar în CloudTrail create de `assumed-role/<proiect>-<mediu>-operator/<sesiune>`,
  separat de identitatea umană. Evenimentul `AssumeRole` rămâne legătura dintre cele două.
- Nu există nicio cheie statică de pierdut, de rotit sau de scurs în afara mașinii.
- Revocarea înseamnă ștergerea rolului, nu vânarea unei chei prin fișiere.

Rolul este lat prin construcție: Terraform creează roluri de serviciu și le pasează, iar acolo
„minimul privilegiu" real nu există. Limitele vin din trust, din gardurile explicite și din
faptul că rolul nu poate crea identități permanente.

## Prima configurare

Se rulează o singură dată per cont, cu identitatea de admin, înainte să existe rolul.

Cele două condiții de pornire:

1. **AWS CLI autentificat.** Cu `aws login` (recomandat, fără chei statice) sau cu un profil
   care are cheie de acces. Verifică: `aws sts get-caller-identity`.
2. **Regiunea proiectului activată în cont.** Regiunile opt-in (inclusiv Zurich, `eu-central-2`)
   sunt `DISABLED` până le activezi, iar apelurile acolo eșuează cu erori care par de
   credențiale. Verifică: `aws account list-regions` și caută regiunea în `.env`.

```bash
aws login --region eu-central-1      # sau: aws configure --profile <profil>
make access-plan                     # ce se creează
make access-up                       # creează rolul
make access-show                     # ARN, link de comutare în consolă, bloc de profil
```

Credențialele de lucru se iau apoi cu:

```bash
eval "$(./scripts/assume-operator.sh)"
aws sts get-caller-identity          # arn:aws:sts::<cont>:assumed-role/cncdt-dev-operator/...
make platform-up
```

## Cine citește credențialele

`aws login` scrie în profilul `default` cheia `login_session`, pe care **doar AWS CLI** o
înțelege. Providerul Terraform citește credențiale din mediu, din chei statice sau din surse
SSO, dar nu din `login_session`. De aceea:

- Țintele `make access-plan/up/down` rezolvă singure credențialele, prin
  `aws configure export-credentials`, și le predau providerului ca variabile de mediu.
- Pentru restul stack-urilor, exportă credențialele de rol în shell înainte de `terraform`:

  ```bash
  eval "$(./scripts/assume-operator.sh)"
  ```

- `make access-profile` scrie un profil care asumă rolul (`role_arn` + `source_profile`).
  Este util pentru AWS CLI și `twinctl`, dar Terraform nu îl poate folosi cât timp sursa e o
  sesiune `aws login`, pentru că lanțul de profiluri se oprește în `login_session`.

`make access-profile` modifică doar cheile profilului `cncdt` din `~/.aws/config`: `region`,
`role_arn`, `source_profile` și, dacă `OPERATOR_MFA_SERIAL` este completat, `mfa_serial`.
Restul profilelor rămân neatinse.

## Durata sesiunii

Când sursa este ea însăși o sesiune temporară (inclusiv cea scrisă de `aws login`), asumarea
rolului este un *lanț de roluri*, iar AWS limitează lanțul la **o oră**, indiferent de
`max_session_duration` al rolului. `assume-operator.sh` detectează sursa și reduce singur
durata, cu un mesaj pe ieșirea de eroare:

```
sursa de credențiale este temporară: asumarea este un lanț de roluri, limitat la 1 oră
durata cerută (4h) se reduce la 1h
```

Cu o sursă permanentă (cheie de acces) se aplică integral `OPERATOR_SESSION_HOURS`, până la
durata maximă a rolului.

## Cum verifici atribuirea

Evenimentele din CloudTrail au identitatea sesiunii de rol:

```bash
aws cloudtrail lookup-events --max-results 50 \
  --lookup-attributes AttributeKey=EventSource,AttributeValue=ec2.amazonaws.com \
  --query 'Events[].CloudTrailEvent' --output json \
| jq -r '.[] | fromjson
  | select((.userIdentity.arn // "") | contains("assumed-role/cncdt-dev-operator"))
  | "\(.eventTime)  \(.eventName)  \(.userIdentity.sessionContext.sessionIssuer.userName // "-")"'
```

În consolă: CloudTrail → Event history → filtrează după `assumed-role/cncdt-dev-operator`.
Toate resursele au în plus etichetele `Project`, `Environment` și `ManagedBy=terraform`.

## MFA

Implicit rolul se poate asuma fără MFA. Cu MFA cerut, asumarea trebuie să conțină tokenul,
iar providerul Terraform nu îl poate trimite: blocul `assume_role` nu are câmp `mfa_serial`
(verificabil cu `terraform providers schema -json`). De aceea asumarea o face AWS CLI, iar
Terraform primește sesiunea deja obținută, prin variabile de mediu.

Activează MFA așa:

```bash
make access-up REQUIRE_MFA=true      # trust-ul cere MFA prezent
```

Apoi, în loc de profilul din `~/.aws/config`, obține credențiale pe termen scurt și exportă-le
în sesiunea curentă:

```bash
eval "$(./scripts/assume-operator.sh 123456)"    # codul din aplicația de autentificare
make platform-up
```

`mfa_serial` se citește din `OPERATOR_MFA_SERIAL` (din `.env`) sau din argument. Dacă lipsește
din `.env`, scriptul îl cere de la AWS dacă îl adaugi acolo:

```bash
OPERATOR_MFA_SERIAL=arn:aws:iam::123456789012:mfa/admin
```

Un dispozitiv MFA virtual se creează din consolă: IAM → Users → utilizatorul tău → Security
credentials → Assign MFA device → Authenticator app. ARN-ul apare imediat după asociere.

Calea cu `mfa_serial` în profil funcționează pentru AWS CLI, care știe să ceară tokenul.
Pentru Terraform folosește `assume-operator.sh`.

## Ce nu poate face rolul

Gardurile sunt active cât timp `enable_guardrails` rămâne `true` (implicit).

| Interdicție | Efect |
| --- | --- |
| `DenyPermanentIdentities` | Nu poate crea utilizatori, chei de acces, profile de consolă, provideri SAML ori OIDC |
| `DenySelfEscalation` | Nu își poate modifica propria politică sau trust-ul; `make access-up` merge doar cu profilul de admin |
| `DenyAuditTampering` | Nu poate opri, șterge sau edita trail-uri CloudTrail |
| `DenyAccountLevelChanges` | Nu poate atinge Organizations ori setările de cont |
| `DenyStateBucketDeletion` | Nu poate șterge bucket-ul de state Terraform și nu îi poate schimba politica |

`iam:PassRole` este limitat la rolurile care încep cu `$(PROJECT_NAME)-`.

## Revocare

```bash
make access-down                     # cu profilul de admin: șterge rolul
```

Ștergerea rolului oprește asumările noi imediat. Sesiunile deja emise trăiesc până la
expirare, adică cel mult `SESSION_HOURS` (implicit 4 ore). Pentru o sesiune compromisă, adaugă
o politică de tip `Deny` pe rol sau șterge rolul și așteaptă expirarea.

## Depanare

| Simptom | Cauză probabilă | Ce faci |
| --- | --- | --- |
| `AccessDenied` la `sts:AssumeRole` | Trust-ul nu acoperă identitatea sau MFA lipsește | `make access-show` verifică trust-ul; cu `REQUIRE_MFA=true` folosește `assume-operator.sh` |
| Terraform: `No valid credential sources found` | Providerul nu poate citi sesiunea `aws login` din profil | `eval "$(./scripts/assume-operator.sh)"` înainte de `terraform`; pentru stack-ul `access` folosește `make access-*` |
| `InvalidClientTokenId` sau `AuthFailure` în regiunea proiectului | Regiunea este `DISABLED` pentru cont | `aws account list-regions`; activează regiunea sau alege una deja activă |
| `DurationSeconds exceeds the 1 hour session limit for roles assumed by role chaining` | Sursa e temporară, deci asumarea e lanț de roluri | `assume-operator.sh` reduce durata singur; la un apel manual cere cel mult 3600 |
| `The security token included in the request is invalid` | Credențiale expirate în cache-ul CLI (`~/.aws/cli/cache`) | Reia `assume-operator.sh` sau șterge cache-ul |
| `AccessDenied` la `iam:PutRolePolicy` pe rolul de operator | Comanda rulează cu rolul, nu cu adminul | Folosește identitatea de admin pentru `make access-up` |
| `sts get-caller-identity` arată utilizatorul, nu rolul | Ai `AWS_ACCESS_KEY_ID` exportat, care are prioritate | `unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN` și reia `assume-operator.sh` |
| La `terraform apply` pe IAM: `description failed to satisfy constraint` | Descrierea conține `ă`, `ș` sau `ț`, pe care IAM nu le acceptă | Scrie descrierea fără diacriticele din afara Latin-1 |

## Fișiere

| Fișier | Rol |
| --- | --- |
| `infra/terraform/stacks/access/` | Rolul, trust-ul, permisiunile și gardurile (state local) |
| `scripts/assume-operator.sh` | Credențiale temporare de rol; singura cale când rolul cere MFA |
| `Makefile` | `access-plan`, `access-up`, `access-show`, `access-profile`, `access-down` |
| `.env` / `.env.example` | `AWS_PROFILE`, `AWS_REGION`, `ACCESS_PROFILE`, `PROFILE_NAME`, `REQUIRE_MFA` |
| `docs/adr/0006-rol-de-operator-fara-chei-statice.md` | De ce rol asumat, fără utilizator nou |
