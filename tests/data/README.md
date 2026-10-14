# Date de probă

`telemetry.jsonl` conține 48 de ferestre îmbogățite, în formatul contractului
(`cnc.telemetry.enriched.v1`). Sunt generate sintetic, cu două cicluri de viață a sculei: uzura
crește liniar, la mijloc scula este înlocuită, iar două piese sunt marcate ca defecte forțate.

Scopul este să existe o cale de rulare completă fără AWS:

```bash
make features-build INPUT=tests/data/telemetry.jsonl OUTPUT=/tmp/features.jsonl FORMAT=jsonl
make e2e
python3 -m pytest tests/unit -q
```

Fișierul este produs de un script determinist (seed fix), deci valorile nu se schimbă între
rulări. Dacă se modifică `cncdt_ml/schema.py`, datele trebuie regenerate; testul
`test_feature_engineering.py` verifică doar prezența coloanelor derivate, nu și valorile.

Nu sunt date reale de la un client și nu trebuie folosite pentru a trage concluzii despre
performanța modelelor.
