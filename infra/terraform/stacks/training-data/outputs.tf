output "bucket_name" {
  description = "Numele bucket-ului corpusului. Se pune în `DATA_LAKE_BUCKET` din `.env`, de unde îl citesc `make mj-collect` și `publish.py`."
  value       = aws_s3_bucket.training.bucket
}

output "bucket_arn" {
  description = "ARN-ul bucket-ului."
  value       = aws_s3_bucket.training.arn
}

output "corpus_prefix" {
  description = "Prefixul sub care scrie colectorul."
  value       = "training-data/mujoco/"
}

output "modal_role_arn" {
  description = "ARN-ul rolului asumat de containerele Modal. Se pune în secretul Modal, câmpul AWS_ROLE_ARN."
  value       = aws_iam_role.modal.arn
}

output "modal_oidc_provider_arn" {
  description = "ARN-ul furnizorului OIDC pe care se sprijină trust-ul rolului; se creează în stack-ul `access`."
  value       = local.modal_provider_arn
}

output "modal_secret_command" {
  description = "Comanda care creează secretul Modal, fără chei de acces: doar rolul și regiunea."
  value = join(" ", [
    "modal secret create cncdt-aws",
    "AWS_ROLE_ARN=${aws_iam_role.modal.arn}",
    "AWS_REGION=${var.region}",
  ])
}

output "next_steps" {
  description = "Pașii de după creare."
  value       = <<-EOT
    1. pune în .env:            DATA_LAKE_BUCKET=${aws_s3_bucket.training.bucket}
    2. creează secretul Modal:  ${join(" ", ["modal secret create cncdt-aws", "AWS_ROLE_ARN=${aws_iam_role.modal.arn}", "AWS_REGION=${var.region}"])}
    3. rulează colectarea:      make mj-modal-collect
    4. verifică urcarea:        aws s3 ls s3://${aws_s3_bucket.training.bucket}/training-data/mujoco/ --recursive --summarize
  EOT
}
