output "role_arn" {
  description = "ARN-ul rolului de operator."
  value       = aws_iam_role.operator.arn
}

output "role_name" {
  description = "Numele rolului de operator."
  value       = aws_iam_role.operator.name
}

output "trusted_principals" {
  description = "Identitățile care pot asuma rolul."
  value       = local.trust_principals
}

output "mfa_required" {
  description = "Dacă rolul cere MFA la asumare."
  value       = var.require_mfa
}

output "modal_oidc_provider_arn" {
  description = "ARN-ul furnizorului OIDC al Modal, pe care se sprijină rolul din stack-ul `training-data`. Gol când `enable_modal_oidc` este `false`."
  value       = var.enable_modal_oidc ? aws_iam_openid_connect_provider.modal[0].arn : ""
}

output "console_switch_role_url" {
  description = "Link de comutare pe rol în consola AWS."
  value       = "https://signin.aws.amazon.com/switchrole?account=${local.account_id}&roleName=${aws_iam_role.operator.name}&displayName=${aws_iam_role.operator.name}"
}

output "profile_name" {
  description = "Numele sugerat pentru profilul local care asumă rolul."
  value       = var.project_name
}

output "profile_block" {
  description = "Blocul de pus în ~/.aws/config, sub numele de profil sugerat."
  value = join("\n", compact([
    "[profile ${var.project_name}]",
    "region = ${var.region}",
    "role_arn = ${aws_iam_role.operator.arn}",
    "source_profile = ${var.aws_profile == "" ? "default" : var.aws_profile}",
    var.require_mfa ? "mfa_serial = REPLACE-CU-ARN-UL-DISPOZITIVULUI-MFA" : "",
  ]))
}

output "verify_command" {
  description = "Comanda care confirmă că sesiunea este a rolului, nu a utilizatorului."
  value       = "aws sts get-caller-identity --profile ${var.project_name}"
}

output "next_steps" {
  description = "Pașii de după crearea rolului."
  value       = <<-EOT
    1. make access-profile          # scrie profilul '${var.project_name}' în ~/.aws/config
    2. pune AWS_PROFILE=${var.project_name} în .env
    3. make audit                   # verifică identitatea și starea resurselor
    4. make platform-up             # resursele apar în CloudTrail ca sesiune a rolului
  EOT
}
