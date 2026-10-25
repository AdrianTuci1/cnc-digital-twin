locals {
  name = "${var.name_prefix}-${var.function_name}"

  tags = merge(
    {
      Component = "data-pipeline"
      ManagedBy = "terraform"
    },
    var.tags,
  )

  # `archive_file` nu acceptă `source_dir` împreună cu blocuri `source`, iar Lambda-urile au
  # nevoie de straturi comune (`_shared`, `cncdt_ml`). De aceea toate fișierele intră ca
  # blocuri `source`: primul director păstrează căile relative, iar directoarele suplimentare
  # primesc numele directorului ca prefix (`_shared/logging_utils.py`). Numele directoarelor
  # trebuie să fie unic; altfel două straturi ar intra în conflict.
  dir_files = merge([
    for index, dir in var.source_dirs : {
      for f in fileset(dir, "**") :
      (index == 0 ? f : "${basename(dir)}/${f}") => "${dir}/${f}"
      if can(regex("\\.(py|json|txt|cfg|toml)$", f))
      && !can(regex("(^|/)(__pycache__|tests|\\.pytest_cache)(/|$)", f))
      && !can(regex("\\.pyc$", f))
      && !can(regex("(^|/)requirements[^/]*\\.txt$", f))
      && !can(regex("\\.md$", f))
      && !can(regex("(^|/)\\.DS_Store$", f))
    }
  ]...)

  explicit_files = { for local_path, archive_name in var.source_files : archive_name => local_path }

  archive_files = merge(local.dir_files, local.explicit_files)
}

# ---------------------------------------------------------------- pachet
data "archive_file" "this" {
  type        = "zip"
  output_path = "${path.module}/.build/${local.name}.zip"

  dynamic "source" {
    for_each = local.archive_files

    content {
      content  = file(source.value)
      filename = source.key
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
  # Politica poate fi cunoscută abia la apply (depinde de ieșirile altor module), iar `count`
  # trebuie să fie cunoscut la plan. Resursa se creează mereu; fără politică, rămâne goală.
  name   = "${local.name}-inline"
  role   = aws_iam_role.this.id
  policy = var.policy_json == "" ? jsonencode({ Version = "2012-10-17", Statement = [] }) : var.policy_json
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
