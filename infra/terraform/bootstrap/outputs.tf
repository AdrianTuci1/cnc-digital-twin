output "state_bucket_name" {
  description = "Bucket-ul de state Terraform."
  value       = aws_s3_bucket.state.bucket
}

output "state_bucket_arn" {
  description = "ARN-ul bucket-ului de state."
  value       = aws_s3_bucket.state.arn
}

output "backend_config_example" {
  description = "Bloc de backend pentru stack-urile de platformă."
  value       = <<-EOT
    bucket       = "${aws_s3_bucket.state.bucket}"
    region       = "${var.region}"
    encrypt      = true
    use_lockfile = true
  EOT
}

output "next_steps" {
  description = "Comenzi de rulat după bootstrap."
  value       = <<-EOT
    Configurația de backend este scrisă automat de scripts/tf-backend.sh.
   1. make platform-up        # init + plan + apply pentru mediul din .env
   Pentru alt mediu: make platform-up ENVIRONMENT=staging
  EOT
}
