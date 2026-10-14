# ADR 0011: Volum EBS persistent pentru starea instanței de simulare

- **Status:** acceptat
- **Data:** 2026-10-05
- **Decis de:** echipa de proiect
- **Pornește de la:** ADR 0010 (instanța de simulare pe piața spot)

## Context

Instanța rulează pe spot cu cerere `one-time` (ADR 0010), deci discul rădăcină dispare la
fiecare întrerupere, la fiecare `make sim-down` și la fiecare recreare. Ce era pe el se reface
din AMI-ul golden sau din S3, ceea ce acoperă instalarea, dar nu și ce se acumulează în timpul
lucrului: oglinda de conținut Isaac Sim (~10 GB, descărcată în ~25 de minute), cache-ul de
shadere și de compilare Kit, modelele exportate, `simulation/` sincronizat cu `make sim-push`
și snapshot-urile intermediare.

Două observații au decis forma soluției:

1. **Oglinda de conținut se plătește în timp, nu în bani.** Se reia la fiecare instanță nouă,
   ceea ce consumă din sesiune și din trafic, fără să producă nimic durabil.
2. **Un volum EBS atașat supraviețuiește unei terminări.** La terminarea unei instanțe,
   `delete_on_termination` al unui volum atașat este ignorat de AWS, deci datele rămân pe
   volum. Riscul nu vine de la AWS, ci de la `terraform destroy`, care ar șterge orice resursă
   aflată în state-ul stack-ului `sim`.

## Decizie

Starea instanței se mută pe un volum EBS gp3 persistent, într-un **stack separat, `sim-data`,
cu state local propriu**, iar instanța îl primește prin `aws_volume_attachment` și îl montează
la `/opt/cncdt`.

Elementele deciziei:

- **Stack separat.** `sim-data` are propriul state, deci `make sim-down` (care distruge
  stack-ul `sim`) nu poate șterge volumul. Ștergerea se face exclusiv cu `make sim-data-down`,
  care rulează `terraform destroy` în `sim-data`. Astfel, singura cale de a pierde datele este
  o comandă explicită care spune ce șterge.
- **Zona se fixează o dată.** Un volum EBS aparține unei singure zone de disponibilitate.
  La prima creare, `twinctl` alege zona cu cea mai ieftină capacitate spot pentru
  `SIM_INSTANCE_TYPE`, o scrie în `SIM_DATA_VOLUME_AZ` și o pasează și stack-ului `sim`, ca
  instanța să pornească exact în zona volumului. Dacă volumul există deja, zona lui câștigă.
- **Montare la `/opt/cncdt`, în două forme.** `user_data` montează volumul înainte de orice
  scriere în directorul de stare, îl formatează o singură dată (ext4, `-m 0`, eticheta
  `cncdt-data`), iar la primul boot copiază în el conținutul din AMI (oglinda Isaac Sim), ca să
  nu îl descarce din nou. Volumul are două forme, după snapshot-ul din care pornește:
  **(a)** un snapshot al volumului de date, etichetat `cncdt-<mediu>-sim-data-*`, are în
  rădăcină direct conținutul lui `/opt/cncdt`, deci se montează la `/opt/cncdt`; **(b)** un
  snapshot al unui disc rădăcină are în rădăcină un sistem de operare întreg, deci se montează
  la `/mnt/cncdt-persist`, iar `<volum>/opt/cncdt` se expune la `/opt/cncdt` printr-un bind
  mount. `user_data` distinge forma după prezența `<volum>/etc` și `<volum>/opt/cncdt`. În
  `/etc/fstab` intră liniile potrivite, cu `nofail`: dacă volumul lipsește, instanța pornește
  totuși, doar fără starea persistentă.
- **Pornirea din snapshot-ul discului rădăcină.** Volumul se creează inițial din
  `snap-06f17127618e7ae67`, discul rădăcină al instanței on-demand dinainte: 200 GB, Isaac Sim
  5.0 instalat, oglinda de conținut și patch-urile de content browser. Un volum nou nu poate fi
  mai mic decât snapshot-ul, deci `SIM_DATA_VOLUME_GB` este cel puțin 200; `twinctl` refuză în
  clar orice combinație mai mică și spune când snapshot-ul ales este un disc rădăcină. Reluarea
  automată rămâne limitată la snapshot-urile `sim-data-*`: un snapshot de disc rădăcină se cere
  explicit, prin `SIM_DATA_VOLUME_SNAPSHOT` sau `make sim-data-up SNAPSHOT=…`.
- **Reatașare după o retragere de capacitate.** Când AWS termină instanța spot, volumul se
  detașează automat și rămâne în stack-ul `sim-data`. Următorul `make sim-up` creează o
  instanță nouă în aceeași zonă; `twinctl` așteaptă scurt dacă volumul este încă `in-use` și
  spune explicit când instanța din state-ul `sim` a fost terminată, ca mesajul să nu pară o
  eroare de configurare. Același volum se poate atașa oricărei instanțe noi din zona lui.
