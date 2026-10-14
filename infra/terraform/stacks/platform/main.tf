data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Repository  = "cnc-digital-twin"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  # Prefixul topicurilor de control trebuie să coincidă cu cel din modulul de ingestie,
  # altfel politica certificatului nu permite simulării să asculte comenzile.
  control_prefix = var.control_topic_prefix == "" ? "${var.project_name}/${var.environment}" : var.control_topic_prefix

  qa_verdict_topic = "${local.control_prefix}/cnc/cell-a/qa-verdict"
  command_topic    = "${local.control_prefix}/cnc/cell-a/command"
  defect_topic     = "${local.control_prefix}/cnc/cell-a/chaos"

  # Numele endpoint-urilor sunt deterministice: pot fi transmise Lambdăi fără a crea
  # o dependență circulară față de resursa de endpoint.
  pm_endpoint_name = "${var.project_name}-${var.environment}-predictive-maintenance"
  qa_endpoint_name = "${var.project_name}-${var.environment}-vision-qa"

  lambdas_dir = "${path.root}/../../../../lambdas"
  # `cncdt_ml` intră în arhiva Lambda-ului de feature engineering, ca hot path-ul și
  # antrenarea să folosească exact aceleași definiții de caracteristici.
  ml_src_dir = "${path.root}/../../../../ml/src"

  telemetry_namespace = "CNCDT/Telemetry"

  # EFS-ul de home este creat de SageMaker, deci ID-ul se citește din domeniu.
  # `one()` întoarce null când domeniul nu există, fără să evalueze un index invalid.
  studio_efs_id = one(module.sagemaker_studio[*].home_efs_file_system_id)

  # Domeniul păstrează EFS-ul la ștergere (`retention_policy = Retain`), deci backup-ul
  # este singurul lucru care apără notebook-urile de o ștergere accidentală a domeniului.
  studio_efs_arn = var.enable_studio_backup && local.studio_efs_id != null ? format(
    "arn:aws:elasticfilesystem:%s:%s:file-system/%s",
    local.region, local.account_id, local.studio_efs_id,
  ) : ""

  backup_vault_name = coalesce(one(module.studio_backup[*].vault_name), "")
}

# ---------------------------------------------------------------- stocare
# Glue Data Catalog apare aici doar ca registru de schemă (vezi modulul de ingestie).
# Nu există job-uri Glue, crawlere sau transformări Glue în acest proiect.
module "storage" {
  source = "../../modules/platform-storage"

  project_name  = var.project_name
  environment   = var.environment
  account_id    = local.account_id
  kms_key_arn   = var.kms_key_arn
  force_destroy = var.force_destroy_buckets

  tags = var.tags
}

# ---------------------------------------------------------------- cozi de comenzi
# Panoul de control (operator sau UI de demo) publică aici comenzi de tip
# „injectează defect", „oprește ciclul", „reia producția".
resource "aws_sqs_queue" "commands_dlq" {
  name                      = "${var.project_name}-${var.environment}-commands-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = local.common_tags
}

resource "aws_sqs_queue" "commands" {
  name                       = "${var.project_name}-${var.environment}-commands"
  message_retention_seconds  = 345600
  visibility_timeout_seconds = 120
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.commands_dlq.arn
    maxReceiveCount     = 5
  })

  tags = local.common_tags
}

# ---------------------------------------------------------------- Lambda: feature engineering
# Rulează în Firehose la fiecare fereastră de 60 de secunde. Calculează caracteristicile
# pe fereastră, cheamă modelul de anomalii când este disponibil și publică metricele.
data "aws_iam_policy_document" "feature_engineering_inline" {
  statement {
    sid       = "PublishMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    # PutMetricData nu acceptă resurse la nivel de ARN, deci `*` este obligatoriu; condiția
    # limitează scrierea la namespace-ul propriu, patternul documentat de AWS.
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [local.telemetry_namespace]
    }
  }

  dynamic "statement" {
    for_each = var.enable_predictive_maintenance_endpoint ? [1] : []

    content {
      sid       = "InvokeAnomalyModel"
      effect    = "Allow"
      actions   = ["sagemaker:InvokeEndpoint", "sagemaker:InvokeEndpointAsync"]
      resources = ["arn:aws:sagemaker:${local.region}:${local.account_id}:endpoint/${local.pm_endpoint_name}"]
    }
  }

  statement {
    sid    = "WriteRejectedRecords"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
    ]

    resources = ["${module.storage.data_lake_bucket_arn}/replay/manual/*"]
  }

  statement {
    sid    = "Logs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["*"]
  }
}

module "lambda_feature_engineering" {
  source = "../../modules/lambda-function"

