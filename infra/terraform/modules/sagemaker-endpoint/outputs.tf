output "endpoint_name" {
  description = "Numele endpoint-ului."
  value       = aws_sagemaker_endpoint.this.name
}

output "endpoint_arn" {
  description = "ARN-ul endpoint-ului."
  value       = aws_sagemaker_endpoint.this.arn
}

output "endpoint_config_name" {
  description = "Numele configurației active."
  value       = aws_sagemaker_endpoint_configuration.this.name
}

output "model_name" {
  description = "Numele modelului înregistrat în SageMaker."
  value       = aws_sagemaker_model.this.name
}

output "model_arn" {
  description = "ARN-ul modelului."
  value       = aws_sagemaker_model.this.arn
}

output "execution_role_arn" {
  description = "Rolul de execuție folosit de endpoint."
  value       = local.execution_role_arn
}

output "container_image" {
  description = "Imaginea de inferență folosită efectiv."
  value       = local.container_image
}

output "deployment_mode" {
  description = "Modul de implementare: serverless sau provisioned."
  value       = var.deployment_mode
}

output "data_capture_uri" {
  description = "Prefixul S3 unde ajunge captura de date (gol dacă este dezactivată)."
  value       = var.enable_data_capture ? "s3://${var.data_capture_bucket_name}/${var.data_capture_prefix}/${local.name}" : ""
}

output "invoke_command" {
  description = "Comandă AWS CLI pentru o invocare de test."
  value = var.model_key == "vision-qa" ? format(
    "aws sagemaker-runtime invoke-endpoint --endpoint-name %s --content-type application/json --body fileb://payload.json --cli-binary-format raw-in-base64-out /dev/stdout",
    aws_sagemaker_endpoint.this.name,
    ) : format(
    "aws sagemaker-runtime invoke-endpoint --endpoint-name %s --content-type text/csv --body fileb://payload.csv --cli-binary-format raw-in-base64-out /dev/stdout",
    aws_sagemaker_endpoint.this.name,
  )
}

output "log_group_name" {
  description = "Log group-ul endpoint-ului."
  value       = aws_cloudwatch_log_group.endpoint.name
}

output "autoscaling_enabled" {
  description = "Dacă auto-scalarea este activă."
  value       = var.enable_autoscaling && !local.is_serverless
}
