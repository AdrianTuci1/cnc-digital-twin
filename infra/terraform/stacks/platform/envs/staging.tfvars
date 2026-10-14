# Staging: structura identică producției, dar cu retenție redusă și endpoint-uri serverless.
# Utilizare: terraform apply -var-file=envs/staging.tfvars

project_name = "cncdt"
environment  = "staging"
region       = "eu-central-1"

force_destroy_buckets = true

telemetry_topic = "cncdt/+/cnc/+/telemetry"

anomaly_threshold         = 3
lambda_log_retention_days = 14

enable_sagemaker_studio     = true
sagemaker_user_profiles     = ["engineer", "qa"]
enable_studio_idle_shutdown = true

# Endpoint-uri serverless: cost zero în repaus, ideal pentru validare.
ml_endpoint_deployment_mode = "serverless"
ml_serverless_memory_mb     = 3072

enable_predictive_maintenance_endpoint = true
predictive_maintenance_model_data_url  = "s3://REPLACE-ME/models/predictive-maintenance/REPLACE/version/model.tar.gz"
vision_qa_endpoint_enabled             = true
vision_qa_model_data_url               = "s3://REPLACE-ME/models/vision-qa/REPLACE/version/model.tar.gz"

model_versions = {
  predictive_maintenance = "REPLACE-ME"
  vision_qa              = "REPLACE-ME"
}

# Fără adrese de e-mail, bugetul și alarmele se creează fără notificări.
alarm_email_endpoints = []

# Backup EFS Studio și buget de cost.
enable_studio_backup         = true
studio_backup_retention_days = 30
enable_cost_budget           = true
monthly_budget_usd           = 50

sim_instance_id = ""
