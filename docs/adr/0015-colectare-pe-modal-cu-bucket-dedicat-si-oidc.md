# ADR 0015: Colectarea lungă pe Modal, cu bucket dedicat și federare OIDC

- **Status:** acceptat
- **Data:** 2026-10-15
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0006 (rol de operator, fără chei statice), ADR 0011 (stack separat
  pentru starea care supraviețuiește), ADR 0012 (geamănul MuJoCo), `docs/11` §4

## Context

Corpusul de antrenament cere **ore de viață de mașină**, nu zeci de cicluri. Măsurat: 5 familii
× 25 de cicluri în 284 s de ceas, adică ~30× real, cu 105 MB de senzori și zero cadre. Un corpus
de uzură respectabil trece de câteva ore simulat pe fiecare familie, iar pe un singur proces
local asta înseamnă o zi de ceas pe calculatorul de birou.

Trei probleme se văd imediat:

1. **Timpul.** Fizica rulează pe un singur fir, iar pe laptop nu se poate paraleliza decât pe
   câteva nuclee, cu zgomot și cu căldură.
2. **Datele.** Câteva gigaocteți urcați din birou înseamnă ore de uplink pe o conexiune de acasă,
   plus șansa ca laptop-ul să adoarmă la mijlocul rulării.
3. **Identitatea.** Containerul din cloud trebuie să scrie în S3. Calea evidentă, o cheie de
   acces de lungă durată într-un secret, este exact ce interzice ADR 0006: fără utilizatori IAM,
   fără chei statice.

Starea contului la data deciziei, verificată cu AWS CLI: există doar rolul de operator
(`cncdt-dev-operator`, `PowerUserAccess` plus politica inline a proiectului). Nu există niciun
bucket `cncdt-*`, iar stack-ul `platform` nu a fost aplicat niciodată (bucket-ul de state
lipsește, `envs/dev.backend.hcl` are încă `REPLACE_WITH_ACCOUNT_ID`). Niciun furnizor OIDC în
cont.

Modal este deja folosit de utilizator prin CLI (`modal`, profil `adrian-tucicovenco`, spațiul de
lucru `ac-AivVdXvaLAfxzsldx0wXCu`), iar containerele lui primesc automat un token de identitate
în `MODAL_IDENTITY_TOKEN`, semnat de `https://oidc.modal.com`. Containerul îl schimbă pe
credențiale temporare cu `sts:AssumeRoleWithWebIdentity`.

## Decizie

1. **Colectarea lungă rulează pe Modal**, în containere efemere, nu pe calculatorul de birou.
   Geamănul rămâne rulabil local pentru demonstrație și verificare; cloud-ul este pentru volum.
2. **Bucket dedicat corpusului, în stack propriu.** `stacks/training-data` (state local, ca
   `sim` și `sim-data`) creează `cncdt-<mediu>-training-data-<cont>`, cu versionare, criptare,
   blocare a accesului public și expirarea versiunilor vechi. Prefixul rămâne cel documentat,
   `training-data/mujoco/dt=<zi>/run-<UTC>/`, iar contractul de urcare rămâne
   `cncdt_mj/export.py` plus `publish.py`.
3. **Identitate federată, fără chei statice.** Furnizorul OIDC `oidc.modal.com` se creează în
   `stacks/access`, fiindcă doar identitatea de admin poate: gardul `DenyPermanentIdentities`
   refuză `iam:CreateOpenIDConnectProvider` în sesiunile de operator (verificat cu
   `aws iam simulate-principal-policy`). Rolul `cncdt-<mediu>-modal-collector` stă în stack-ul
   `training-data`, se poate crea din sesiunea de operator, iar trust-ul lui este legat de
   spațiul de lucru Modal (`oidc.modal.com:sub` = `modal:workspace_id:ac-...:*`) și de audiență
   (`aud = oidc.modal.com`). Politica lui acoperă doar bucket-ul corpusului: `PutObject`,
   `GetObject`, `AbortMultipartUpload` pe obiecte și `ListBucket` pe bucket.
