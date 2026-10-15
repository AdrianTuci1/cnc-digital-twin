# Bucket-ul corpusului de antrenament și rolul pe care îl asumă containerele Modal.

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Repository  = "cnc-digital-twin"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  # Numele de bucket S3 sunt unice global: includem ID-ul de cont ca sufix, ca la data lake.
  bucket_name = "${var.project_name}-${var.environment}-training-data-${local.account_id}"

  modal_role_name = var.modal_role_name == "" ? "${var.project_name}-${var.environment}-modal-collector" : var.modal_role_name

  modal_provider_arn = var.modal_oidc_provider_arn == "" ? "arn:${local.partition}:iam::${local.account_id}:oidc-provider/oidc.modal.com" : var.modal_oidc_provider_arn
}

# Gardul de lansare (ops/runbooks/launch-guard.md). Nu creează nimic în AWS: doar refuză planul
# și aplicarea care ar crea resurse plătite, cât timp `launch_confirmed` nu este `true`. Bucket-ul
# se plătește pe volum stocat (0,023 USD/GB-lună în Standard), iar corpusul crește cu fiecare
# colectare, deci crearea lui este deliberată.
resource "terraform_data" "launch_guard" {
  lifecycle {
    precondition {
      condition     = var.launch_confirmed
      error_message = <<-EOT
        Crearea bucket-ului de date de antrenament este blocată deliberat: `launch_confirmed` este `false`.

        Bucket-ul se plătește pe volum stocat, iar corpusul crește la fiecare colectare, deci nu se
        creează fără o confirmare explicită. Refuzul apare înainte de orice apel la AWS, deci NU este
        o eroare de credențiale, de profil sau de rețea.

        Ca să lansezi deliberat:
          pune în .env:  INFRA_LAUNCH_ENABLED=true
          apoi rulează:  make training-data-up

        Ori, direct din acest director:
          terraform apply -var launch_confirmed=true

        Ștergerea nu este afectată: `make training-data-down` rămâne permis, cu sau fără
        `-var launch_confirmed=true`. Detalii: ops/runbooks/launch-guard.md
      EOT
    }
  }
}

# ---------------------------------------------------------------- corpusul de antrenament
# Aici ajung sesiunile geamănului MuJoCo, urcate de pe Modal sub `training-data/mujoco/`, și de
# aici le citesc joburile de procesare și de antrenare din SageMaker, fără să treacă prin
# calculatorul nimănui. Prefixele sunt cele documentate în `docs/11-geaman-mujoco.md` §4.
resource "aws_s3_bucket" "training" {
  bucket        = local.bucket_name
  force_destroy = var.force_destroy

  tags = merge(local.common_tags, { Name = local.bucket_name, Purpose = "training-data" })
}

resource "aws_s3_bucket_versioning" "training" {
  bucket = aws_s3_bucket.training.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "training" {
  bucket = aws_s3_bucket.training.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.kms_key_arn == "" ? "AES256" : "aws:kms"
      kms_master_key_id = var.kms_key_arn == "" ? null : var.kms_key_arn
    }
    bucket_key_enabled = var.kms_key_arn != ""
  }
}

resource "aws_s3_bucket_public_access_block" "training" {
  bucket = aws_s3_bucket.training.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Fiecare colectare scrie o rulare nouă, iar versiunile vechi ale acelorași chei se adună.
# Nu trecem corpusul în IA ori Glacier: este citit la fiecare antrenare.
resource "aws_s3_bucket_lifecycle_configuration" "training" {
  bucket = aws_s3_bucket.training.id

  rule {
    id     = "expira-versiunile-vechi"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.training]
}

# ---------------------------------------------------------------- rolul pentru Modal
# Containerele Modal primesc un token de identitate în `MODAL_IDENTITY_TOKEN` și îl schimbă pe
# credențiale temporare prin `sts:AssumeRoleWithWebIdentity`. Nu există utilizator IAM și nu
# există chei de acces în secretul Modal (ADR 0006). Trust-ul este legat de spațiul de lucru
# Modal: alt spațiu nu poate asuma rolul, chiar dacă are un token valid.
data "aws_iam_policy_document" "modal_assume" {
  statement {
    sid     = "AllowModalWorkspaceOnly"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.modal_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "oidc.modal.com:aud"
      values   = ["oidc.modal.com"]
    }

    condition {
      test     = "StringLike"
      variable = "oidc.modal.com:sub"
      values   = ["modal:workspace_id:${var.modal_workspace_id}:*"]
    }
  }
}

resource "aws_iam_role" "modal" {
  name = local.modal_role_name
  # IAM acceptă în `description` doar ASCII și Latin-1 (U+0020-U+007E, U+00A1-U+00FF), iar
  # diacriticele românești sunt în afara intervalului. De aceea textul e fără ele.
  description = "Rolul asumat de containerele Modal care scriu corpusul de antrenament in bucket; fara chei statice."

  assume_role_policy   = data.aws_iam_policy_document.modal_assume.json
  max_session_duration = 3600

  tags = merge(local.common_tags, { Component = "modal-collector" })
}

# Doar bucket-ul corpusului, și doar ce îi trebuie colectorului: scriere, citire pentru
# verificarea proprie, listare. Fără `Resource: "*"` (regula 6 din AGENTS.md).
data "aws_iam_policy_document" "modal" {
  statement {
    sid    = "WriteCorpus"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:GetObject",
    ]

    resources = ["${aws_s3_bucket.training.arn}/*"]
  }

  statement {
    sid       = "ListCorpus"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.training.arn]
  }
}

resource "aws_iam_role_policy" "modal" {
  name   = "${local.modal_role_name}-corpus"
  role   = aws_iam_role.modal.id
  policy = data.aws_iam_policy_document.modal.json
}