  name_prefix   = "${var.project_name}-${var.environment}"
  function_name = "feature-engineering"
  source_dirs = [
    "${local.lambdas_dir}/feature_engineering",
    "${local.lambdas_dir}/_shared",
    "${local.ml_src_dir}/cncdt_ml",
  ]

  handler               = "handler.lambda_handler"
  architecture          = var.lambda_architecture
  memory_mb             = 1024
  timeout_seconds       = 120
  log_retention_days    = var.lambda_log_retention_days
  policy_json           = data.aws_iam_policy_document.feature_engineering_inline.json
  error_alarm_threshold = 5

  environment_variables = {
    TELEMETRY_NAMESPACE     = local.telemetry_namespace
    SAGEMAKER_ENDPOINT_NAME = var.enable_predictive_maintenance_endpoint ? local.pm_endpoint_name : ""
    MODEL_VERSION           = lookup(var.model_versions, "predictive_maintenance", "unversioned")
    ANOMALY_SCORE_THRESHOLD = "0.65"
    DATA_LAKE_BUCKET        = module.storage.data_lake_bucket_name
    # Pantele degradării din simulare, din care se estimează RUL publicat în CloudWatch.
    # Se schimbă în același commit cu `.env` (CNCDT_TOOL_WEAR_RATE, CNCDT_JOINT_FRICTION_RATE).
    TOOL_WEAR_RATE      = tostring(lookup(var.degradation_rates, "cnc", 0))
    JOINT_FRICTION_RATE = tostring(lookup(var.degradation_rates, "robot", 0))
    # Caracteristicile brute nu se publică în CloudWatch: nu le citește niciun widget sau
    # alarmă, iar fiecare metrică custom costă lunar. Se pornește doar pentru analiză.
    PUBLISH_FEATURE_METRICS = "false"
  }

  tags = var.tags
}

# ---------------------------------------------------------------- Lambda: inspecție vizuală
# Declanșată de încărcarea unui cadru QA în S3. Cheamă modelul de viziune și publică
# verdictul PASS/REJECT pe topicul de control, de unde simularea acționează pistonul.
data "aws_iam_policy_document" "qa_vision_inline" {
  statement {
    sid    = "ReadSnapshots"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]

    resources = ["${module.storage.data_lake_bucket_arn}/raw/qa-snapshots/*"]
  }

  statement {
    sid    = "WriteVerdicts"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
    ]

    resources = ["${module.storage.data_lake_bucket_arn}/curated/qa-verdicts/*"]
  }

  dynamic "statement" {
    for_each = var.vision_qa_endpoint_enabled ? [1] : []

    content {
      sid       = "InvokeVisionModel"
      effect    = "Allow"
      actions   = ["sagemaker:InvokeEndpoint"]
      resources = ["arn:aws:sagemaker:${local.region}:${local.account_id}:endpoint/${local.qa_endpoint_name}"]
    }
  }

  statement {
    sid       = "PublishControlEvent"
    effect    = "Allow"
    actions   = ["iot:Publish"]
    resources = ["arn:aws:iot:${local.region}:${local.account_id}:topic/${local.qa_verdict_topic}"]
  }

  statement {
    sid       = "PublishMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }
}

module "lambda_qa_vision" {
  source = "../../modules/lambda-function"

  name_prefix   = "${var.project_name}-${var.environment}"
  function_name = "qa-vision-inference"
  source_dirs = [
    "${local.lambdas_dir}/qa_vision_inference",
    "${local.lambdas_dir}/_shared",
  ]

  handler            = "handler.lambda_handler"
  architecture       = var.lambda_architecture
  memory_mb          = 1024
  timeout_seconds    = 60
  log_retention_days = var.lambda_log_retention_days
  policy_json        = data.aws_iam_policy_document.qa_vision_inline.json

  environment_variables = {
    TELEMETRY_NAMESPACE     = local.telemetry_namespace
    SAGEMAKER_ENDPOINT_NAME = var.vision_qa_endpoint_enabled ? local.qa_endpoint_name : ""
    MODEL_VERSION           = lookup(var.model_versions, "vision_qa", "unversioned")
    CONTROL_TOPIC           = local.qa_verdict_topic
    DEFECT_THRESHOLD        = "0.5"
    DATA_LAKE_BUCKET        = module.storage.data_lake_bucket_name
    IOT_DATA_ENDPOINT       = module.iot_ingestion.iot_data_endpoint
    # Fotografia de la zona lamei/piston pleacă spre endpoint; oprit, verdictul se ia doar
    # din vectorul de caracteristici.
    SEND_IMAGE_TO_ENDPOINT = "true"
  }

  tags = var.tags
}

resource "aws_lambda_permission" "allow_s3_qa_snapshots" {
  statement_id  = "AllowInvokeFromQaSnapshotsBucket"
  action        = "lambda:InvokeFunction"
  function_name = module.lambda_qa_vision.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = module.storage.data_lake_bucket_arn
}

