# Contractele de inferență

Aici stau adaptoarele dintre artefactele antrenate și endpoint-urile pe care le cheamă
Lambda-urile din simulare. Fiecare model are un singur contract, iar simularea nu citește
fișiere de model: trimite o cerere către endpoint și primește un răspuns.

| Model | Container | Artefact | Cerere | Răspuns |
| --- | --- | --- | --- | --- |
| `predictive-maintenance` | built-in `randomcutforest:1` | `model_algo-*.model.rcf` | `{"instances": [[9 valori]]}` | `{"scores": [{"score": 1.87}]}` |
| `vision-qa` | `pytorch-inference` CPU | `model.pth` + `code/inference.py` | `{"instances": [{"part_id": ..., "image": {...}}]}` | `{"predictions": [{"defect_probability": 0.8, "family": "hex_68", "family_probability": 0.97}]}` |

## Predictive maintenance

RCF-ul built-in nu are nevoie de un `inference.py`: containerul citește direct artefactul
SageMaker. Ordinea celor nouă valori este `MODEL_FEATURE_ORDER` din
`lambdas/feature_engineering/core.py`, iar notebook-ul `pm-02-rcf.ipynb` o folosește la
antrenare. O diferență de ordine ori de număr de coloane produce scoruri fără sens, fără nicio
eroare la deployment.

Antrenarea scrie o copie stabilă la
`s3://<data-lake>/models/predictive-maintenance/<versiune>/model.tar.gz`. Din ea se
completează `predictive_maintenance_model_data_url` și
`predictive_maintenance_anomaly_threshold` (percentila 99) în
`stacks/platform/envs/dev.tfvars`, iar `enable_predictive_maintenance_endpoint = true`
pornește endpoint-ul serverless.

## Vision QA

`train.py` copiază `inference.py`, `vision_inference_core.py` și `vision_model.py` în `code/`
din `model.tar.gz`, deci artefactul este deployabil imediat. Containerul de inferență este CPU,
fiindcă endpoint-ul serverless nu are GPU; greutățile ResNet18 sunt aceleași.

Modelul are două capete peste același backbone: familia piesei (șase clase) și verdictul
PASS/REJECT. Răspunsul păstrează `defect_probability` pentru Lambda și adaugă `family` cu
`family_probability`; un cadru fără imagine rămâne `judged: false` și nu primește familie.
Sortarea folosește verdictul pentru cutie sau rebut, iar familia pentru alegerea
compartimentului; sub încrederea de familie stabilită în celulă, piesa urmează ruta de rezervă.

Antrenarea scrie o copie stabilă la `s3://<data-lake>/models/vision-qa/<versiune>/model.tar.gz`
și un `deployment.json` cu imaginea, contractul, lista familiilor și pragul. Din ele se
completează `vision_qa_model_data_url`, `vision_qa_defect_threshold` și
`vision_qa_endpoint_enabled = true`. Dacă notebook-ul schimbă `INFERENCE_IMAGE`, aceeași
etichetă se pune în `vision_qa_prebuilt_image_tag`.

Fără endpoint, Lambda-ul de inspecție nu inventează verdicte: numără cadrele în
`QaModelMissing`, iar simularea rămâne pe ruta de rezervă (ADR 0013). Un cadru fără
fotografie primește de la container `judged: false`; Lambda-ul îl tratează la fel, nu
transformă probabilitatea de umplutură într-un verdict.

Datasetul de antrenare are cadre de sus, cu piesa singură în imagine, iar snapshot-ul QA din
celulă are 1280×720 și cuprinde banda, fiind luat de camera brațului de sortare. Modelul
primește imaginea întreagă, redimensionată la 224×224, deci diferența de încadrare este o
limită de performanță, nu una de contract. Se verifică pe cadre reale înainte de promovare;
dacă piesa ocupă prea puțin din cadru, se regenerează datasetul din cadrele aceleiași camere.

## Verificare după deployment

Ambele endpoint-uri sunt serverless, deci prima invocare include cold start-ul: la RCF câteva
zeci de secunde, la PyTorch CPU până la câteva minute. O invocare de încălzire, făcută din
Studio, evită ca primele cadre din celulă să cadă pe `QaEndpointFailures`.

Din Studio, cu rolul de execuție al spațiului:

```python
import boto3, json
rt = boto3.client("sagemaker-runtime", region_name="eu-central-1")

# Predictive maintenance: aceleași nouă valori, în ordinea FEATURES.
r = rt.invoke_endpoint(EndpointName="cncdt-dev-predictive-maintenance",
                       ContentType="application/json", Accept="application/json",
                       Body=json.dumps({"instances": [[1.2, 2.1, 0.4, 0.8, 2.4, 0.3, 1.8, 41.0, 0.25]]}))
print(r["Body"].read())   # {"scores": [{"score": ...}]}

# Vision QA: un PNG din training-data/parts/, în base64, fără etichete.
r = rt.invoke_endpoint(EndpointName="cncdt-dev-vision-qa",
                       ContentType="application/json", Accept="application/json",
                       Body=json.dumps({"instances": [{"part_id": "smoke-1", "image": {
                           "format": "png", "encoding": "base64", "data": "<base64>"}}]}))
print(r["Body"].read())   # {"predictions": [{"part_id": "smoke-1", "defect_probability": ..., "family": ..., "family_probability": ...}]}
```

Serverless Inference nu acceptă data capture; `stacks/platform` pornește captura doar pe
`provisioned`, iar modulul de endpoint refuză combinația în plan.

Pentru hot path, `make publish-dev` publică 20 de cicluri sintetice în contractul de
telemetrie, fără Isaac Sim. Lambda-ul de feature engineering trebuie să publice `AnomalyScore`
în CloudWatch și să scrie în Parquet `model_version` din `model_versions`; dacă rămâne
`thresholds`, endpoint-ul nu a fost chemat.

## Verificare fără endpoint

- `make test` acoperă contractele pure: `parse_endpoint_response` pentru ambele răspunsuri și
  `vision_inference_core` pentru cererea cu imagine base64.
- În Studio, `qa-02-vision.ipynb` încarcă `model.pth` pe CPU și clasifică câteva imagini de
  test. Asta verifică greutățile, nu containerul.
- Un endpoint serverless se poate invoca din notebook cu `boto3.client("sagemaker-runtime")`;
  costul apare doar la invocare, iar oprirea se face cu `enable_*_endpoint = false` și
  `make platform-up`.
