# ADR 0010: Instanța de simulare pe piața spot, fără repornire automată

- **Status:** acceptat
- **Data:** 2026-10-05
- **Decis de:** echipa de proiect
- **Pornește de la:** ADR 0001 (Terraform ca uneltă de infrastructură)

## Context

Instanța GPU este singura resursă scumpă care rulează în `dev` și se pornește doar când cineva
lucrează în Isaac Sim. La 2026-10-05, `g5.2xlarge` costa în `eu-central-1` 1,5156 USD/oră
on-demand, iar istoricul de prețuri spot arăta 0,7436 (1b), 0,7845 (1c) și 0,9002 (1a)
USD/oră: aproximativ jumătate. Diferența se plătește pe fiecare sesiune, iar pentru sesiuni de
câteva ore pe lună este cea mai mare reducere disponibilă fără să schimbe nimic altceva.

Munca nu depinde de instanța respectivă: instalarea se reface din AMI-ul golden
(`make sim-freeze`), asset-urile stau în S3, telemetria ajunge în data lake, iar discul
rădăcină este efemer prin construcție. Deci o întrerupere nu pierde nimic care să nu se poată
reface sau reîncărca.

Două constrângeri au decis forma implementării:

1. **Nimeni nu pornește instanța în locul nostru.** Un `persistent` spot request cu
   `instance_interruption_behavior = "stop"` păstrează discul la întrerupere, dar AWS
   repornește singură instanța când reapare capacitatea, fără ca cineva să fi cerut asta; dacă
   capacitatea nu reapare imediat, instanța stă oprită cu discul plătit, iar dacă reapare în
   cinci minute, sesiunea continuă fără să știe nimeni de ce. Pornirea trebuie să fie o
   decizie explicită.
2. **Nicio resursă-fantomă.** Proiectul nu are voie să lase în urmă resurse care facturează
   fără să apară în Terraform.

## Decizie

Instanța de simulare se cumpără de pe piața spot cu o cerere `one-time` și terminare la
întrerupere. Variabila `use_spot` (în `stacks/sim`, scrisă din `SIM_USE_SPOT`) este implicit
`true`, iar modulul o traduce în:

```hcl
instance_market_options {
  market_type = "spot"

  spot_options {
    spot_instance_type             = "one-time"
    instance_interruption_behavior = "terminate"
  }
}
```

La o întrerupere AWS anunță cu două minute, apoi instanța și discul se termină. Revenirea este
`make sim-up`, care pornește din AMI-ul golden (Isaac Sim deja instalat) și reia sincronizarea
oglinzii de conținut în fundal. Nimic nu se pornește singur.

Cererea `one-time` se închide când instanța se termină, deci `terraform destroy` rămâne
complet: nu e nevoie de anularea unei cereri deschise și nu poate apărea o instanță-fantomă.
Asta elimină și dependența de AWS CLI la ștergere, care ar fi fost necesară pentru o cerere
`persistent`.

Oprirea rămâne posibilă doar pe on-demand. AWS acceptă oprirea exclusiv pentru cererile spot
`persistent`, iar cererea de aici este `one-time`, deci `twinctl stop` refuză pe spot și trimite
la `make sim-down` urmat de `make sim-up`: instanța se recreează, iar volumul de date persistent
(ADR 0011) se reatașează. Cu `SIM_USE_SPOT=false`, `stop` și `start` funcționează normal.

## Consecințe

- **Costul de calcul scade la aproximativ jumătate** pe fiecare sesiune. Pe o sesiune de patru
  ore, diferența este de circa 3 USD; pe zece sesiuni, circa 30 USD.
- **O întrerupere pierde discul**, ca un `down`: ce nu e în S3 sau într-un AMI se reface.
  `make sim-freeze` după fiecare instalare nouă ține AMI-ul la zi, iar `make sim-push` urcă
  codul simulării.
- **`twinctl cost` și `twinctl status` citesc prețul spot curent** din istoricul AWS, iar
  `status` afișează piața, ca o instanță on-demand rămasă dintr-o configurare veche să fie
  vizibilă.
- **La `up` poate lipsi capacitatea spot** (`InsufficientInstanceCapacity`). `twinctl up`
  recunoaște eroarea și sugerează o reîncercare sau alt tip de instanță prin
  `SIM_INSTANCE_TYPE`; `make sim-spot` arată prețul pe zone și scorul de capacitate înainte de
  lansare. On-demand nu are această problemă.
- **`SIM_USE_SPOT=false` readuce on-demand** fără alte schimbări: același modul, fără blocul de
  piață.

## Alternative respinse

- **`persistent` cu `stop`.** Păstrează discul și face oprirea/pornirea să rămână pe spot, dar
  AWS repornește singură instanța când reapare capacitatea, iar o cerere `persistent` rămâne
  deschisă după ce instanța dispare: `terraform destroy` ar șterge doar instanța, iar AWS ar
  relansa imediat una nouă, pe care Terraform nu o știe. S-ar putea acoperi cu un provisioner
  de distrugere care anulează cererea, dar rămâne un mecanism în plus pentru un comportament
  pe care nu îl vrem oricum.
- **On-demand.** Dublu la preț, fără niciun câștig: instanța este efemeră și întreruptibilă
  prin construcție.
- **Fleet sau Auto Scaling Group cu politică mixtă.** Rezolvă cazuri în care capacitatea
  trebuie apărată la scară; pentru o singură instanță interactivă adaugă un strat de
  configurare și de debugging fără beneficiu.
- **`aws_spot_instance_request`.** AWS descurajează API-ul legacy folosit de acest resource,
  iar etichetele lui nu ajung pe instanță, deci s-ar rupe regula de etichetare și alocarea
  costurilor pe `Project`.