resource "aws_s3_bucket_notification" "qa_snapshots" {
  bucket = module.storage.data_lake_bucket_name

  lambda_function {
    id                  = "qa-snapshot-created"
    lambda_function_arn = module.lambda_qa_vision.function_arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/qa-snapshots/"
    filter_suffix       = ".json"
  }

  depends_on = [aws_lambda_permission.allow_s3_qa_snapshots]
}

# ---------------------------------------------------------------- Lambda: dispecer de comenzi
# Consumă comenzi din SQS și le publică pe topicul de control către simulare.
data "aws_iam_policy_document" "control_dispatcher_inline" {
  statement {
    sid       = "ConsumeCommands"
    effect    = "Allow"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"]
    resources = [aws_sqs_queue.commands.arn]
  }

  statement {
    sid     = "PublishCommands"
    effect  = "Allow"
    actions = ["iot:Publish"]
    resources = [
      "arn:aws:iot:${local.region}:${local.account_id}:topic/${local.command_topic}",
      "arn:aws:iot:${local.region}:${local.account_id}:topic/${local.defect_topic}",
    ]
  }

  statement {
    sid    = "AuditTrail"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
    ]

    resources = ["${module.storage.data_lake_bucket_arn}/curated/control-audit/*"]
  }
}

module "lambda_control_dispatcher" {
  source = "../../modules/lambda-function"

  name_prefix   = "${var.project_name}-${var.environment}"
  function_name = "control-dispatcher"
  source_dirs = [
    "${local.lambdas_dir}/control_dispatcher",
    "${local.lambdas_dir}/_shared",
  ]

  handler            = "handler.lambda_handler"
  architecture       = var.lambda_architecture
  memory_mb          = 256
  timeout_seconds    = 30
  log_retention_days = var.lambda_log_retention_days
  policy_json        = data.aws_iam_policy_document.control_dispatcher_inline.json

  environment_variables = {
    CONTROL_TOPIC     = local.command_topic
    CHAOS_TOPIC       = local.defect_topic
    AUDIT_PREFIX      = "curated/control-audit"
    DATA_LAKE_BUCKET  = module.storage.data_lake_bucket_name
    IOT_DATA_ENDPOINT = module.iot_ingestion.iot_data_endpoint
  }

  tags = var.tags
}

resource "aws_lambda_event_source_mapping" "commands" {
  event_source_arn                   = aws_sqs_queue.commands.arn
  function_name                      = module.lambda_control_dispatcher.function_arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]

  depends_on = [module.lambda_control_dispatcher]
}

# ---------------------------------------------------------------- ingestie
module "iot_ingestion" {
  source = "../../modules/iot-ingestion"

  project_name                 = var.project_name
  environment                  = var.environment
  account_id                   = local.account_id
  region                       = local.region
  telemetry_topic              = var.telemetry_topic
  control_topic_prefix         = local.control_prefix
  thing_name                   = var.iot_thing_name
  create_thing_and_certificate = var.create_iot_thing_and_certificate

  data_lake_bucket_name = module.storage.data_lake_bucket_name
  data_lake_bucket_arn  = module.storage.data_lake_bucket_arn
  logs_bucket_name      = module.storage.logs_bucket_name
  logs_bucket_arn       = module.storage.logs_bucket_arn
  glue_database_name    = module.storage.glue_database_name

  transform_lambda_arn  = module.lambda_feature_engineering.function_arn
  transform_lambda_name = module.lambda_feature_engineering.function_name

  buffering_interval_seconds = var.firehose_buffering_interval
  enable_backup              = var.firehose_enable_backup
  log_retention_days         = var.lambda_log_retention_days

  tags = var.tags
}

# ---------------------------------------------------------------- SageMaker Studio
module "sagemaker_studio" {
  source = "../../modules/sagemaker-studio"
  count  = var.enable_sagemaker_studio ? 1 : 0

  project_name                 = var.project_name
  environment                  = var.environment
  region                       = local.region
  vpc_id                       = var.sagemaker_vpc_id
  subnet_ids                   = var.sagemaker_subnet_ids
  user_profiles                = var.sagemaker_user_profiles
  default_kernel_instance_type = var.sagemaker_kernel_instance_type
  training_instance_types      = var.sagemaker_training_instance_types
  enable_idle_shutdown         = var.enable_studio_idle_shutdown
  data_lake_bucket_arn         = module.storage.data_lake_bucket_arn
  assets_bucket_arn            = module.storage.assets_bucket_arn
  kms_key_arn                  = var.kms_key_arn

  tags = var.tags
}

# ---------------------------------------------------------------- endpoint-uri ML
module "endpoint_predictive_maintenance" {
  source = "../../modules/sagemaker-endpoint"
  count  = var.enable_predictive_maintenance_endpoint ? 1 : 0

