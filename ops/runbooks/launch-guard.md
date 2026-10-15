# Runbook — comutatorul de lansare a infrastructurii

Gard care face ca infrastructura AWS plătită să nu poată fi lansată din greșeală. Nu
restructurează nimic: resursele din Terraform rămân definite exact ca înainte, se blochează
doar calea care le creează sau care le extinde.

## Regula, într-o propoziție

Orice comandă care **creează sau extinde** o resursă plătită cere `INFRA_LAUNCH_ENABLED=true`
în `.env`; orice comandă care **citește** ori **șterge** rămâne neblocată.

Comutatorul e implicit `false`. Nu e o problemă de credențiale, de profil sau de rețea: refuzul
se decide local, înainte de orice apel la AWS.

## Când se citește runbook-ul

1. `make up`, `make sim-up`, `make sim-data-up`, `make platform-up`, `make sim-start`,
   `make training-data-up` ori `make sim-vpn-on` refuză și vrei să știi exact ce să pui în `.env`.
2. `terraform plan` sau `terraform apply` din `infra/terraform/stacks/sim`, `stacks/sim-data` ori
   `stacks/training-data` se termină cu `Resource precondition failed`.
3. Vrei să lansezi deliberat și vrei să știi ce rămâne permis fără comutator.

## Cum arată refuzul

La nivel de twinctl (apare înainte de orice apel la AWS):

```text
refuz: crearea instanței GPU nu pornește (comutatorul de lansare este oprit).

  INFRA_LAUNCH_ENABLED este „false” în .env, iar implicit este „false”.
  Refuzul este deliberat și se decide local, înainte de orice apel la AWS: nu este o
  eroare de credențiale, de profil sau de rețea.

  Ca să lansezi deliberat, pune în .env:
        INFRA_LAUNCH_ENABLED=true
  apoi repetă comanda:
        make sim-up   (sau ./tools/twinctl/twinctl up)

  Fără comutator rămân permise: down, data-down, vpn-off, stop (șterg ori opresc), precum
  și status, cost, data, spot, outputs, logs, freeze (citesc ori salvează).
  Detalii: ops/runbooks/launch-guard.md
```

La nivel de Terraform (pentru `terraform apply` rulat direct din directorul stack-ului):

```text
Error: Resource precondition failed

  on main.tf line 9, in resource "terraform_data" "launch_guard":
   9:     precondition {

Lansarea infrastructurii de simulare este blocată deliberat: `launch_confirmed` este `false`.
...
        Ca să lansezi deliberat:
          pune în .env:  INFRA_LAUNCH_ENABLED=true
          apoi rulează:  make sim-up          (sau ./tools/twinctl/twinctl up)

        Ori, direct din acest director:
          terraform apply -var launch_confirmed=true
```

## Cum se deblochează deliberat

```bash
# 1. în `.env`:  INFRA_LAUNCH_ENABLED=true
# 2. lansează:
make sim-up            # instanța GPU (cere și volumul de date, dacă lipsește)
make sim-start         # repornește o instanță on-demand oprită (SIM_USE_SPOT=false)
make sim-data-up       # doar volumul EBS persistent
make sim-vpn-on        # doar endpoint-ul Client VPN
make up                # platformă + instanță
```

Comutatorul se citește la fiecare rulare, deci valoarea schimbată în `.env` se aplică imediat —
nu e nevoie de `export`. Verificarea reală e în `tools/twinctl/twinctl` și în Terraform;
`Makefile` doar repetă refuzul devreme, ca mesaj.

Direct din Terraform, gardul se deschide cu argumentul explicit:

```bash
cd infra/terraform/stacks/sim          # sau stacks/sim-data
terraform apply -var launch_confirmed=true
```

## Cum se blochează la loc

```bash
# în `.env`:  INFRA_LAUNCH_ENABLED=false
```

Ce rulează deja nu se atinge: comutatorul blochează lansarea, nu oprește resursele existente.
Pentru oprire sau curățenie, vezi secțiunea următoare.

