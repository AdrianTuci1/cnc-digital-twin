# Notebook-urile de explorare și de antrenare

Aici intră notebook-urile care merită păstrate: explorarea datelor, comparația modelelor,
rapoartele care însoțesc o antrenare. Repo-ul este sursa de adevăr; home-ul EFS al Studio este
locul unde rulează.

## Notebook-uri disponibile

| Notebook | Rol | Intrare |
| --- | --- | --- |
| `pm-01-dataset.ipynb` | ferestre de 60 s, caracteristici offline, etichete | `simulation/mujoco/runs/` |
| `pm-02-rcf.ipynb` | baseline `IsolationForest`, evaluare pe ferestre | `ml/artifacts/pm/features.parquet` |
| `qa-01-dataset.ipynb` | inventar de cadre, clase, split pe cicluri | `simulation/mujoco/runs/` |
| `qa-02-vision.ipynb` | schiță de clasificator PASS/REJECT | `ml/artifacts/qa/qa-frames.json` |

## Etichete pe MuJoCo

`health_label` din `cncdt_ml.labels` este calibrat pe scara celulei Isaac Sim. În MuJoCo,
debitul mediu de coolant pe o fereastră este ~1,8 LPM fiindcă mașina stă mult în `IDLE`, iar
pragul generic de 3 LPM ar eticheta aproape orice fereastră ca `coolant`. `build_windows`
calculează `twin_label` din defectul majoritar al pieselor, uzura maximă a sculei, frecarea
maximă și temperatura maximă; coolantul rămâne în afara etichetei până la o calibrare pe
ferestre `CUTTING`. Diferența dintre cele două seturi de etichete apare explicit în
`pm-01-dataset.ipynb`.

Rularea de referință `run-20261015T172741` are 144 de ferestre, din care 19 sănătoase după
`twin_label`. Este un corpus de uzură, nu o bază normală: pentru un detector RCF real, colectează
întâi sesiuni sănătoase la `--rate 1`.

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
