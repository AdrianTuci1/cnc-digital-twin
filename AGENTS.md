# Conventions & orientare pentru contribuitori (inclusiv agenți AI)

Acest document descrie regulile de lucru în repository. Citiți-l înainte de a modifica cod.

## Limba și stilul

- Documentația, comentariile de design și mesajele de commit: **română**.
- Identificatorii din cod (variabile, funcții, resurse Terraform, topicuri): **engleză**.
- Commit-uri: [Conventional Commits](https://www.conventionalcommits.org/) — `feat:`, `fix:`,
  `infra:`, `docs:`, `chore:`, `test:`. Subiectul în română, imperativ, maximum 72 de caractere.
- Fără secreturi în cod. Cheile, `.env`, certificatele și fișierele de state sunt ignorate de git.

## Harta codului

| Unde | Ce se schimbă acolo |
| --- | --- |
| `infra/terraform/modules/<nume>` | Un modul reutilizabil: resurse + `variables.tf` + `outputs.tf`. Fără valori hardcodate de mediu. |
| `infra/terraform/stacks/<nume>` | Compoziție: cheamă module, fixează valorile de mediu. Aici se adaugă resurse noi, nu în module. `access` și `sim` au state local, `platform` are backend S3. |
| `simulation/extensions/` | Extensia Isaac Sim (Python, Kit). Se validează doar rulând în Isaac Sim. |
| `simulation/extensions/cncdt.cnc_cell/cncdt/cnc_cell/parts/` | Generatorul procedural de piese și manifestul de etichete. Modul pur (numpy + mapbox-earcut), testat fără Isaac Sim. |
| `simulation/mujoco/` | Al doilea geamăn al celulei, pe MuJoCo (ADR 0012). Venv propriu, ignorat de git: `make mj-venv`; scena se validează cu `make mj-check`, testele care ating motorul cu `make mj-test`. `layout.py`, `part.py`, `checks.py`, `telemetry.py`, `wear.py` și `export.py` rămân pure, ca `make test` să ruleze fără `mujoco`. Colectarea de antrenament (senzori + camere) se urcă în bucketul data lake cu `make mj-collect`, sub `datasets/mujoco/`. |
| `lambdas/<nume>/handler.py` | Punctul de intrare Lambda. Logica pură (fără boto3) stă într-un modul `*_core.py` cu nume unic (`core.py` este deja folosit de feature engineering), ca să fie testabilă. |
| `ml/src/cncdt_ml/` | Cod comun partajat de Lambda, feature engineering și antrenare. Contractul de date stă în `schema.py`. |
| `ml/feature_engineering/` | Data Wrangler: rețeta generată din cod, fluxurile exportate, `run_flow.py`, `build_features.py`. |
| `ml/training/<model>/train.py` | Script-mode SageMaker: citește `SM_*` din mediu, scrie în `/opt/ml/model`. |
| `ml/inference/<model>/inference.py` | Contract de inferență SageMaker: `model_fn`, `input_fn`, `predict_fn`, `output_fn`. |
| `telemetry/schemas/` | Contracte de date. Orice schimbare de schemă cere bump de versiune în `$schema`-ul topicului. |
| `tests/unit/` | Teste fără AWS: module pure din simulare, contract, Lambda, feature engineering. |
| `ops/runbooks/` | Proceduri operaționale. Se actualizează în același commit cu schimbarea care le invalidează. |
| `scripts/` | Scripturi locale: backend de state Terraform, `audit.sh` (inventar de resurse), `assume-operator.sh` (credențiale temporare), `simulation/scripts/generate-parts.py` (datasetul de piese). |
| `docs/` | Sursa de adevăr pentru decizii. Schimbările arhitecturale primesc un ADR nou în `docs/adr/`. |

## Reguli tehnice

1. **Infrastructură**: orice resursă AWS se creează prin Terraform. Consola se folosește doar
   pentru investigație. Resursele create manual se importă sau se șterg.
2. **Nume**: toate resursele primesc prefixul `${project_name}-${environment}` și eticheta
   `Project`, `Environment`, `ManagedBy=terraform`, `Owner`.
3. **Lambda**: fără dependențe grele; `boto3` vine din runtime. Logica de business în funcții
   pure, testate fără AWS. Fiecare funcție are `LOG_LEVEL` configurabil.
4. **ML**: antrenarea scrie metrice în SageMaker Experiments; artefactul final ajunge în S3 sub
   `models/<model-name>/<version>/`. Niciun model nu se promovează fără un card în
   `ml/models/<nume>.md` care descrie datele, metricele și limitele.
5. **Cost**: resursele efemere (instanță GPU, endpoint-uri) au cale explicită de oprire.
   Orice resursă nouă care costă peste 5 USD/zi în `dev` are nevoie de justificare în PR.
   Bugetul lunar (`modules/budget`) și `make audit` sunt plasa de siguranță pentru resturi.
6. **Securitate**: principiul minimului privilegiu în IAM. Fără `*` la `Resource` în politici noi.
   Bucket-urile S3 au `public_access_block`, criptare și versionare pornite.
7. **Observabilitate**: fiecare Lambda are log group cu retenție definită și cel puțin o alarmă
   CloudWatch dacă e pe hot path. Vizualizarea se face exclusiv în dashboard-uri custom
   CloudWatch, definite în `modules/observability`; nu se adaugă QuickSight sau alt instrument
   BI (ADR 0005).
8. **Identitate**: resursele se creează din sesiunea rolului de operator din `stacks/access`,
   asumată din identitatea de admin (ADR 0006). Nu se adaugă utilizatori IAM sau chei de acces
   în Terraform; rolul are garduri explicite care blochează crearea lor.

## Fluxul de lucru

```bash
git switch -c feat/<subiect>
make tf-fmt && make tf-validate
make test
# prima dată într-un cont: make access-up (cu identitatea de admin)
# apoi, în fiecare sesiune de lucru: eval "$(./scripts/assume-operator.sh)"
# pentru schimbări la geamănul MuJoCo: make mj-venv (o dată), apoi make mj-check și make mj-test
# pentru colectarea datelor de antrenament: make mj-collect-plan, apoi make mj-collect (cere rolul de operator)
# pentru schimbări de feature engineering: make features-verify
# pentru schimbări de UI/streaming: make sim-up && verificare în browser
# pentru interfața grafică a instanței: make sim-dcv OPEN=1 (https://localhost:8443/)
# pentru schimbări de infrastructură: make platform-plan, apoi make platform-up
# după orice sesiune cu resurse pornite: make audit
```

Verificarea în browser este obligatorie pentru orice schimbare care afectează interfața
clientului WebRTC, desktopul DCV, panoul de control din Isaac Sim sau dashboard-ul CloudWatch.

## Capcane cunoscute

- **Instanța de simulare rulează pe piața spot (ADR 0010).** Cererea Terraform este `one-time`
  cu terminare: la o întrerupere AWS anunță cu două minute, instanța și discul se termină, iar
  nimic nu se repornește singur — revenirea este `make sim-up`, care recreează instanța și
  reatașează volumul de date (ADR 0011). AWS acceptă oprirea doar pentru cererile spot
  `persistent`, deci `twinctl stop` refuză pe spot și trimite la `make sim-down` urmat de
  `make sim-up`. `SIM_USE_SPOT=false` readuce on-demand, unde `stop` și `start` funcționează;
  `make sim-spot` arată prețul pe zone și scorul de capacitate.
- **Volumul de date persistent are stack propriu (ADR 0011).** Starea de sub `/opt/cncdt`
  (oglinda de conținut, cache-ul, modelele, `simulation/`) stă pe un EBS din
  `infra/terraform/stacks/sim-data`, cu state local. `make sim-down` nu îl atinge; ștergerea
  se face exclusiv cu `make sim-data-down` (cere volumul detașat), iar snapshot-urile exclusiv
  cu `make sim-data-snapshot`. Volumul aparține unei singure zone: `twinctl` scrie
  `SIM_DATA_VOLUME_AZ` la creare și impune aceeași zonă instanței. Dacă volumul lipsește,
  `make sim-up` îl recreează și reia automat cel mai nou snapshot (deci ștergerea volumului nu
  pierde starea); `make sim-data-up FRESH=1` cere un volum gol. Volumul pornește din
  `snap-06f17127618e7ae67`, discul rădăcină al instanței on-demand dinainte (200 GB, Isaac Sim
  5.0, oglinda de conținut), deci `SIM_DATA_VOLUME_GB` este cel puțin cât snapshot-ul. Fiind un
  disc rădăcină întreg, `user_data` îl montează la `/mnt/cncdt-persist` și expune
  `<volum>/opt/cncdt` la `/opt/cncdt` prin bind mount; un snapshot al volumului de date, făcut
  cu `make sim-data-snapshot`, se montează direct la `/opt/cncdt`. Când AWS retrage capacitatea
  spot, instanța se termină, AWS detașează volumul, iar următorul `make sim-up` îl reatașează la
  instanța nouă; `twinctl` așteaptă scurt dacă volumul e încă `in-use` și spune explicit când
  instanța din state a fost terminată.
- **Glue este doar registru de schemă.** Nu adăugați job-uri, crawlere sau transformări Glue.
  Feature engineering-ul offline se face cu Data Wrangler (vezi `docs/adr/0003`), iar hot
  path-ul este Lambda-ul. Motivul: `docs/adr/0002`.
- **Corpusul de antrenament al geamănului are prefix propriu, `datasets/mujoco/`.** Nu se urcă
  sub `raw/telemetry/` (acolo e Parquet de ferestre, scris de Firehose și citit de tabelul Glue)
  și nici sub `raw/qa-snapshots/` (fiecare scriere acolo cheamă Lambda-ul de inspecție). Urcarea
  se face cu `make mj-collect`, prin **AWS CLI**, cu credențialele rolului de operator: `boto3`
  nu există în niciunul dintre cele două venv-uri, iar comanda nu adaugă o dependență. Verificarea
  corpusului dinaintea urcării stă în `simulation/mujoco/cncdt_mj/export.py` (pur).
- **Contractul de coloane este verificat în trei locuri**: tabelul din Terraform, schema JSON
  din `telemetry/schemas/` și `cncdt_ml/schema.py`. Firehose eșuează la conversia Parquet dacă
  înregistrarea are câmpuri în plus, deci cele trei se schimbă în același commit.
- **Straturile comune ale Lambda-ului își păstrează numele directorului** în arhivă
  (`_shared/`, `cncdt_ml/`), deci se importă ca `_shared.logging_utils`, nu ca modul de nivel
  superior. Două straturi cu același nume de director ar intra în conflict.
- **Modulele pure ale Lambda-urilor au nume unic** (`qa_vision_core.py`, `control_core.py`).
  Toate directoarele ajung pe același `sys.path` în teste, iar `core` este luat de feature
  engineering; un al doilea `core.py` ar fi importat greșit, fără eroare.
- **Porturile WebRTC se schimbă între versiunile Isaac Sim.** Nu hardcodați porturile în cod;
  citiți-le din `.env` și din `docs/02-isaac-sim-remote.md`.
- **AMI-ul: Marketplace-ul NVIDIA cere abonare per cont.** Implicit folosim AMI-ul Amazon Deep
  Learning Base OSS Nvidia Driver GPU (Ubuntu 22.04): drivere NVIDIA, Docker și container
  toolkit preinstalate, fără framework-uri de ML (Isaac Sim își aduce PyTorch în container).
  Detalii: `docs/02-isaac-sim-remote.md`.
- **AMI-ul de Deep Learning nu are server grafic.** Nu există GNOME, GDM3 sau Xorg, deci nici
  interfață grafică, nici sesiune DCV. Bootstrap-ul le instalează când `enable_dcv = true`
  (implicit): `ubuntu-desktop`, Wayland dezactivat în `/etc/gdm3/custom.conf`, `xorg.conf`
  generat cu `nvidia-xconfig --preserve-busid --enable-all-gpus --connected-monitor=DFP-0`
  (nu există monitor fizic), apoi pachetele Amazon DCV. Fără `--connected-monitor`, Xorg se
  oprește cu „no screens found", iar DCV rămâne fără sesiune de consolă.
- **Portul DCV (8443) nu se deschide public.** Se ajunge la el prin tunel, cu `make sim-dcv
  OPEN=1`, apoi `https://localhost:8443/`. Parola sesiunii este cea a utilizatorului `ubuntu`,
  generată la bootstrap și păstrată în SSM Parameter Store (`SecureString`,
  `/cncdt-<mediu>-sim/dcv-password`), niciodată în user_data sau în state.
- **DCV suportă doar X11.** Dacă GDM3 repornește pe Wayland, sesiunea de consolă nu se mai
  creează; verificarea rapidă este `pgrep -a Xorg` și `sudo dcv list-sessions`.
- **Licența DCV pe EC2 nu se cumpără, dar cere o permisiune.** Serverul își verifică licența în
  bucketul administrat de AWS `dcv-license.<regiune>`, deci rolul instanței are nevoie de
  `s3:GetObject` pe `arn:aws:s3:::dcv-license.<regiune>/*`. Fără el, `/var/log/dcv/server.log`
  scrie `Unable to retrieve license object ... Access Denied`, iar serverul rulează pe licența
  de evaluare de 30 de zile. Simptomul apare tăcut: sesiunile funcționează până expiră.
- **DCV și livestreaming-ul WebRTC sunt căi diferite.** DCV (8443, client web sau nativ) arată
  desktopul mașinii; clientul Isaac Sim WebRTC (49100 + UDP 47998) arată viewport-ul aplicației
  și nu are echivalent în browser în Isaac Sim 5.0. Nu se înlocuiesc unul pe altul.
- **Volumul rădăcină al instanței de simulare este EFEMER la `down`.** Asset-urile și
  snapshot-urile de valoare se urcă în S3.
- **State-ul Terraform al stack-ului `sim` este local.** Nu îl comitați și nu îl mutați între
  mașini fără `terraform state pull/push`.
- **Stack-ul `access` are, de asemenea, state local și se aplică cu profilul de admin.**
  Rolul de operator nu se poate modifica pe sine (ADR 0006), deci schimbările de trust,
  garduri sau durată de sesiune se fac exclusiv din identitatea de admin, prin
  `make access-up`. Nu mutați state-ul între mașini fără `terraform state pull/push`.
- **Providerul Terraform nu citește sesiunile `aws login` și nu poate trimite tokenul MFA.**
  Cheia `login_session` din profil este înțeleasă doar de AWS CLI, iar blocul `assume_role`
  al providerului nu are `mfa_serial`. Credențialele ajung la Terraform prin variabile de
  mediu: `eval "$(./scripts/assume-operator.sh)"` pentru resurse, iar `make access-plan/up/down`
  rezolvă singure credențialele de admin. Detalii: `ops/runbooks/access.md`.
- **Regiunile opt-in sunt `DISABLED` până le activezi în cont.** Apelurile într-o regiune
  dezactivată eșuează cu `InvalidClientTokenId` sau `AuthFailure`, care par probleme de
  credențiale. Verifică cu `aws account list-regions` înainte de a schimba `AWS_REGION`.
- **Lanțul de roluri este limitat de AWS la o oră.** Când sursa e o sesiune temporară
  (inclusiv `aws login`), `assume-operator.sh` reduce singur durata și anunță pe stderr.
  Durata cerută în `.env` se aplică integral doar cu o sursă permanentă.
- **IAM nu acceptă `ă`, `ș` sau `ț` în `description`.** Câmpul permite doar ASCII și Latin-1,
  deci `aws_iam_role.description` se scrie fără diacriticele din afara intervalului. Restul
  textelor (variabile, ieșiri, comentarii, etichete) păstrează diacriticele normale.
- **Rolul de operator nu poate crea utilizatori sau chei de acces.** Gardurile din
  `stacks/access` blochează `iam:CreateUser`, `iam:CreateAccessKey`, `iam:CreateLoginProfile`
  și modificările la nivel de cont sau organizație. Identități permanente se creează doar
  manual, din admin, și niciodată prin Terraform (regula 8).
- **Backend-ul Terraform al platformei este generat.** `envs/<mediu>.backend.hcl` este ignorat
  de git și îl scrie `scripts/tf-backend.sh` (rulat de `make bootstrap` / `make platform-up`).
  Nu îl editați manual, iar comanda de generare are nevoie de credențiale AWS.
- **EFS-ul de home al Studio supraviețuiește unui `terraform destroy`.** Domeniul are
  `retention_policy.home_efs_file_system = "Retain"`, deci notebook-urile nu se pierd, dar
  rămâne un EFS cu cost recurent. Domeniul recreat primește un EFS nou, iar cel vechi se
  remontează manual. Backup-ul din `modules/backup` este singura plasă de siguranță.
- **Seiful AWS Backup blochează `terraform destroy` când are puncte de restaurare.** În `dev`
  `studio_backup_force_destroy = true`; în `prod` punctele se șterg manual, conform
  `ops/runbooks/audit.md`.
- **Vizualizarea este exclusiv CloudWatch.** Fără QuickSight, Grafana sau alt instrument BI
  (ADR 0005). Ce trebuie urmărit recurent devine widget de dashboard sau alarmă în Terraform;
  analiza exploratorie se face cu Athena ori într-un notebook din Studio.
- **Aplicațiile Studio deschise blochează ștergerea domeniului.** Oprirea pe inactivitate le
  închide după `idle_timeout_in_minutes`; `make audit` le listează pe cele care încă rulează.