4. **Secretul Modal nu conține chei.** Poartă `AWS_ROLE_ARN` și `AWS_REGION`; credențialele
   apar în container, temporare, la fiecare pornire.
5. **Prima rulare este pe senzori, fără cadre.** `run.py --no-frames` nu deschide niciun
   randator, deci containerul nu are nevoie de GPU și nici de un context grafic; modelul de uzură
   citește oricum senzori tabulari (regula din `docs/11` §4). Cadrele pentru clasificatorul de
   piese vin într-o rulare separată, pe un container cu GPU și `MUJOCO_GL=egl`.
6. **Gardul de lansare rămâne închis până la o confirmare explicită.** Bucket-ul se plătește pe
   volum stocat, iar corpusul crește la fiecare colectare, deci `stacks/training-data` are aceeași
   `precondition` ca `sim` și `sim-data` (`ops/runbooks/launch-guard.md`).

## Consecințe

- **Costul colectării iese din categoria „timp de om".** Modal facturează 0,04716 USD pe
  nucleu-oră și 0,008 USD pe GiB-oră; un container cu 8 nuclee care ține 8 sesiuni în paralel
  duce un corpus de 2500 de cicluri în ~12 minute, adică sub 0,10 USD. Rularea cu cadre, pe un
  T4 (0,59 USD/oră), rămâne sub 1 USD. În planul Starter, primii 30 USD de compute pe lună sunt
  incluși, iar ieșirea de rețea are 1 TiB gratuit pe lună.
- **Corpusul nu trece prin calculatorul nimănui.** Containerul scrie direct în S3, în aceeași
  regiune (`eu-central-1`) în care va citi SageMaker, deci nici urcarea, nici citirea nu
  plătesc trafic de internet.
- **Două aplicări, cu ordine impusă:** `make access-up` (admin, o dată: rol de operator plus
  furnizorul OIDC) și apoi `make training-data-up` (operator: bucket plus rol Modal). Ordinea se
  vede și în `terraform.tfvars.example`, fiindcă trust-ul rolului se sprijină pe furnizor.
- **State-ul rămâne local**, deci numele bucket-ului se citește cu `make training-data-status` și
  se pune în `.env` (`DATA_LAKE_BUCKET`). Manifestul corpusului din S3 rămâne sursa de adevăr
  pentru ce conține o rulare.
- **`make mj-collect` local rămâne calea scurtă** (câteva cicluri, ca să vezi fișierele și să
  verifici planul de urcare), iar rularea lungă devine o comandă Modal.
- **`make audit` vede și stack-ul nou**: plan de derivă pe `training-data`, cu gardul deschis
  explicit, ca la `sim` și `sim-data`.
- Rolul Modal are sesiunea maximă de o oră, iar containerul își reia credențialele la nevoie;
  politica lui nu acoperă nimic din afara bucket-ului corpusului.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Chei de acces de lungă durată într-un secret Modal | Încalcă ADR 0006 și regula 8: identitate permanentă, fără expirare, care supraviețuiește oricărei revocări de sesiune. |
| Rularea lungă pe laptop, cu urcare prin `aws s3 cp` | Ore de ceas pe un singur proces, câteva gigaocteți pe uplink de acasă, plus riscul unei rulări întrerupte la mijloc. |
| Aplicarea stack-ului `platform` acum, ca să existe data lake-ul | Aduce IoT Core, Firehose, trei Lambda-uri, domeniul Studio, backup și buget înainte de primul corpus; costuri recurente și o zi de lucru pentru un bucket. |
| Cadre din prima rulare | Randarea cere GPU (ori randare software lentă) și un context grafic, iar modelul de uzură nu se uită la poze. |
| Furnizorul OIDC creat din sesiunea de operator | Refuzat explicit de gard; slăbirea gardului ar permite oricărei sesiuni de operator să federateze identități externe. |
| Feature store gestionat pentru caracteristici | Ar adăuga o a doua sursă de adevăr față de `cncdt_ml`; la acest volum, Parquet în S3 plus codul comun ajung (ADR 0002, ADR 0003). |
