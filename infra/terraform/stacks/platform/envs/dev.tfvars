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
alarm_email_endpoints     = []

# Studio: pornit, dar cu instanțe mici și oprire automată agresivă.
enable_sagemaker_studio           = true
sagemaker_user_profiles           = ["engineer"]
sagemaker_kernel_instance_type    = "ml.t3.medium"
sagemaker_training_instance_types = ["ml.t3.medium", "ml.m5.large", "ml.g5.xlarge"]
enable_studio_idle_shutdown       = true

# Endpoint-uri: activate după prima antrenare.
enable_predictive_maintenance_endpoint = false
predictive_maintenance_model_data_url  = ""
vision_qa_endpoint_enabled             = false
vision_qa_model_data_url               = ""

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
