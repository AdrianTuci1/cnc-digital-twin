locals {
  name = var.endpoint_name == "" ? "${var.project_name}-${var.environment}-${var.model_key}" : var.endpoint_name

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Component   = "ml-inference"
      Model       = var.model_key
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  use_prebuilt_image = var.image_uri == ""
  kms_key_id         = var.kms_key_id == "" ? null : var.kms_key_id
  is_serverless      = var.deployment_mode == "serverless"
}

data "aws_region" "current" {}

# Imaginea preconstruită este potrivită când codul de inferență vine în model.tar.gz
# (model_fn/input_fn/predict_fn/output_fn din inference.py).
data "aws_sagemaker_prebuilt_ecr_image" "this" {
  count = local.use_prebuilt_image ? 1 : 0

  repository_name = var.prebuilt_image_repository
  image_tag       = var.prebuilt_image_tag
  region          = var.region
}

locals {
  container_image = local.use_prebuilt_image ? data.aws_sagemaker_prebuilt_ecr_image.this[0].registry_path : var.image_uri
}

# ---------------------------------------------------------------- rol de execuție
data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "endpoint" {
  count = var.create_execution_role ? 1 : 0

  name               = "${local.name}-execution-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  description        = "Rolul de executie al endpoint-ului ${local.name}."

  tags = local.tags
}

locals {
  execution_role_arn = var.create_execution_role ? aws_iam_role.endpoint[0].arn : var.execution_role_arn
}

data "aws_iam_policy_document" "endpoint_inline" {
  statement {
    sid    = "ReadModelArtifacts"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]

    resources = compact([
      var.model_data_bucket_arn,
      var.model_data_bucket_arn == "" ? "" : "${var.model_data_bucket_arn}/*",
    ])
  }

  dynamic "statement" {
    for_each = var.enable_data_capture && var.data_capture_bucket_name != "" ? [1] : []

    content {
      sid    = "WriteDataCapture"
      effect = "Allow"

      actions = [
        "s3:PutObject",
        "s3:AbortMultipartUpload",
        "s3:GetBucketLocation",
      ]

      resources = [
        "arn:aws:s3:::${var.data_capture_bucket_name}",
        "arn:aws:s3:::${var.data_capture_bucket_name}/*",
      ]
    }
  }

  statement {
    sid    = "PullInferenceImage"
    effect = "Allow"

    actions = [
      "ecr:GetAuthorizationToken",
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "Observability"
    effect = "Allow"

    actions = [
      "cloudwatch:PutMetricData",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:CreateLogGroup",
      "logs:DescribeLogStreams",
    ]

    resources = ["*"]
  }

  dynamic "statement" {
    for_each = var.kms_key_id == "" ? [] : [1]

    content {
      sid       = "UseKmsKey"
      effect    = "Allow"
      actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
      resources = [var.kms_key_id]
    }
  }
}

resource "aws_iam_role_policy" "endpoint_inline" {
  count = var.create_execution_role ? 1 : 0

  name   = "${local.name}-inline"
  role   = aws_iam_role.endpoint[0].id
  policy = data.aws_iam_policy_document.endpoint_inline.json
}

# ---------------------------------------------------------------- model
resource "aws_sagemaker_model" "this" {
  name                     = local.name
  execution_role_arn       = local.execution_role_arn
  enable_network_isolation = false

  primary_container {
    image          = local.container_image
    model_data_url = var.model_data_url

    environment = merge(
      {
        # Layout-ul așteptat în model.tar.gz este code/inference.py.
        MODEL_KEY = var.model_key
      },
      var.container_environment,
    )
  }

  tags = local.tags
}

# ---------------------------------------------------------------- configurația endpoint-ului
resource "aws_sagemaker_endpoint_configuration" "this" {
  name = local.name

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.this.name
    initial_variant_weight = 1

    # Modul serverless nu acceptă instance_type; modul provisioned nu acceptă serverless_config.
    instance_type          = local.is_serverless ? null : var.instance_type
    initial_instance_count = local.is_serverless ? null : var.initial_instance_count

    dynamic "serverless_config" {
      for_each = local.is_serverless ? [1] : []

      content {
        memory_size_in_mb = var.serverless_memory_mb
        max_concurrency   = var.serverless_max_concurrency
      }
    }
  }

  dynamic "data_capture_config" {
    for_each = var.enable_data_capture ? [1] : []

    content {
      enable_capture              = true
      initial_sampling_percentage = var.data_capture_sampling_percentage
      destination_s3_uri          = "s3://${var.data_capture_bucket_name}/${var.data_capture_prefix}/${local.name}"
      kms_key_id                  = local.kms_key_id

      capture_options {
        capture_mode = "Input"
      }
      capture_options {
        capture_mode = "Output"
      }
    }
  }

  kms_key_arn = local.kms_key_id

  tags = local.tags

  lifecycle {
    precondition {
      condition     = !var.enable_data_capture || var.data_capture_bucket_name != ""
      error_message = "Captura de date este activă, dar data_capture_bucket_name este gol. Completați numele bucket-ului sau dezactivați captura."
    }

    precondition {
      condition     = var.create_execution_role || var.execution_role_arn != ""
      error_message = "create_execution_role este false, deci execution_role_arn trebuie completat."
    }

    precondition {
      condition     = var.model_data_bucket_arn != ""
      error_message = "model_data_bucket_arn este obligatoriu pentru a acorda drepturi de citire asupra artefactului de model."
    }
  }
}

# ---------------------------------------------------------------- endpoint
resource "aws_sagemaker_endpoint" "this" {
  name                 = local.name
  endpoint_config_name = aws_sagemaker_endpoint_configuration.this.name

  tags = local.tags
}

# ---------------------------------------------------------------- auto-scalare (doar provisioned)
resource "aws_appautoscaling_target" "this" {
  count = var.enable_autoscaling && !local.is_serverless ? 1 : 0

  max_capacity       = var.autoscaling_max_capacity
  min_capacity       = var.autoscaling_min_capacity
  resource_id        = "endpoint/${aws_sagemaker_endpoint.this.name}/variant/AllTraffic"
  scalable_dimension = "sagemaker:variant:DesiredInstanceCount"
  service_namespace  = "sagemaker"

  depends_on = [aws_sagemaker_endpoint.this]
}

resource "aws_appautoscaling_policy" "this" {
  count = var.enable_autoscaling && !local.is_serverless ? 1 : 0

  name               = "${local.name}-invocations-target"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.this[0].resource_id
  scalable_dimension = aws_appautoscaling_target.this[0].scalable_dimension
  service_namespace  = aws_appautoscaling_target.this[0].service_namespace

  target_tracking_scaling_policy_configuration {
    target_value = var.autoscaling_target_invocations

    predefined_metric_specification {
      predefined_metric_type = "SageMakerVariantInvocationsPerInstance"
    }

    scale_in_cooldown  = 300
    scale_out_cooldown = 60
  }
}

# ---------------------------------------------------------------- log group
resource "aws_cloudwatch_log_group" "endpoint" {
  name              = "/aws/sagemaker/Endpoints/${local.name}"
  retention_in_days = var.environment == "prod" ? 30 : 7

  tags = local.tags
}
