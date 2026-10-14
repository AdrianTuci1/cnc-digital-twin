locals {
  name = "${var.name_prefix}-${var.function_name}"

  tags = merge(
    {
      Component = "data-pipeline"
      ManagedBy = "terraform"
    },
    var.tags,
  )

  excludes = ["__pycache__", "*.pyc", ".pytest_cache", "tests", "requirements*.txt", "*.md", ".DS_Store"]

  # Al doilea director și cele următoare sunt straturi comune (de exemplu `lambdas/_shared`
  # sau `ml/src/cncdt_ml`). `archive_file` acceptă un singur `source_dir`, deci fișierele
  # suplimentare intră ca blocuri `source`, păstrându-și numele directorului ca prefix:
  # `lambdas/_shared/logging.py` ajunge în arhivă ca `_shared/logging.py`, deci se importă
  # drept `from _shared import logging`. Două straturi cu același nume de director ar intra
  # în conflict; numele trebuie să fie unic.
  extra_dirs = length(var.source_dirs) > 1 ? slice(var.source_dirs, 1, length(var.source_dirs)) : []

  extra_files = length(local.extra_dirs) > 0 ? merge([
    for dir in local.extra_dirs : {
      for f in fileset(dir, "**") :
      "${basename(dir)}/${f}" => "${dir}/${f}"
      if can(regex("\\.(py|json|txt|cfg|toml)$", f))
    }
  ]...) : {}
}

# ---------------------------------------------------------------- pachet
data "archive_file" "this" {
  type        = "zip"
  output_path = "${path.module}/.build/${local.name}.zip"

  source_dir = var.source_dirs[0]
  excludes   = local.excludes

  dynamic "source" {
    for_each = local.extra_files

    content {
      content  = file(source.value)
      filename = source.key
    }
  }

  dynamic "source" {
    for_each = var.source_files

    content {
      content  = file(source.key)
      filename = source.value
    }
  }
}

# ---------------------------------------------------------------- IAM
data "aws_iam_policy_document" "assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${local.name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  description        = "Rolul functiei Lambda ${local.name}."

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "basic_execution" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "xray" {
  count = var.tracing_mode == "Active" ? 1 : 0

  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy" "inline" {
  count = var.policy_json == "" ? 0 : 1

  name   = "${local.name}-inline"
  role   = aws_iam_role.this.id
  policy = var.policy_json
}

# ---------------------------------------------------------------- funcția
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${local.name}"
  retention_in_days = var.log_retention_days

  tags = local.tags
}

resource "aws_lambda_function" "this" {
  function_name    = local.name
  description      = "CNC Digital Twin — ${var.function_name}"
  role             = aws_iam_role.this.arn
  handler          = var.handler
  runtime          = var.runtime
  architectures    = [var.architecture]
  memory_size      = var.memory_mb
  timeout          = var.timeout_seconds
  layers           = var.layers
  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256

  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = merge(
      {
        LOG_LEVEL               = "INFO"
        POWERTOOLS_SERVICE_NAME = var.function_name
      },
      var.environment_variables,
    )
  }

  tracing_config {
    mode = var.tracing_mode
  }

  dynamic "dead_letter_config" {
    for_each = var.dlq_arn == "" ? [] : [var.dlq_arn]
    content {
      target_arn = dead_letter_config.value
    }
  }

  # Log group-ul se creează înaintea funcției, ca să nu rămână fără retenție.
  depends_on = [
    aws_cloudwatch_log_group.this,
    aws_iam_role_policy_attachment.basic_execution,
  ]

  tags = local.tags
}

# ---------------------------------------------------------------- alarme
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${local.name}-errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = var.error_alarm_threshold
  treat_missing_data  = "notBreaching"
  alarm_description   = "Funcția ${local.name} a înregistrat erori."

  dimensions = {
    FunctionName = aws_lambda_function.this.function_name
  }

  alarm_actions = var.alarm_actions
  ok_actions    = var.alarm_actions

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "throttles" {
  alarm_name          = "${local.name}-throttles"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "Funcția ${local.name} este limitată de concurrency."

  dimensions = {
    FunctionName = aws_lambda_function.this.function_name
  }

  alarm_actions = var.alarm_actions

  tags = local.tags
}
