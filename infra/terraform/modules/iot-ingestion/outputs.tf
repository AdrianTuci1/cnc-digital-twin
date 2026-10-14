output "iot_data_endpoint" {
  description = "Endpoint-ul MQTT (ATS) pentru AWS IoT Core."
  value       = data.aws_iot_endpoint.data_ats.endpoint_address
}

output "thing_name" {
  description = "Numele lucrului IoT al celulei."
  value       = var.create_thing_and_certificate ? aws_iot_thing.cell[0].name : var.thing_name
}

output "thing_arn" {
  description = "ARN-ul lucrului IoT."
  value       = var.create_thing_and_certificate ? aws_iot_thing.cell[0].arn : ""
}

output "certificate_arn" {
  description = "ARN-ul certificatului dispozitivului."
  value       = var.create_thing_and_certificate ? aws_iot_certificate.cell[0].arn : ""
}

output "certificate_id" {
  description = "ID-ul certificatului, necesar pentru atașarea la un lucru IoT."
  value       = var.create_thing_and_certificate ? aws_iot_certificate.cell[0].id : ""
}

# Secret: cheia privată ajunge în state-ul Terraform. În producție folosiți fleet provisioning
# sau mutați certificatele în AWS Secrets Manager imediat după creare.
output "certificate_pem" {
  description = "Certificatul public al dispozitivului, în format PEM."
  value       = var.create_thing_and_certificate ? aws_iot_certificate.cell[0].certificate_pem : ""
  sensitive   = true
}

output "private_key" {
  description = "Cheia privată a dispozitivului, în format PEM."
  value       = var.create_thing_and_certificate ? aws_iot_certificate.cell[0].private_key : ""
  sensitive   = true
}

output "device_policy_name" {
  description = "Numele politicii IoT atașate certificatului."
  value       = var.create_thing_and_certificate ? aws_iot_policy.cell[0].name : ""
}

output "firehose_stream_name" {
  description = "Numele stream-ului Firehose."
  value       = aws_kinesis_firehose_delivery_stream.telemetry.name
}

output "firehose_stream_arn" {
  description = "ARN-ul stream-ului Firehose."
  value       = aws_kinesis_firehose_delivery_stream.telemetry.arn
}

output "topic_rule_name" {
  description = "Numele regulii IoT de telemetrie."
  value       = aws_iot_topic_rule.telemetry.name
}

output "telemetry_topic_filter" {
  description = "Filtrul de topic de telemetrie."
  value       = var.telemetry_topic
}

output "control_topic_prefix" {
  description = "Prefixul topicurilor de control scrise către simulare."
  value       = local.control_prefix
}

output "glue_table_name" {
  description = "Tabelul Glue cu schema telemetriei."
  value       = var.create_glue_table ? aws_glue_catalog_table.telemetry[0].name : ""
}

output "firehose_log_group" {
  description = "Log group-ul livrărilor Firehose."
  value       = aws_cloudwatch_log_group.firehose.name
}

output "rule_error_log_group" {
  description = "Log group-ul erorilor regulii IoT."
  value       = aws_cloudwatch_log_group.iot_rule.name
}