- **Identificarea dispozitivului după ID-ul EBS.** Pe Nitro, `/dev/sdf` apare ca
  `/dev/nvmeXn1`, cu un nume care depinde de ordinea de atașare. `user_data` caută legătura din
  `/dev/disk/by-id` care conține ID-ul volumului (fără cratime) și așteaptă până la cinci
  minute, ca un boot lent să nu rateze atașarea.
- **Snapshot-uri doar manual, reluate automat la recreare.** `make sim-data-snapshot` creează un
  snapshot etichetat, așteaptă starea `completed` și, cu `KEEP=N`, le șterge pe cele mai vechi
  peste N. Nu există snapshot automat și nicio politică de ciclu de viață: cine vrea o copie
  rulează comanda. În schimb, dacă volumul lipsește (a fost șters cu `data-down`), `make sim-up`
  îl recreează și **reia automat cel mai nou snapshot**, ca ștergerea volumului să nu însemne
  pierderea stării. `SIM_DATA_VOLUME_SNAPSHOT` în `.env` sau `make sim-data-up SNAPSHOT=…`
  impune un snapshot anume, iar `make sim-data-up FRESH=1` cere explicit un volum gol.
- **Atașare/detașare curată pe on-demand, directă pe spot.** `stop_instance_before_detaching`
  este pornit doar când instanța nu e pe spot. Pe spot (`one-time`), oprirea ar termina
  instanța și așteptarea stării `stopped` ar dura degeaba; jurnalul ext4 de pe volum se reia la
  montarea următoare, iar datele rămân consistente.

## Consecințe

- **Cost nou permanent.** 200 GB gp3 în `eu-central-1` costă ≈19 USD/lună, plătit și când nu
  rulează nicio instanță. Este prețul pentru a nu re-descărca oglinda și pentru a nu pierde
  cache-ul între sesiuni. `make sim-data` și `make sim-cost` afișează suma.
- **`make sim-down` nu mai golește tot.** Instanța, discul rădăcină, security group-ul, cheia
  și VPN-ul se șterg; volumul de date rămâne și se reatașează la următorul `up`.
- **`make sim-data-down` cere volumul detașat.** Dacă volumul este atașat, comanda se oprește și
  trimite la `make sim-down`, pentru că AWS refuză ștergerea unui volum în uz. Implicit face
  întâi un snapshot, deci datele pot fi readuse: următoarea creare de volum, ori `make sim-up`,
  reia automat cel mai nou snapshot.
- **Un snapshot consumat nu se repetă în `.env`.** După o creare pornită dintr-un snapshot cerut
  explicit, `twinctl` golește `SIM_DATA_VOLUME_SNAPSHOT`, ca o recreare ulterioară să nu readucă
  din greșeală aceeași stare veche. Reluarea automată citește lista de snapshot-uri la fiecare
  creare, deci urmează întotdeauna cel mai nou.
- **Volumul este legat de o zonă și de o regiune.** Mutarea în altă zonă înseamnă snapshot,
  ștergere, recreare din snapshot în zona nouă. Snapshot-urile sunt regionale.
- **Dimensiunea se poate crește, nu scădea.** `user_data` rulează `resize2fs`, deci o creștere
  a `SIM_DATA_VOLUME_GB` se aplică la următorul boot. Micșorarea cere un volum nou dintr-un
  snapshot.

## Alternative respinse

- **Volum de date în stack-ul `sim`.** Ar fi fost mai simplu de scris, dar `make sim-down` ar
  fi șters datele odată cu instanța, adică exact comportamentul pe care îl eliminăm.
- **`persistent` spot cu `stop`.** Ar păstra discul rădăcină la întrerupere, dar AWS ar
  reporni singură instanța și ar rămâne o cerere deschisă după `terraform destroy`
  (respinse în ADR 0010).
- **Snapshot-uri automate prin Amazon Data Lifecycle Manager.** Ar adăuga o resursă și un
  program de facturare pentru o copie pe care o vrem rară și explicită.
- **Doar AMI golden, fără volum.** AMI-ul acoperă instalarea, nu și ce se acumulează între
  înghețări; iar fiecare `sim-freeze` scrie un snapshot nou al întregului disc.
- **AMI înregistrat din snapshot-ul discului rădăcină.** `aws ec2 register-image` ar face din
  `snap-06f17127618e7ae67` un AMI, iar `SIM_AMI_ID` ar indica spre el: instanța ar porni cu
  tot discul, inclusiv cu `/opt/cncdt`. Este mecanismul AWS firesc pentru un disc de sistem,
  dar AMI-ul este imutabil: fiecare schimbare de stare ar cere un snapshot nou al întregului
  disc, iar starea ar trăi în discul rădăcină, șters la terminare. Volumul ține starea
  mutabilă separată de AMI-ul golden, se atașează unei instanțe noi în secunde și se salvează
  doar când se cere.
- **EFS montat la `/opt/cncdt`.** Rezolvă partajarea și supraviețuiește zonelor, dar costă
  mult mai mult la 200 GB, iar latența nu se potrivește cu cache-ul de shadere și cu cache-ul
  Kit, care sunt scrise intens.
