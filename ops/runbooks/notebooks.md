# Runbook — notebook-urile din Studio: backup și drumul în repo

Notebook-urile de explorare și de antrenare se scriu în SageMaker Studio, adică în home-ul EFS al
domeniului, unde nu ajunge git. Procedura de aici le scoate de acolo prin S3 și le aduce în repo,
fără niciun token de git în cloud. Motivarea deciziei: ADR 0016.

## Ce apără fiecare strat

| Strat | Ce prinde | Ce nu prinde |
| --- | --- | --- |
| `retention_policy.home_efs_file_system = "Retain"` | Home-ul supraviețuiește unui `terraform destroy` al domeniului | Nimic din ce ai șters din home |
| Backup zilnic AWS Backup la 05:00 UTC (selecția `studio-home-efs`, retenție 30 de zile în `dev`, 365 în `prod`) | O ștergere accidentală, o editare din care ai vrut să te întorci | Restaurarea e o procedură, nu un click; vezi [audit.md](audit.md#restaurarea-notebook-urilor) |
| Sincronizarea în S3 (`notebooks/studio/`, bucket-ul data lake al platformei) | Versiunea de acum, citită în câteva secunde de pe laptop | Ce nu ai sincronizat |
| Git (`ml/experiments/notebooks/`) | Istoric, diff, review, și nu depinde de niciun serviciu AWS | Output-urile rulării (rămân artefacte) |

Regula practică: **orice notebook care merită păstrat trece prin S3 în repo în aceeași zi.**
Backup-ul rămâne plasa de siguranță pentru ce nu a trecut.

## Din Studio spre S3

În terminalul spațiului (File → New → Terminal), cu notebook-urile ținute într-un director al
home-ului, de pildă `~/cnc-notebooks`:

```bash
aws s3 sync ~/cnc-notebooks s3://<NOTEBOOKS_BUCKET>/notebooks/studio/
```

`NOTEBOOKS_BUCKET` este bucket-ul data lake al platformei, adică ieșirea `data_lake_bucket` a lui
`make platform-up`, pusă în `.env`. Bucket-ul corpusului (`training-data`, din `DATA_LAKE_BUCKET`)
rămâne separat: acolo scrie doar colectorul, iar artefactele de explorare nu se amestecă cu
datele care intră în antrenare.

Rolul de execuție al Studio are deja `s3:PutObject`, `s3:GetObject` și `s3:ListBucket` pe
bucket-ul data lake (`modules/sagemaker-studio`), deci nu e nevoie de nicio permisiune nouă.
Versiunile vechi ale obiectelor expiră după `noncurrent_version_expiration_days`, iar
bucket-ul e versionat: o sincronizare care suprascrie ceva nu pierde versiunea dinainte.

Cheile păstrează structura din home (`notebooks/studio/<subdirector>/<notebook>.ipynb`), deci un
`sync` fără `--delete` doar adaugă și actualizează.

## Din S3 în repo

Pe laptop, cu credențialele rolului de operator și cu `NOTEBOOKS_BUCKET` completat în `.env`:

```bash
eval "$(./scripts/assume-operator.sh)"
make nb-pull      # aduce notebook-urile în ml/experiments/notebooks/
make nb-strip     # golește output-urile
make nb-check     # opțional: spune dacă a rămas vreun notebook cu output-uri
git add ml/experiments/notebooks && git commit -m "docs: adaugă notebook-ul de explorare"
```

`make nb-push` merge în sens invers, pentru un notebook scris în repo pe care vrei să îl
deschizi în Studio.

## Convenții

- Notebook-urile stau în `ml/experiments/notebooks/`, pe subiecte: `pm-explorare.ipynb`,
  `qa-clasificare.ipynb`. Legătura dintre notebook și artefactul lui (rețeta, modelul, raportul)
  se scrie în prima celulă markdown.
- **Output-urile nu se comit.** `make nb-strip` le golește, iar notebook-ul rulat rămâne
  artefact: în S3, ori în `ml/artifacts/`, care e ignorat de git.
- **Logica nu stă în notebook.** Caracteristicile, ferestrele și antrenarea stau în
  `cncdt_ml` și în `ml/training/<model>/train.py`; notebook-ul explorează și documentează
  (ADR 0003). Un pas care merită repetat se mută în cod și primește test.
- `.ipynb_checkpoints/` este ignorat de git, deci salvările automate ale Jupyter nu ajung în repo.

## Când sincronizarea nu s-a făcut la timp

1. Verifică ce e în S3:
   `aws s3 ls s3://<NOTEBOOKS_BUCKET>/notebooks/studio/ --recursive --human-readable`.
2. Dacă notebook-ul nu e acolo, ultima versiune este în backup-ul EFS de la ora planificată:
   [audit.md](audit.md#restaurarea-notebook-urilor). Backup-ul nu înlocuiește home-ul curent, deci
   restaurarea cere un EFS nou, o instanță EC2 și `rsync`.
3. Alarma `backup-jobs-failed` anunță pe SNS când un job de copiere a picat, ca golul să nu treacă
   neobservat.
