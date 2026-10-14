# Producție: fără force_destroy, retenție lungă, endpoint-uri provisioned pentru latență constantă.
# Utilizare: terraform apply -var-file=envs/prod.tfvars
#
# Atenție: înainte de apply, completați ARN-ul cheii KMS și URL-urile model.tar.gz.

project_name = "cncdt"
environment  = "prod"
region       = "eu-central-1"

force_destroy_buckets = false
kms_key_arn           = "arn:aws:kms:us-east-1:REPLACE-ME:key/REPLACE-ME"

telemetry_topic             = "cncdt/+/cnc/+/telemetry"
firehose_buffering_interval = 60
firehose_enable_backup      = true

anomaly_threshold         = 3
lambda_log_retention_days = 30
alarm_email_endpoints     = ["REPLACE-ME@example.com"]

# Studio cu instanțe aprobate și oprire automată.
enable_sagemaker_studio           = true
sagemaker_user_profiles           = ["engineer", "qa", "ops"]
sagemaker_kernel_instance_type    = "ml.t3.medium"
sagemaker_training_instance_types = ["ml.t3.medium", "ml.m5.large", "ml.g5.xlarge", "ml.g5.2xlarge"]
enable_studio_idle_shutdown       = true

# Endpoint-uri provisioned, cu auto-scalare pe invocări.
ml_endpoint_deployment_mode = "provisioned"

enable_predictive_maintenance_endpoint = true
predictive_maintenance_model_data_url  = "s3://REPLACE-ME/models/predictive-maintenance/REPLACE/version/model.tar.gz"
vision_qa_endpoint_enabled             = true
vision_qa_model_data_url               = "s3://REPLACE-ME/models/vision-qa/REPLACE/version/model.tar.gz"

model_versions = {
  predictive_maintenance = "REPLACE-ME"
  vision_qa              = "REPLACE-ME"
}

# Backup EFS Studio: seif protejat și retenție lungă. `force_destroy = false` face
# `terraform destroy` să ceară întâi ștergerea manuală a punctelor de restaurare.
enable_studio_backup            = true
studio_backup_schedule          = "cron(0 3 ? * * *)"
studio_backup_retention_days    = 365
studio_backup_cold_storage_days = 90
studio_backup_force_destroy     = false

# Buget lunar, cu notificări la 50%, 80% din cheltuiala reală și 100% din prognoză.
# `budget_scope_to_project` cere eticheta `Project` activată în consola Billing.
enable_cost_budget      = true
monthly_budget_usd      = 100
budget_scope_to_project = false

sim_instance_id = ""
