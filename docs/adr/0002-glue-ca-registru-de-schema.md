# ADR 0002: Glue Data Catalog doar ca registru de schemă

- **Status:** acceptat
- **Data:** 2026-10-04
- **Decis de:** echipa de proiect

## Context

Firehose poate converti JSON în Parquet, dar are nevoie de o schemă: `data_format_conversion_configuration` cu `schema_configuration` care indică o bază de date și un tabel din
AWS Glue Data Catalog. Fără ea, datele ajung în S3 ca JSON necompactat și interogările Athena
devin lente și scumpe.

Există două moduri de a folosi Glue în acest proiect:

1. **Registru de schemă** — doar tabelul și baza de date, folosite de Firehose și Athena.
2. **Motor de transformare** — job-uri Glue, crawlere, `DynamicFrame`, scripturi Spark.

## Decizie

Folosim Glue exclusiv ca **registru de schemă**, varianta 1:

- `aws_glue_catalog_database` în modulul `platform-storage`, opțional (`enable_glue_catalog`);
- un singur tabel, `telemetry_enriched`, cu proiecție de partiții pe `dt_year`, `dt_month`,
  `dt_day`, `dt_hour` (fără crawler, fără `MSCK REPAIR`);
- politica IAM a domeniului Studio are doar acțiuni de citire (`GetTable`, `GetPartitions`),
  comentate explicit ca atare;
- nu există `aws_glue_job`, `aws_glue_crawler` sau transformări Glue în repository.

## Consecințe

- Schema este declarată o dată, în Terraform, iar conversia Parquet din Firehose o folosește
  prin `version_id = "LATEST"`.
- Ordinea coloanelor și lista lor sunt verificate de teste în trei locuri (Terraform, schema
  JSON, cod Python). Firehose eșuează dacă o înregistrare are câmpuri în plus, deci
  contractul este strict prin construcție.
- Transformările trăiesc în altă parte, cu responsabilități clare: Lambda pe hot path, Data
  Wrangler și `cncdt_ml/offline.py` offline (vezi ADR 0003).
- Când schema se schimbă, versiunea din `schema_version` crește, se actualizează tabelul din
  Terraform, schema JSON și `cncdt_ml/schema.py` în același commit.

## Alternative respinse

| Alternativă | Motivul respingerii |
| --- | --- |
| Fără Glue, doar JSON în S3 | Athena scanează mult mai mult pentru aceleași interogări; costul și timpul cresc. |
| Crawlere Glue | Pornesc pe program și modifică schema fără revizuire; schema ar deveni implicită, nu declarată. |
| Job-uri Glue pentru transformări | Respinse de echipă; feature engineering-ul se face cu Data Wrangler (ADR 0003). |
| Schema scrisă de mână în Firehose fără catalog | Nu există loc pentru `schema_configuration`; conversia Parquet nu este disponibilă. |
