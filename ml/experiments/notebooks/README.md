# Notebook-urile de explorare și de antrenare

Aici intră notebook-urile care merită păstrate: explorarea datelor, comparația modelelor,
rapoartele care însoțesc o antrenare. Repo-ul este sursa de adevăr; home-ul EFS al Studio este
locul unde rulează.

## Notebook-uri disponibile

Toate citesc direct din S3, cu rolul de execuție al Studio, și își descarcă singure pachetul de
cod din `s3://<data-lake>/notebooks/studio/code/cncdt-code.tgz` (reîmprospătat cu
`make studio-code`).

| Notebook | Rol | Intrare |
| --- | --- | --- |
| `pm-01-dataset.ipynb` | inventar de rulări, ferestre de 60 s, etichete | corpus `training-data/mujoco/` |
| `pm-02-rcf.ipynb` | RCF built-in: antrenare spot, batch transform, prag, ploturi | `features/predictive-maintenance/v1/windows.parquet` |
| `qa-01-dataset.ipynb` | inventar de piese, clase și verificarea scurgerii între splituri | `training-data/parts/` |
| `qa-02-vision.ipynb` | clasificator ResNet18 cu două capete (familie + PASS/REJECT) pe un singur GPU spot | indexurile `images-*.jsonl` ale rulării de piese |

`qa-01` doar confirmă datele; `qa-02` lansează un job de `ml.g5.xlarge` pe spot, cu limita de
o oră, și se oprește singur la final.

## Etichete pe MuJoCo

`health_label` din `cncdt_ml.labels` este calibrat pe scara celulei Isaac Sim. În MuJoCo,
debitul mediu de coolant pe o fereastră este ~1,8 LPM fiindcă mașina stă mult în `IDLE`, iar
pragul generic de 3 LPM ar eticheta aproape orice fereastră ca `coolant`. `build_windows`
calculează `twin_label` din defectul majoritar al pieselor, uzura maximă a sculei, frecarea
maximă și temperatura maximă; coolantul rămâne în afara etichetei până la o calibrare pe
ferestre `CUTTING`. Diferența dintre cele două seturi de etichete apare explicit în
`pm-01-dataset.ipynb`.

Corpusul are două rulări pe aceleași șase familii: `run-20261018T230709` (`wear.rate = 0`,
sănătoasă) și `pm-wear-20261024T212228` (rampă lentă, `rate 1`, 200 de cicluri). `pm-02-rcf.ipynb`
antrenează pe prima și testează pe a doua, ținută complet deoparte; rularea de test începe
sănătos și coboară treptat, deci se poate măsura și preavizul. Cifrele de detecție, de alarme
false pe oră și preavizul minim se scriu în `metrics.json` la fiecare antrenare; metricele rămân
preliminare până există mai multe rulări degradate pe fiecare familie.

Drumul complet, ambele direcții, este în [`ops/runbooks/notebooks.md`](../../../ops/runbooks/notebooks.md):

```bash
eval "$(./scripts/assume-operator.sh)"
make nb-pull      # aduce din S3 ce s-a sincronizat din Studio
make nb-strip     # golește output-urile, înainte de commit
```

Două reguli care țin repo-ul curat:

- **Output-urile nu se comit.** Un notebook cu output-uri face diff-uri ilizibile și umflă git cu
  base64. Notebook-ul rulat rămâne artefact, în S3 ori în `ml/artifacts/` (ignorat de git).
- **Logica nu stă în notebook.** Caracteristicile, ferestrele și antrenarea stau în `cncdt_ml` și
  în `ml/training/<model>/train.py`, iar ce se repetă primește test (ADR 0003). Notebook-ul
  explorează, documentează și produce cifrele care intră în cardul modelului.
