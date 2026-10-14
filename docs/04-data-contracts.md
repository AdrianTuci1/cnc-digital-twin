# Contracte de date

Patru contracte, fiecare cu un fișier în `telemetry/schemas/` și cu un test care verifică
acordul cu codul. Schimbarea unui contract cere un bump de versiune în toate locurile în care
apare.

| Contract | Fișier | Cine publică | Cine consumă |
| --- | --- | --- | --- |
| `cnc.telemetry.v1` | `cnc.telemetry.v1.json` | Extensia Isaac Sim, 10 Hz | IoT Core, Firehose, Lambda |
| `cnc.telemetry.enriched.v1` | `cnc.telemetry.enriched.v1.json` | Lambda de feature engineering, 60 s | Firehose (Parquet), Athena, Data Wrangler |
| `cnc.qa.snapshot.v1` | `cnc.qa.snapshot.v1.json` | Extensia, la inspecție | Lambda de inspecție vizuală |
| `cnc.control.v1` | `cnc.control.v1.json` | Dispecerul de comenzi, Lambda de inspecție | Simularea |

## De ce contractele sunt stricte

Firehose convertește JSON în Parquet folosind schema din registrul Glue. Dacă o înregistrare
are un câmp care nu apare în schemă, conversia eșuează, iar Firehose scrie înregistrarea în
prefixul de erori. Un contract strict este mai ieftin decât unul permisiv.

Trei verificări automate țin contractele sincronizate:

1. `tests/unit/test_cncdt_ml.py::test_glue_table_matches_contract` compară coloanele din cod cu
   tabelul din Terraform.
2. `tests/unit/test_cncdt_ml.py::test_enriched_schema_matches_the_contract_exactly` compară
   schema JSON cu `cncdt_ml.schema.ENRICHED_COLUMNS`.
3. `tests/unit/test_feature_engineering_lambda.py` verifică faptul că răspunsul Firehose
   conține doar coloanele din contract.

## Câmpuri care sunt eliminate înainte de scriere

Simularea publică mai multe valori decât încap în tabel: `spindle_load_max`, `vibration_crest`,
`temp_c_max`, `joint_effort_*`, `axis_feed_*`. Sunt utile pentru diagnostic local, dar nu fac
parte din contract. `cncdt_ml.schema.conform` le elimină, iar
`test_conform_drops_unknown_fields_and_keeps_order` verifică lista exactă.

Dacă un câmp nou publicat de simulare trebuie să ajungă în tabel, se adaugă în trei locuri
în același commit: `ENRICHED_COLUMNS`, tabelul din Terraform și schema JSON.

## Ferestre

Agregarea se face pe ferestre aliniate la epoch, cu durata `window_s` (implicit 60 s):

```text
cheie fereastră = ts_ms // (window_s * 1000)
```

Alinierea la epoch face ferestrele comparabile între dispozitive și între rulări. Formula
este implementată în `cncdt_ml.windows.window_key` și testată pe granițe (`59_999` și `60_000`).

Gruparea include dispozitivul și celula: `(device_id, cell_id, cheie fereastră)`. Un singur
delivery stream poate primi date de la mai multe celule, iar agregarea între ele ar produce
caracteristici fără sens.

## Partiții

Firehose extrage cheile de partiționare din metadatele răspunsului Lambda, nu din coloanele
înregistrării:

```text
raw/telemetry/dt_year=!{partitionKeyFromQuery:dt_year}/dt_month=.../dt_day=.../dt_hour=.../
```

Cheile sunt șiruri (cerință Firehose) și sunt declarate `integer` în proiecția Athena, ca
interogările să poată filtra pe intervale fără `MSCK REPAIR`. Tabelul are
`storage.location.template`, deci Athena știe unde să caute.

## Versiuni

`schema_version` este scris în fiecare înregistrare. Regula:

- se schimbă formatul unui câmp existent → versiune nouă majoră, tabel nou, perioadă de
  tranziție cu ambele tabele;
- se adaugă un câmp → versiune minoră, se actualizează contractul, tabelul și testele;
- se elimină un câmp → versiune majoră; consumatorii trebuie să ignore câmpuri necunoscute
  (`additionalProperties: false` este intenționat pentru contractul îmbogățit, ca erorile să
  apară la testare, nu la scriere).

## Contractul de cadru QA

`cnc.qa.snapshot.v1` descrie sidecar-ul scris de extensie lângă fiecare fotografie. Din
versiunea **1.1.0** are câmpul obligatoriu `camera_role`, cu două valori:

| `camera_role` | Camera | Când |
| --- | --- | --- |
| `sorting` | `/World/QA/DiverterCamera` | la lamă, pe piesa oprită în poartă; cadrul decide ruta |
| `inspection` | `/World/QA/Camera` | la ieșirea din mașină; folosită când lipsește camera de sortare |

Sidecar-ul este cel care declanșează Lambda-ul de inspecție (regula S3 filtrează `.json`).
Fotografia stă lângă el, sub `raw/qa-snapshots/<cycle_id>/`, iar Lambda-ul o citește din S3 și
o trimite endpoint-ului **inline, în base64**, împreună cu vectorul de caracteristici, într-un
singur apel `InvokeEndpoint`. Politica IAM acoperă `s3:GetObject` pe `raw/qa-snapshots/*`,
deci citirea imaginii nu cere permisiuni suplimentare.

Constrângerea care contează: `InvokeEndpoint` acceptă 6 MB, iar base64 umflă datele cu 4/3,
deci Lambda-ul sare peste cadrele mai mari de 4 MB (`IMAGE_PAYLOAD_LIMIT_BYTES`) și continuă cu
vectorul de caracteristici. `SEND_IMAGE_TO_ENDPOINT=false` permite un endpoint care primește
doar caracteristici. Detalii: [09-scena-celulei-cnc.md](09-scena-celulei-cnc.md) și
[../lambdas/qa_vision_inference/README.md](../lambdas/qa_vision_inference/README.md).

## Contractul de control

Mesajele de control au două forme, cu prioritate diferită:

- `verdict` (inspecție) — închide așteptarea porții de la lamă și aplică ruta piesei:
  `PASS`, `REJECT`, `FAIL`, `DEFECT`;
- `command` (operator) — `inject_tool_wear`, `inject_joint_friction`, `force_defect`,
  `replace_tool`, `repair_joints`, `set_defect_probability`, `pause`, `resume`,
  `start_cycle`, `stop_cycle`, `reset`.

Un mesaj care are și `verdict` și `command` este tratat ca verdict: verdictul este decizia
autoritativă. Comenzile ajung la simulare prin SQS (reîncercări, coadă de mesaje moarte), nu
direct prin IoT, ca o pană de rețea să nu piardă o comandă de operator.
