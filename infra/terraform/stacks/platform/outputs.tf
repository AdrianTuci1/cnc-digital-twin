output "data_lake_bucket" {
  description = "Bucketul data lake."
  value       = module.storage.data_lake_bucket_name
}

output "assets_bucket" {
  description = "Bucketul de asset-uri de simulare."
  value       = module.storage.assets_bucket_name
}

output "logs_bucket" {
  description = "Bucketul de loguri și backup-uri."
  value       = module.storage.logs_bucket_name
}

output "glue_database_name" {
  description = "Baza de date din catalogul Glue, folosită doar ca registru de schemă."
  value       = module.storage.glue_database_name
}

output "telemetry_glue_table" {
  description = "Tabela de telemetrie din catalogul Glue."
  value       = module.iot_ingestion.glue_table_name
}

output "iot_endpoint" {
  description = "Endpoint-ul MQTT al AWS IoT Core."
  value       = module.iot_ingestion.iot_data_endpoint
}

output "iot_thing_name" {
  description = "Numele lucrului IoT al celulei CNC."
  value       = module.iot_ingestion.thing_name
}

output "iot_certificate_pem" {
  description = "Certificatul dispozitivului (PEM)."
  value       = module.iot_ingestion.certificate_pem
  sensitive   = true
}

output "iot_private_key" {
  description = "Cheia privată a dispozitivului (PEM). Se scrie în state-ul Terraform."
  value       = module.iot_ingestion.private_key
  sensitive   = true
}

output "telemetry_topic_filter" {
  description = "Filtrul de topic pentru telemetrie."
  value       = module.iot_ingestion.telemetry_topic_filter
}

output "control_topic" {
  description = "Topicul de comenzi către simulare."
  value       = local.command_topic
}

output "qa_verdict_topic" {
  description = "Topicul pe care se publică verdictul de inspecție."
  value       = local.qa_verdict_topic
}

output "chaos_topic" {
  description = "Topicul pentru injectarea de defecte în simulare."
  value       = local.defect_topic
}

output "commands_queue_url" {
  description = "Coada SQS din care se trimit comenzile de operator."
  value       = aws_sqs_queue.commands.url
}

output "firehose_stream_name" {
  description = "Numele stream-ului Firehose."
  value       = module.iot_ingestion.firehose_stream_name
}

output "lambda_function_names" {
  description = "Numele funcțiilor Lambda de pipeline."
  value = {
    feature_engineering = module.lambda_feature_engineering.function_name
    qa_vision           = module.lambda_qa_vision.function_name
    control_dispatcher  = module.lambda_control_dispatcher.function_name
  }
}

output "predictive_maintenance_endpoint" {
  description = "Numele endpoint-ului de anomalii (gol dacă este dezactivat)."
  value       = var.enable_predictive_maintenance_endpoint ? local.pm_endpoint_name : ""
}

output "vision_qa_endpoint" {
  description = "Numele endpoint-ului de inspecție vizuală (gol dacă este dezactivat)."
  value       = var.vision_qa_endpoint_enabled ? local.qa_endpoint_name : ""
}

output "sagemaker_domain_id" {
  description = "ID-ul domeniului SageMaker Studio."
  value       = var.enable_sagemaker_studio ? module.sagemaker_studio[0].domain_id : ""
}

output "sagemaker_execution_role_arn" {
  description = "Rolul de execuție pentru notebook-uri și job-uri."
  value       = var.enable_sagemaker_studio ? module.sagemaker_studio[0].execution_role_arn : ""
}

output "dashboard_names" {
  description = "Numele dashboard-urilor CloudWatch: infrastructură și fabrică."
  value       = module.observability.dashboard_names
}

output "dashboard_urls" {
  description = "Linkuri către dashboard-uri: infrastructură și fabrică."
  value       = module.observability.dashboard_urls
}

output "dashboard_name" {
  description = "Numele dashboard-ului de infrastructură, păstrat pentru compatibilitate."
  value       = module.observability.dashboard_name
}

output "dashboard_url" {
  description = "Link către dashboard-ul de infrastructură, păstrat pentru compatibilitate."
  value       = module.observability.dashboard_url
}

output "alarm_topic_arn" {
  description = "Subiectul SNS al alarmelor."
  value       = module.observability.alarm_topic_arn
}

output "alarm_names" {
  description = "Toate alarmele de metrice create."
  value       = module.observability.alarm_arns
}

output "studio_efs_file_system_id" {
  description = "ID-ul EFS-ului de home al Studio. Gol când Studio este dezactivat."
  value       = coalesce(local.studio_efs_id, "")
}

output "studio_backup_vault_name" {
  description = "Seiful AWS Backup care apără EFS-ul Studio. Gol când backup-ul este dezactivat."
  value       = local.backup_vault_name
}

output "studio_backup_plan_id" {
  description = "ID-ul planului de backup al EFS-ului Studio."
  value       = coalesce(one(module.studio_backup[*].plan_id), "")
}

output "studio_backup_role_arn" {
  description = "Rolul folosit de AWS Backup. Necesar dacă politica EFS-ului cere o excepție."
  value       = coalesce(one(module.studio_backup[*].service_role_arn), "")
}

output "monthly_budget_name" {
  description = "Numele bugetului lunar de cost. Gol când bugetul este dezactivat."
  value       = coalesce(one(module.budget[*].budget_name), "")
}

output "athena_workgroup_hint" {
  description = "Interogare Athena de verificare a fluxului de telemetrie."
  value       = "SELECT count(*), max(ts_ms) FROM \"${module.storage.glue_database_name}\".\"${module.iot_ingestion.glue_table_name}\" WHERE dt_year >= year(current_date)"
}
