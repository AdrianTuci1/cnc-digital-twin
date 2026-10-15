# Notebook-urile de explorare și de antrenare

Aici intră notebook-urile care merită păstrate: explorarea datelor, comparația modelelor,
rapoartele care însoțesc o antrenare. Repo-ul este sursa de adevăr; home-ul EFS al Studio este
locul unde rulează.

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
