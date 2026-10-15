locals {
  # Numele de bucket S3 sunt unice global: includem ID-ul de cont ca sufix.
  base     = "${var.project_name}-${var.environment}"
  datalake = "${local.base}-datalake-${var.account_id}"
  assets   = "${local.base}-assets-${var.account_id}"
  logs     = "${local.base}-logs-${var.account_id}"
  athena   = "${local.base}-athena-results-${var.account_id}"

  glue_db = var.glue_database_name == "" ? replace("${var.project_name}_${var.environment}", "-", "_") : var.glue_database_name

  sse_algorithm     = var.kms_key_arn == "" ? "AES256" : "aws:kms"
  kms_master_key_id = var.kms_key_arn == "" ? null : var.kms_key_arn

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Component   = "storage"
      ManagedBy   = "terraform"
    },
    var.tags,
  )
}

# ---------------------------------------------------------------- data lake
# Structura prefixelor:
#   raw/telemetry/...        date brute transformate de Firehose în Parquet
#   raw/qa-snapshots/...     imagini de inspecție
#   curated/features/...     caracteristici agregate pentru antrenare
#   datasets/mujoco/...      corpus de antrenare urcat de geamănul MuJoCo (senzori + cadre)
#   models/<nume>/<versiune>/ artefacte de model
#   inference-logs/...       captură de date de la endpoint-uri
#   replay/errors/...        înregistrări respinse de Firehose, pentru rejucare
resource "aws_s3_bucket" "datalake" {
  bucket        = local.datalake
  force_destroy = var.force_destroy

  tags = merge(local.tags, { Name = local.datalake, Purpose = "data-lake" })
}

resource "aws_s3_bucket_versioning" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = local.sse_algorithm
      kms_master_key_id = local.kms_master_key_id
    }
    bucket_key_enabled = var.kms_key_arn != ""
  }
}

resource "aws_s3_bucket_public_access_block" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "raw-tiering"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    transition {
      days          = var.raw_transition_ia_days
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = var.raw_transition_glacier_days
      storage_class = "GLACIER_IR"
    }
  }

  rule {
    id     = "expire-error-replays"
    status = "Enabled"

    filter {
      prefix = "replay/errors/"
    }

    expiration {
      days = 90
    }
  }

  depends_on = [aws_s3_bucket_versioning.datalake]
}

# ---------------------------------------------------------------- assets de simulare
# Aici ajung USD/USDZ, texturi și snapshot-uri de referință. Instanța GPU le descarcă la boot.
resource "aws_s3_bucket" "assets" {
  bucket        = local.assets
  force_destroy = var.force_destroy

  tags = merge(local.tags, { Name = local.assets, Purpose = "simulation-assets" })
}

resource "aws_s3_bucket_versioning" "assets" {
  bucket = aws_s3_bucket.assets.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = local.sse_algorithm
      kms_master_key_id = local.kms_master_key_id
    }
  }
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket = aws_s3_bucket.assets.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id

  rule {
    id     = "expire-old-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }
  }

  depends_on = [aws_s3_bucket_versioning.assets]
}

# ---------------------------------------------------------------- logs
resource "aws_s3_bucket" "logs" {
  bucket        = local.logs
  force_destroy = var.force_destroy

  tags = merge(local.tags, { Name = local.logs, Purpose = "logs-and-backups" })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = local.sse_algorithm
      kms_master_key_id = local.kms_master_key_id
    }
  }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket = aws_s3_bucket.logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expire-firehose-backups"
    status = "Enabled"

    filter {
      prefix = "firehose-backup/"
    }

    expiration {
      days = var.environment == "prod" ? 30 : 7
    }
  }

  rule {
    id     = "expire-incident-archive"
    status = "Enabled"

    filter {
      prefix = "incident-archive/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
  }
}

# ---------------------------------------------------------------- Athena
resource "aws_s3_bucket" "athena" {
  bucket        = local.athena
  force_destroy = var.force_destroy

  tags = merge(local.tags, { Name = local.athena, Purpose = "athena-results" })
}

resource "aws_s3_bucket_public_access_block" "athena" {
  bucket = aws_s3_bucket.athena.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "athena" {
  bucket = aws_s3_bucket.athena.id

  rule {
    id     = "expire-query-results"
    status = "Enabled"

    filter {}

    expiration {
      days = var.athena_result_retention_days
    }
  }
}

# ---------------------------------------------------------------- catalog Glue
resource "aws_glue_catalog_database" "this" {
  count = var.enable_glue_catalog ? 1 : 0

  name        = local.glue_db
  description = "Catalogul de telemetrie și rezultate de inspecție pentru ${var.project_name}/${var.environment}."

  tags = local.tags
}
