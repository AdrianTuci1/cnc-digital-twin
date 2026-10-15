# ADR 0016: Notebook-urile de antrenare ajung în repo prin S3

- **Status:** acceptat
- **Data:** 2026-10-15
- **Decizat de:** echipa de proiect
- **Pornește de la:** ADR 0003 (logica stă în cod, nu în notebook), ADR 0006 (fără chei statice),
  ADR 0015 (colectarea lungă iese din calculatorul de birou)

## Context

Notebook-urile de explorare și de antrenare se scriu în SageMaker Studio, adică în home-ul EFS al
domeniului: un director de utilizator, fără depozit git în el. Ele sunt totuși cod care merită
istoric, diff și review, iar cifrele din ele ajung în cardurile de model din `ml/models/`.

Trei fapte despre mediu, verificate în cod:

1. **Domeniul se creează în VPC-ul implicit**, deci spațiul are ieșire în internet și poate vorbi
   cu S3 și cu orice remote git.
2. **Rolul de execuție al Studio are deja `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`,
   `s3:ListBucket`, `s3:AbortMultipartUpload`** pe bucket-ul data lake din stack-ul `platform`
   (`modules/sagemaker-studio`). Orice cale prin acel bucket nu cere permisiuni noi.
3. **Home-ul EFS este păstrat la `destroy`** (`retention_policy.home_efs_file_system = "Retain"`)
   și copiat zilnic de AWS Backup, în seiful cu retenție de 30 de zile în `dev`.

## Decizie

1. **Sursa de adevăr este repo-ul**, în `ml/experiments/notebooks/`. Home-ul EFS rămâne locul
   unde notebook-ul rulează, nu locul unde trăiește.
2. **Ieșirea din Studio se face prin S3.** Din terminalul spațiului:
   `aws s3 sync ~/cnc-notebooks s3://<NOTEBOOKS_BUCKET>/notebooks/studio/`. Fără token git în
   cloud, fără remote configurat în spațiu.
3. **Bucket-ul este data lake-ul platformei**, singurul pe care rolul de Studio îl acoperă deja.
   Bucket-ul corpusului (`training-data`) rămâne al colectorului Modal, cu ciclul lui de viață.
4. **Commit-ul se face pe laptop**, cu credențialele rolului de operator: `make nb-pull`,
   `make nb-strip`, `git add ml/experiments/notebooks`. `make nb-push` merge în sens invers,
   pentru un notebook scris în repo pe care vrei să îl deschizi în Studio.
5. **Output-urile nu se comit.** `make nb-strip` le golește (`make nb-check` verifică), iar
   notebook-ul rulat rămâne artefact: în S3, ori în `ml/artifacts/`, ignorat de git.
6. **Patru straturi de protecție, fiecare cu rolul lui:** EFS `Retain` (domeniul se poate
   recrea), backup zilnic AWS Backup (o ștergere accidentală), sincronizarea în S3 (versiunea de
   acum, citită în câteva secunde), git (istoric și review, fără dependență de AWS). Procedura
   completă stă în `ops/runbooks/notebooks.md`.

## Consecințe

- **Nicio resursă nouă, nicio permisiune nouă.** Prefixul `notebooks/studio/` intră în lista de
  prefixuri documentată în `modules/platform-storage`, iar sync-ul folosește politica existentă a
  rolului de Studio.
- **Calea devine disponibilă odată cu aplicarea stack-ului `platform`**, adică exact atunci când
  există Studio. Nu se poate sincroniza mai devreme, fiindcă nici notebook-ul nu poate rula mai
  devreme.
- **Bucket-ul este versionat**, deci o sincronizare care suprascrie un notebook păstrează
  versiunea dinainte până la expirarea versiunilor vechi.
- **Un notebook nemarcat nu ajunge în repo.** Regula practică din runbook: ce merită păstrat
  trece prin S3 în repo în aceeași zi, iar backup-ul EFS rămâne plasa de siguranță pentru rest.
- **`make nb-strip` este obligatoriu înainte de commit**, iar `make test` prinde regresia de
  format prin testele din `tests/unit/test_nb_strip.py`.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Clone git în spațiul Studio, printr-un lifecycle config la pornirea aplicației | Cere un token de git în cloud (Secrets Manager ori PAT), are limita de cinci minute a lifecycle config-ului și lasă două copii editabile ale aceluiași fișier, fără o regulă clară de care câștigă. |
| CodeCommit ori alt remote intermediar, sincronizat cu repo-ul | Un al doilea remote, o a doua identitate și un al doilea flux de review, pentru un beneficiu pe care S3 îl dă deja din politica existentă. |
| Doar backup-ul EFS, fără drum în repo | Restaurarea cere un EFS nou, o instanță EC2 și `rsync`; nu dă istoric, nici diff, nici review, iar notebook-urile nu ajung niciodată lângă codul pe care îl descriu. |
| Notebook-urile în bucket-ul corpusului | Amestecă artefacte de explorare cu corpusul care intră în antrenare, iar rolul de Studio nu are scriere acolo. |
| Output-urile comise, ca dovadă a rulării | Diff ilizibil și git umflat cu base64; dovada rulării este notebook-ul salvat ca artefact în S3, ori cifrele din cardul modelului. |
