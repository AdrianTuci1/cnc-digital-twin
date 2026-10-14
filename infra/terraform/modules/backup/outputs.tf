output "vault_name" {
  description = "Numele seifului de backup. Folosit de alarma de joburi eșuate."
  value       = aws_backup_vault.this.name
}

output "vault_arn" {
  description = "ARN-ul seifului."
  value       = aws_backup_vault.this.arn
}

output "plan_id" {
  description = "ID-ul planului de backup."
  value       = aws_backup_plan.this.id
}

output "service_role_arn" {
  description = "ARN-ul rolului folosit de AWS Backup."
  value       = aws_iam_role.backup.arn
}
