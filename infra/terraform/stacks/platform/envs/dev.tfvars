# Valori pentru mediul dev: costuri minime, date sintetice, endpoint-uri ML dezactivate
# până când există primul model antrenat.
#
# Utilizare: terraform apply -var-file=envs/dev.tfvars

project_name = "cncdt"
environment  = "dev"
region       = "eu-central-1"

force_destroy_buckets = true

# Ingestie
telemetry_topic             = "cncdt/+/cnc/+/telemetry"
iot_thing_name              = "cnc-cell-01"
firehose_buffering_interval = 60
firehose_enable_backup      = true

# Hot path
anomaly_threshold         = 3
lambda_log_retention_days = 7
alarm_email_endpoints     = ["adrian.tucicovenco+cncalerts@gmail.com"]

# Studio: pornit, dar cu instanțe mici și oprire automată agresivă.
enable_sagemaker_studio           = true
sagemaker_user_profiles           = ["engineer"]
sagemaker_kernel_instance_type    = "ml.t3.medium"
sagemaker_training_instance_types = ["ml.t3.medium", "ml.m5.large", "ml.g5.xlarge"]
enable_studio_idle_shutdown       = true
# Corpusul geamănului, din stack-ul `training-data`: Studio îl citește direct, fără copie.
training_data_bucket_arn = "arn:aws:s3:::cncdt-dev-training-data-864981730944"

# Endpoint-uri: activate după prima antrenare. Ambele sunt serverless, deci nu costă când
# nu sunt invocate. `*_model_data_url` se completează cu calea stabilă scrisă de notebook
# (`models/<model>/<versiune>/model.tar.gz`), nu cu artefactul jobului.
enable_predictive_maintenance_endpoint = true
predictive_maintenance_model_data_url  = "s3://cncdt-dev-datalake-864981730944/models/predictive-maintenance/pm-rcf-v3/model.tar.gz"
# Pragul din metrics.json (percentila 97,5 pe ferestrele sănătoase, cu alarmă pe două ferestre
# consecutive). Scorul RCF nu este o probabilitate, iar scara lui se deplasează între rulări.
predictive_maintenance_anomaly_threshold = 1.58
vision_qa_endpoint_enabled               = true
vision_qa_model_data_url                 = "s3://cncdt-dev-datalake-864981730944/models/vision-qa/vision-qa-v3/model.tar.gz"
# Pragul din deployment.json-ul v3: ales pe val cu recall PASS de cel puțin 0,90. Pe cadrele
# celulei verdictul e slab (vezi cardul de model); familia e perfectă, verdictul cere mai multe date.
vision_qa_defect_threshold = 0.91
# Versiunile scrise în `model_version` pe hot path; fără ele rămâne „unversioned”.
model_versions = { vision_qa = "vision-qa-v3", predictive_maintenance = "pm-rcf-v3" }

# Backup și control de cost.
# EFS-ul de home al Studio este păstrat de domeniu la ștergere, deci backup-ul este
# singura plasă de siguranță pentru notebook-uri. Seiful se șterge cu puncte cu tot.
enable_studio_backup         = true
studio_backup_retention_days = 30
studio_backup_force_destroy  = true

# Buget lunar. Fără `alarm_email_endpoints`, bugetul se creează fără notificări.
enable_cost_budget      = true
monthly_budget_usd      = 20
budget_scope_to_project = false

# Simulare: opțional, ID-ul instanței din stack-ul `sim` pentru widget-urile GPU
# din dashboard. `make sim-status` afișează ID-ul.
sim_instance_id = ""
