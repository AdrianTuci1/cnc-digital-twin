output "domain_id" {
  description = "ID-ul domeniului SageMaker Studio."
  value       = aws_sagemaker_domain.this.id
}

output "domain_arn" {
  description = "ARN-ul domeniului."
  value       = aws_sagemaker_domain.this.arn
}

output "domain_name" {
  description = "Numele domeniului."
  value       = aws_sagemaker_domain.this.domain_name
}

output "studio_url" {
  description = "Link către consola SageMaker Studio."
  value       = "https://${var.region}.console.aws.amazon.com/sagemaker/home?region=${var.region}#/studio/"
}

output "user_profile_names" {
  description = "Profilele de utilizator create."
  value       = [for p in aws_sagemaker_user_profile.users : p.user_profile_name]
}

output "execution_role_arn" {
  description = "Rolul de execuție folosit de notebook-uri și job-uri."
  value       = aws_iam_role.studio.arn
}

output "execution_role_name" {
  description = "Numele rolului de execuție."
  value       = aws_iam_role.studio.name
}

output "security_group_id" {
  description = "Security group-ul domeniului."
  value       = aws_security_group.studio.id
}

output "home_efs_file_system_id" {
  description = "ID-ul EFS-ului de home creat de SageMaker. Folosit de planul de backup."
  value       = aws_sagemaker_domain.this.home_efs_file_system_id
}

output "vpc_id" {
  description = "VPC-ul folosit de domeniu."
  value       = local.vpc_id
}

output "subnet_ids" {
  description = "Subneturile folosite de domeniu."
  value       = local.subnet_ids
}