  project_name               = var.project_name
  environment                = var.environment
  region                     = local.region
  model_key                  = "predictive-maintenance"
  model_data_url             = var.predictive_maintenance_model_data_url
  deployment_mode            = var.ml_endpoint_deployment_mode
  serverless_memory_mb       = var.ml_serverless_memory_mb
  serverless_max_concurrency = var.ml_serverless_max_concurrency

  prebuilt_image_repository = "sagemaker-scikit-learn"
  prebuilt_image_tag        = "1.2-1-cpu-py3"

  enable_data_capture      = true
  data_capture_bucket_name = module.storage.data_lake_bucket_name
  data_capture_prefix      = "inference-logs/predictive-maintenance"
  model_data_bucket_arn    = module.storage.data_lake_bucket_arn
  kms_key_id               = var.kms_key_arn

  container_environment = {
    ANOMALY_THRESHOLD = "0.65"
    MODEL_VERSION     = lookup(var.model_versions, "predictive_maintenance", "unversioned")
  }

  tags = var.tags
}

module "endpoint_vision_qa" {
  source = "../../modules/sagemaker-endpoint"
  count  = var.vision_qa_endpoint_enabled ? 1 : 0

  project_name               = var.project_name
  environment                = var.environment
  region                     = local.region
  model_key                  = "vision-qa"
  model_data_url             = var.vision_qa_model_data_url
  deployment_mode            = var.ml_endpoint_deployment_mode
  serverless_memory_mb       = var.ml_serverless_memory_mb
  serverless_max_concurrency = var.ml_serverless_max_concurrency

  prebuilt_image_repository = var.vision_qa_prebuilt_image_repository
  prebuilt_image_tag        = var.vision_qa_prebuilt_image_tag

  enable_data_capture      = true
  data_capture_bucket_name = module.storage.data_lake_bucket_name
  data_capture_prefix      = "inference-logs/vision-qa"
  model_data_bucket_arn    = module.storage.data_lake_bucket_arn
  kms_key_id               = var.kms_key_arn

  container_environment = {
    DEFECT_THRESHOLD = "0.5"
    MODEL_VERSION    = lookup(var.model_versions, "vision_qa", "unversioned")
  }

  tags = var.tags
}

# ---------------------------------------------------------------- backup EFS Studio
module "studio_backup" {
  source = "../../modules/backup"
  count  = local.studio_efs_arn == "" ? 0 : 1

  project_name   = var.project_name
  environment    = var.environment
  selection_name = "studio-home-efs"
  resource_arns  = [local.studio_efs_arn]

  schedule                = var.studio_backup_schedule
  retention_days          = var.studio_backup_retention_days
  cold_storage_after_days = var.studio_backup_cold_storage_days
  force_destroy           = var.studio_backup_force_destroy
  kms_key_arn             = var.kms_key_arn

  tags = var.tags
}

# ---------------------------------------------------------------- buget de cost
# Singurul control care prinde cheltuiala scăpată de alarmele tehnice.
module "budget" {
  source = "../../modules/budget"
  count  = var.enable_cost_budget ? 1 : 0

  project_name     = var.project_name
  environment      = var.environment
  limit_amount_usd = var.monthly_budget_usd
  email_endpoints  = var.alarm_email_endpoints
  scope_to_project = var.budget_scope_to_project

  tags = var.tags
}

# ---------------------------------------------------------------- observabilitate
module "observability" {
  source = "../../modules/observability"

  project_name = var.project_name
  environment  = var.environment
  region       = local.region
  account_id   = local.account_id

  alarm_email_endpoints = var.alarm_email_endpoints
  alarm_sms_numbers     = var.alarm_sms_numbers
  degradation_elements  = var.degradation_elements

  firehose_stream_name          = module.iot_ingestion.firehose_stream_name
  iot_rule_name                 = module.iot_ingestion.topic_rule_name
  sim_instance_id               = var.sim_instance_id
  feature_engineering_log_group = module.lambda_feature_engineering.log_group_name

  lambda_function_names = [
    module.lambda_feature_engineering.function_name,
    module.lambda_qa_vision.function_name,
    module.lambda_control_dispatcher.function_name,
  ]

  sagemaker_endpoint_names = compact([
    var.enable_predictive_maintenance_endpoint ? local.pm_endpoint_name : "",
    var.vision_qa_endpoint_enabled ? local.qa_endpoint_name : "",
  ])

  anomaly_alarm_threshold = var.anomaly_threshold
  backup_vault_name       = local.backup_vault_name

  # Vizualizarea este exclusiv CloudWatch; linkul duce la notebook-ul de analiză.
  dashboard_anomaly_widget_link = var.dashboard_anomaly_widget_link

  tags = var.tags
}