## Ce rămâne mereu permis, fără comutator

| Comandă | Ce face |
| --- | --- |
| `make sim-down` (`twinctl down`) | șterge instanța, discul rădăcină, SG, cheia, VPN-ul |
| `make sim-data-down` (`twinctl data-down`) | șterge volumul de date, după un snapshot |
| `make sim-vpn-off` (`twinctl vpn-off`) | șterge endpoint-ul Client VPN |
| `make sim-stop` (`twinctl stop`) | oprește instanța on-demand; discul rămâne |
| `make sim-status`, `make sim-cost` | starea și estimarea de cost |
| `make sim-data`, `make sim-data-snapshot` | starea și snapshot-urile volumului |
| `make sim-spot`, `make sim-outputs`, `make sim-logs` | prețuri spot, ieșiri, loguri |
| `make sim-freeze` | creează un AMI golden (reduce costul, nu îl crește) |
| `make audit`, `make tf-validate` | inventar, curățenie, validare |

`freeze` și `data-snapshot` nu sunt blocate deliberat: creează snapshots, dar sunt pași de
reducere a costului și fac parte din căile de teardown (`down --freeze`, `data-down` face un
snapshot înainte de ștergere).

`start` este blocat, deși nu creează nimic: pe `SIM_USE_SPOT=false` repornește instanța GPU,
adică exact cheltuiala pe care gardul o oprește. Nu e chemat de nicio cale internă (`down`,
`data-down`), deci blocarea lui nu poate atinge teardown-ul. `stop` rămâne liber: oprește
instanța și reduce costul.

## Unde stă gardul

| Nivel | Ce face |
| --- | --- |
| `Makefile` | refuză devreme, cu un rând de explicație: `up`, `sim-up`, `sim-start`, `sim-data-up`, `platform-up`, `training-data-up`. Strat de mesaj, nu de adevăr. |
| `tools/twinctl/twinctl` | citește `INFRA_LAUNCH_ENABLED` în `load_env` și refuză înainte de orice apel la AWS: `up`, `start`, `data-up`, `vpn-on`. Pe căile de lansare trimite `-var launch_confirmed=true`; pe cele de ștergere îl trimite mereu. |
| `infra/terraform/stacks/sim`, `stacks/sim-data` și `stacks/training-data` | `terraform_data.launch_guard` cu `lifecycle { precondition }`: refuză orice plan sau apply fără `-var launch_confirmed=true`. Acesta e nivelul care închide ocolirea prin `terraform apply` direct. |

`scripts/audit.sh` trimite `launch_confirmed=true` la planurile de pe cele două stack-uri
gardate: inventarul trebuie să poată citi starea indiferent de comutator.

## Verificat pe viu

Pe Terraform 1.15.8, cu un stack minimal identic (aceleași blocuri de gard):

- precondiția se evaluează la `plan` și la `apply`, chiar și când resursa-gard este nemodificată,
  deci `apply` nu poate ocoli gardul care apare la plan;
- precondiția **nu** se evaluează la `destroy` — ștergerea a trecut cu `launch_confirmed=false`;
  twinctl trimite oricum `true` pe căile de teardown, ca să nu depindă de acest detaliu.

## Limite cunoscute

- Stack-ul `platform` nu are gard în Terraform, doar refuzul din `Makefile`: un
  `terraform apply` rulat direct din `infra/terraform/stacks/platform` nu e blocat. Dacă e
  nevoie de simetrie, se adaugă acolo același `terraform_data.launch_guard`.
- Comenzile țintite pe VPN (`vpn-on`) nu trec prin gardul din Terraform, pentru că `-target`
  scoate resursa-gard din plan; refuzul vine de la twinctl.
- Comutatorul nu oprește ce rulează deja. Pentru asta: `make sim-vpn-off`, `make sim-down`,
  `make sim-data-down`.
- `terraform plan` pe cele două stack-uri gardate cere `launch_confirmed=true`; un plan manual
  se rulează cu `-var launch_confirmed=true`.
