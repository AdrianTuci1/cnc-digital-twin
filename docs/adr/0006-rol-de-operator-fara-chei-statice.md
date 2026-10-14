# ADR 0006: Rol de operator asumat, fără utilizator IAM nou și fără chei statice

- **Status:** acceptat
- **Data:** 2026-10-04
- **Decis de:** echipa de proiect

## Context

Terraform și `twinctl` rulează de pe mașina operatorului și trebuie să prezinte AWS
credențialele unei identități din cont. În cont există deja o identitate umană cu privilegii
de administrator, folosită pentru bootstrap.

Două cerințe au contat în alegere:

1. **Atribuire.** Vrem ca resursele proiectului să apară în CloudTrail ca fiind create de o
   identitate a proiectului, separată de persoana care le lansează.
2. **Igienă.** Fără credențiale de lungă durată care se pot scurge, plus posibilitatea de a
   revoca accesul printr-o singură acțiune.

Un rol IAM nu are cheie de acces proprie: se asumă, iar sesiunea rezultată are o cheie, un
secret și un token, valabile câteva ore. O pereche statică `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` aparține unui utilizator IAM.

## Decizie

1. **Nu se creează utilizatori IAM și nu se creează chei de acces statice.** Singurul rol nou
   este `${project_name}-${environment}-operator`, în `infra/terraform/stacks/access`.
2. **Asumarea se face din identitatea de admin existentă**, prin lanțul standard al AWS CLI și
   al SDK-ului: profilul `cncdt` din `~/.aws/config` are `role_arn` și `source_profile`.
   Credențialele temporare se cachează local de CLI.
3. **Trust-ul este restrâns la cont**, prin principalul `arn:<partiție>:iam::<cont>:root`, cu
   posibilitatea de a-l îngusta la un utilizator anume prin `trusted_principal_arns`. Rolul
   nu este asumabil din afara contului.
4. **Permisiunile de bază sunt `PowerUserAccess`**, plus o politică inline pentru acțiunile
   IAM de care are nevoie Terraform (creare de roluri de serviciu, politici inline,
   instance profiles și `iam:PassRole` limitat la rolurile proiectului).
5. **Garduri explicite** cât timp `enable_guardrails` este `true`: rolul nu poate crea
   identități permanente, nu își poate modifica propria politică, nu poate atinge dovezile de
   audit, nu poate face schimbări la nivel de cont și nu poate șterge bucket-ul de state.
6. **MFA opțional.** Implicit dezactivat, pentru că providerul Terraform nu poate trimite
   tokenul MFA (blocul `assume_role` nu are `mfa_serial`). Cu MFA activat, credențialele se
   obțin cu `scripts/assume-operator.sh` și se exportă înainte de `terraform apply`.
7. **State-ul stack-ului `access` este local**, ca al bootstrap-ului: se aplică o dată per
   cont, cu identitatea de admin, iar nimic din el nu ajunge în S3.

## Consecințe

- CloudTrail arată două straturi: `AssumeRole` de la identitatea umană și apoi acțiunile
  sesiunii de rol. Se vede cine a deschis sesiunea și ce a făcut sesiunea.
- Revocarea înseamnă `make access-down`. Sesiunile deja emise expiră în cel mult
  `max_session_duration_hours`.
- Rolul este lat: Terraform creează roluri și le pasează, deci o listă minimă de permisiuni ar
  fi iluzorie. Limita reală este trust-ul, gardurile și faptul că rolul nu poate produce
  credențiale permanente.
- Modificarea accesului cere identitatea de admin: rolul nu își poate schimba propria
  politică, ceea ce face imposibilă escaladarea din interiorul sesiunii.
- Costul suplimentar este zero; un rol IAM și o politică inline sunt gratuite.
- `make platform-up` și `make sim-up` se comportă identic, doar identitatea din spate diferă.

## Notă operațională (octombrie 2026)

Primele aplicări au arătat trei lucruri pe care decizia le presupunea, dar nu le spunea
explicit:

- **Sursa de credențiale.** Cu `aws login`, profilul primește o cheie `login_session` pe care
  doar AWS CLI o citește. Providerul Terraform nu o poate folosi, deci credențialele ajung la
  el prin variabile de mediu: `make access-*` le rezolvă singure, iar pentru restul stack-urilor
  sesiunea de rol se exportă cu `scripts/assume-operator.sh`.
- **Durata sesiunii.** Când sursa e temporară, asumarea este un lanț de roluri, iar AWS îl
  limitează la o oră. `assume-operator.sh` reduce durata automat; `OPERATOR_SESSION_HOURS` se
  aplică integral doar cu o sursă permanentă.
- **Regiunea.** Rolul IAM este global, dar stack-urile care creează servicii regionale au nevoie
  de o regiune activă în cont. Regiunile opt-in sunt `DISABLED` până le activezi, iar apelurile
  acolo eșuează cu erori care par de credențiale.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Utilizator IAM nou cu cheie de acces | Cere încă o identitate de administrat, iar cheia este de lungă durată: se scurge, se uită, trebuie rotită. Cerința de atribuire se rezolvă la fel de bine cu un rol. |
| Doar identitatea de admin, fără rol | Resursele apar în CloudTrail ca acțiuni ale persoanei, iar revocarea ar însemna să te atingi de identitatea umană folosită peste tot. |
| IAM Identity Center (SSO) | Cea mai bună variantă pe termen lung, dar cere activarea serviciului și configurare în afara Terraform; contul are deja o identitate de admin funcțională. Se poate adopta ulterior peste același rol. |
| Politici explicite, minim necesare, în loc de `PowerUserAccess` | `iam:CreateRole` și `iam:PassRole` fac oricum escaladarea posibilă; o listă lungă de permisiuni ar da o impresie falsă de limitare. |
| Cont AWS separat per mediu | Cel mai puternic perete de izolare, dar schimbă complet bootstrap-ul și nu este necesar pentru acest proiect. |
