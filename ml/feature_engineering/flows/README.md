# Fluxuri Data Wrangler

Două tipuri de fișiere trăiesc aici, cu reguli diferite.

| Fișier | Cine îl scrie | Se editează manual? |
| --- | --- | --- |
| `predictive_maintenance.recipe.json` | `python3 build_features.py --print-recipe` | Nu. Se regenerează, iar testul verifică acordul cu codul. |
| `predictive_maintenance.flow` | SageMaker Studio, prin `Export flow` | Nu direct. Se modifică în Studio și se re-exportă. |
| `vision_qa.flow` | SageMaker Studio | Nu direct. Ramura de imagini, vezi mai jos. |

## Rețeta

Rețeta este lista de pași și lista exactă de coloane de ieșire, generată din
`cncdt_ml.offline.DEFAULT_SPEC` și din `recipe.py`. Are două roluri:

1. ghid pentru cine construiește fluxul în Studio — pașii sunt în ordinea de aplicare;
2. contract verificabil — testul `tests/unit/test_feature_engineering.py` compară fișierul
   comis cu cel generat acum. Dacă cineva schimbă rețeta în cod și uită să regenereze
   fișierul, testul cade.

Regenerare:

```bash
python3 ml/feature_engineering/build_features.py --print-recipe \
  > ml/feature_engineering/flows/predictive_maintenance.recipe.json
```

## Fluxul exportat

Un `.flow` este un graf JSON de noduri (surse, transformări, destinații) și muchii. Conținutul
depinde de versiunea Data Wrangler, de aceea nu este scris de mână: se creează în Studio, se
exportă și se comite.

Convenție de nume: `<model>.flow`, unde `<model>` este `predictive_maintenance` sau `vision_qa`.

Înainte de a comite un flux:

- verificați că destinația din pasul `Export to S3` este
  `s3://<datalake-bucket>/features/<model>/<versiune>/`;
- verificați că `recipe_version` din rețetă corespunde versiunii din `cncdt_ml/offline.py`;
- rulați `run_flow.py --dry-run` ca să vedeți cererea de job fără să porniți nimic.

## Ramura de imagini

Fluxul pentru `vision_qa` are altă sursă: `s3://<datalake-bucket>/raw/qa-snapshots/`, cu
fișiere `.json` și `.png` per piesă. Pașii sunt: citire metadate, îmbinare cu verdictul de
inspecție din `curated/qa-verdicts/`, derivarea etichetei binare `defect` / `ok`, redimensionare
și scriere în `s3://<datalake-bucket>/features/vision_qa/<versiune>/`.

Eticheta provine din verdictul cloud, nu din previziunea locală a simulării: modelul trebuie
să învețe din decizia autoritativă.
