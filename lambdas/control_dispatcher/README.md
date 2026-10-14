# Lambda: dispecer de comenzi (control dispatcher)

Consumă coada SQS de comenzi și publică fiecare comandă pe topicul IoT potrivit către
simulare. Comenzile care modifică degradarea sau injectează defecte merg pe topicul de chaos;
celelalte (pornire, oprire, reparații) merg pe topicul de comandă. Fiecare comandă primește o
urmă de audit în data lake.

## Fișiere

| Fișier | Rol |
| --- | --- |
| `handler.py` | Legătura cu AWS: citire SQS, publicare IoT, audit în S3, `batchItemFailures` |
| `control_core.py` | Logica pură: decodare, validare, rutare pe topic, chei și urme de audit |

`control_core.py` nu importă boto3. Numele modulului este unic pentru că toate directoarele de
Lambda ajung pe același `sys.path` în teste, iar `core` este deja folosit de feature
engineering.

## Flux

1. **Citire lot** — maxim 10 mesaje per invocare, fereastră de batching de 5 secunde.
2. **Decodare** — corpul JSON; se acceptă și mesaje împachetate de SNS (`Message`).
3. **Validare** — comanda trebuie să fie în lista din contractul `cnc.control.v1`; câmpurile
   `amount` și `value` trebuie să fie numere între 0 și 1.
4. **Rutare** — `inject_tool_wear`, `inject_joint_friction`, `force_defect`,
   `set_defect_probability` merg pe `CHAOS_TOPIC`; restul pe `CONTROL_TOPIC`.
5. **Audit** — `curated/control-audit/dt=<zi>/<ts>-<comandă>-<messageId>.json`.
6. **Răspuns** — `batchItemFailures` conține doar mesajele care au căzut la publicare, ca SQS
   să le reia pe acelea și numai pe acelea.

## Comenzi acceptate

`inject_tool_wear`, `inject_joint_friction`, `force_defect`, `replace_tool`, `repair_joints`,
`set_defect_probability`, `pause`, `resume`, `start_cycle`, `stop_cycle`, `reset`.

O comandă necunoscută este logată ca `command_rejected` și **nu** se reia: ar cădea la
nesfârșit. Mesajele care nu pot fi publicate ajung în `batchItemFailures` și, după cinci
încercări, în coada DLQ.

## Variabile de mediu

| Variabilă | Implicit | Rol |
| --- | --- | --- |
| `CONTROL_TOPIC` | gol | Topicul IoT pentru comenzile de producție |
| `CHAOS_TOPIC` | gol | Topicul IoT pentru degradare și defecte |
| `AUDIT_PREFIX` | `curated/control-audit` | Prefixul urmelor de audit |
| `DATA_LAKE_BUCKET` | gol | Bucket-ul pentru audit; gol înseamnă fără audit |
| `IOT_DATA_ENDPOINT` | gol | Endpoint-ul ATS IoT Core; gol folosește endpoint-ul implicit boto3 |
| `LOG_LEVEL` | `INFO` | Nivelul de jurnalizare |

## Limite

- Nu publică metrice CloudWatch: politica IAM a funcției nu include `cloudwatch:PutMetricData`.
  Observabilitatea comenzilor se face prin logurile structurate și prin dashboard.
- Auditul este best-effort: dacă scrierea în S3 eșuează, comanda a plecat deja și eroarea se
  loghează, fără reîncercare.
