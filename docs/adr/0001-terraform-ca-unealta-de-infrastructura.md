# ADR 0001: Terraform ca unealtă de infrastructură

- **Status:** acceptat
- **Data:** 2026-10-04
- **Decis de:** echipa de proiect

## Context

Proiectul trebuie să creeze și să șteargă resurse AWS în mai multe medii (`dev`, `staging`,
`prod`) și, separat, o instanță GPU efemeră pentru simulare. S-au luat în calcul trei variante:
CloudFormation, CDK și Terraform.

Cerințele care contează:

- ștergerea completă și repetabilă a instanței GPU, fără resurse orfane;
- două stack-uri cu ciclu de viață diferit (instanța de simulare se recreează des, platforma
  nu);
- același cod pentru toate mediile, cu valori diferite;
- verificare offline: `fmt`, `validate`, plan fără credențiale pentru părțile care nu cer AWS.

## Decizie

Folosim **Terraform**, cu module reutilizabile și două rădăcini:

- `infra/terraform/stacks/sim` — stack efemer, cu state local. Conține exclusiv instanța GPU,
  rețeaua ei, rolul și alarma de status. Se creează și se distruge des.
- `infra/terraform/stacks/platform` — stack de platformă, cu state în S3 (bucket creat de
  `infra/terraform/bootstrap`), câte un `tfvars` și un `backend.hcl` per mediu. Conține
  stocarea, ingestia, Lambda-urile, SageMaker Studio și endpoint-urile.

Resursele partajate stau în module sub `infra/terraform/modules/`, fără valori hardcodate de
mediu. Un stack nou adaugă resurse, nu cod în module.

## Consecințe

- `terraform validate` și `terraform fmt -check -recursive` rulează în CI fără credențiale
  (`init -backend=false`). `terraform plan` cere credențiale doar pentru stack-ul de platformă.
- Ștergerea instanței de simulare este o singură comandă (`twinctl down`), cu o singură
  sursă de adevăr pentru resursele ei.
- State-ul local al stack-ului `sim` nu se comite și nu se mută între mașini fără
  `terraform state pull/push`.
- Blocarea state-ului folosește `use_lockfile = true` (nativ), deci nu mai depinde de tabelul
  DynamoDB de blocare.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| CloudFormation | Sintaxă mai verbosă pentru aceeași topologie; verificarea offline este limitată, iar ștergerea stack-ului efemer este mai puțin flexibilă (fără `-target` simplu). |
| CDK | Adaugă un strat de sinteză și dependențe Node; pentru două stack-uri și module simple, câștigul nu justifică complexitatea. |
| Consola AWS | Nu este reproductibilă și nu poate fi revizuită în PR. Se folosește doar pentru investigație. |
