output "function_name" {
  description = "Numele funcției Lambda."
  value       = aws_lambda_function.this.function_name
}

output "function_arn" {
  description = "ARN-ul funcției."
  value       = aws_lambda_function.this.arn
}

output "invoke_arn" {
  description = "ARN-ul de invocare (folosit de integrarea cu Firehose și API Gateway)."
  value       = aws_lambda_function.this.invoke_arn
}

output "qualified_arn" {
  description = "ARN-ul calificat al versiunii publicate."
  value       = aws_lambda_function.this.qualified_arn
}

output "role_arn" {
  description = "ARN-ul rolului de execuție."
  value       = aws_iam_role.this.arn
}

output "role_name" {
  description = "Numele rolului de execuție."
  value       = aws_iam_role.this.name
}

output "log_group_name" {
  description = "Numele log group-ului CloudWatch."
  value       = aws_cloudwatch_log_group.this.name
}

output "source_code_hash" {
  description = "Hash-ul pachetului de deployment, util pentru a forța o nouă versiune."
  value       = aws_lambda_function.this.source_code_hash
}

output "error_alarm_names" {
  description = "Alarmele create pentru funcție."
  value       = [aws_cloudwatch_metric_alarm.errors.alarm_name, aws_cloudwatch_metric_alarm.throttles.alarm_name]
}
