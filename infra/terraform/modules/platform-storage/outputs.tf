output "data_lake_bucket_name" {
  description = "Bucketul data lake (raw, curated, models)."
  value       = aws_s3_bucket.datalake.bucket
}

output "data_lake_bucket_arn" {
  description = "ARN-ul bucket-ului data lake."
  value       = aws_s3_bucket.datalake.arn
}

output "assets_bucket_name" {
  description = "Bucketul de asset-uri de simulare."
  value       = aws_s3_bucket.assets.bucket
}

output "assets_bucket_arn" {
  description = "ARN-ul bucket-ului de asset-uri."
  value       = aws_s3_bucket.assets.arn
}

output "logs_bucket_name" {
  description = "Bucketul de loguri și backup-uri Firehose."
  value       = aws_s3_bucket.logs.bucket
}

output "logs_bucket_arn" {
  description = "ARN-ul bucket-ului de loguri."
  value       = aws_s3_bucket.logs.arn
}

output "athena_results_bucket_name" {
  description = "Bucketul pentru rezultatele interogărilor Athena."
  value       = aws_s3_bucket.athena.bucket
}

output "glue_database_name" {
  description = "Baza de date Glue (gol dacă este dezactivată)."
  value       = var.enable_glue_catalog ? aws_glue_catalog_database.this[0].name : ""
}

output "all_bucket_arns" {
  description = "Toate ARN-urile de bucket, pentru politici IAM."
  value = [
    aws_s3_bucket.datalake.arn,
    aws_s3_bucket.assets.arn,
    aws_s3_bucket.logs.arn,
    aws_s3_bucket.athena.arn,
  ]
}
