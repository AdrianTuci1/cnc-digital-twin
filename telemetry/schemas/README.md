# Contracte de date

Scheme JSON, câte una per contract. Descrierea de ansamblu este în
[`docs/04-data-contracts.md`](../../docs/04-data-contracts.md).

| Fișier | Contract |
| --- | --- |
| `cnc.telemetry.v1.json` | Eșantion brut publicat de celula CNC la ~10 Hz |
| `cnc.telemetry.enriched.v1.json` | Fereastra agregată; **exact** coloanele tabelului din registrul de schemă |
| `cnc.qa.snapshot.v1.json` | Metadatele cadrului de inspecție (`image_captured` poate fi fals) |
| `cnc.control.v1.json` | Comenzi de operator și verdicte de inspecție |

Reguli:

- `additionalProperties: false` la contractele scrise de pipeline (`telemetry.v1`,
  `telemetry.enriched.v1`): un câmp în plus face conversia Parquet din Firehose să eșueze, deci
  vrem să aflăm la testare.
- `additionalProperties: true` la metadatele de inspecție: câmpurile descriptive nu afectează
  pipeline-ul.
- Schema îmbogățită este verificată în teste contra `cncdt_ml.schema.ENRICHED_COLUMNS` și
  contra tabelului din Terraform. Nu o editați singură.

Nu există un validator adăugat ca dependență: testele verifică structura și acordul cu codul.
Pentru validare completă `jsonschema`, se poate folosi `pip install jsonschema` și
`jsonschema -i exemplu.json cnc.telemetry.v1.json`.
