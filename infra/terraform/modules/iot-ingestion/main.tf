locals {
  name = "${var.project_name}-${var.environment}-ingest"
  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Component   = "ingestion"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  control_prefix = var.control_topic_prefix == "" ? "${var.project_name}/${var.environment}" : var.control_topic_prefix

  # Prefixul S3 construit din cheile de partiționare emise de Lambda. Lambda-ul întoarce
  # `metadata.partitionKeys`, deci prefixul folosește `partitionKeyFromLambda`; varianta
  # `partitionKeyFromQuery` ar cere în plus un procesor de extragere din JSON.
  s3_prefix = "raw/telemetry/dt_year=!{partitionKeyFromLambda:dt_year}/dt_month=!{partitionKeyFromLambda:dt_month}/dt_day=!{partitionKeyFromLambda:dt_day}/dt_hour=!{partitionKeyFromLambda:dt_hour}/"

  firehose_stream_arn = "arn:aws:firehose:${var.region}:${var.account_id}:deliverystream/${local.name}"
}

# ---------------------------------------------------------------- lucru IoT și certificat
resource "aws_iot_thing_type" "cnc_cell" {
  count = var.create_thing_and_certificate ? 1 : 0

  name = "${var.project_name}-cnc-cell"

  properties {
    # IoT Core acceptă doar ASCII imprimabil în descriere; diacriticele sunt respinse.
    description           = "Celula CNC simulata (Isaac Sim) cu senzori si camera de inspectie."
    searchable_attributes = ["cell_id", "site", "sim_version"]
  }

  tags = local.tags
}

resource "aws_iot_thing" "cell" {
  count = var.create_thing_and_certificate ? 1 : 0

  name            = var.thing_name
  thing_type_name = aws_iot_thing_type.cnc_cell[0].name

  attributes = {
    cell_id      = "cell-a"
    site         = "simulation"
    sensor_set   = "vibration,spindle_load,coolant,temperature,joint_effort"
    provisioning = "terraform"
  }
}

resource "aws_iot_thing_group" "cells" {
  count = var.create_thing_and_certificate ? 1 : 0

  name              = "${var.project_name}-${var.environment}-cells"
  parent_group_name = null

  properties {
    # Aceeași restricție ca la tipul de thing: numai ASCII imprimabil.
    description = "Grupul celulelor CNC simulate in ${var.environment}."
    attribute_payload {
      attributes = {
        environment = var.environment
      }
    }
  }

  tags = local.tags
}

resource "aws_iot_thing_group_membership" "cell" {
  count = var.create_thing_and_certificate ? 1 : 0

  thing_name       = aws_iot_thing.cell[0].name
  thing_group_name = aws_iot_thing_group.cells[0].name
}

resource "aws_iot_certificate" "cell" {
  count = var.create_thing_and_certificate ? 1 : 0

  active = true
}

data "aws_iam_policy_document" "iot_device_policy" {
  # Dispozitivul se poate conecta doar cu propriul nume de client.
  statement {
    sid       = "Connect"
    effect    = "Allow"
    actions   = ["iot:Connect"]
    resources = ["arn:aws:iot:${var.region}:${var.account_id}:client/${var.thing_name}"]
  }

  statement {
    sid       = "PublishTelemetry"
    effect    = "Allow"
    actions   = ["iot:Publish"]
    resources = ["arn:aws:iot:${var.region}:${var.account_id}:topic/${var.project_name}/${var.environment}/*"]
  }

  # Simularea ascultă comenzile de control venite din pipeline (pistonul de rejecție).
  statement {
    sid    = "SubscribeControl"
    effect = "Allow"

    actions = [
      "iot:Subscribe",
      "iot:Receive",
    ]

    resources = [
      "arn:aws:iot:${var.region}:${var.account_id}:topicfilter/${local.control_prefix}/*",
      "arn:aws:iot:${var.region}:${var.account_id}:topic/${local.control_prefix}/*",
    ]
  }
}

resource "aws_iot_policy" "cell" {
  count = var.create_thing_and_certificate ? 1 : 0

  name   = "${local.name}-device-policy"
  policy = data.aws_iam_policy_document.iot_device_policy.json

  tags = local.tags
}

resource "aws_iot_policy_attachment" "cell" {
  count = var.create_thing_and_certificate ? 1 : 0

  policy = aws_iot_policy.cell[0].name
  target = aws_iot_certificate.cell[0].arn
}

# ---------------------------------------------------------------- roluri IAM
data "aws_iam_policy_document" "assume_iot_rule" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["iot.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "iot_rule" {
  name               = "${local.name}-iot-rule-role"
  assume_role_policy = data.aws_iam_policy_document.assume_iot_rule.json
  description        = "Permite regulii IoT sa livreze catre Firehose si sa scrie loguri."

  tags = local.tags
}

data "aws_iam_policy_document" "iot_rule_inline" {
  statement {
    sid       = "DeliverToFirehose"
    effect    = "Allow"
    actions   = ["firehose:PutRecord", "firehose:PutRecordBatch"]
    resources = [local.firehose_stream_arn]
  }

  statement {
    sid    = "WriteRuleErrorLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]

    resources = ["${aws_cloudwatch_log_group.iot_rule.arn}:*"]
  }
}

resource "aws_iam_role_policy" "iot_rule_inline" {
  name   = "${local.name}-iot-rule-inline"
  role   = aws_iam_role.iot_rule.id
  policy = data.aws_iam_policy_document.iot_rule_inline.json
}

data "aws_iam_policy_document" "assume_firehose" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }

    # Constrângere suplimentară: doar stream-uri din acest cont.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
  }
}

resource "aws_iam_role" "firehose" {
  name               = "${local.name}-firehose-role"
  assume_role_policy = data.aws_iam_policy_document.assume_firehose.json
  description        = "Permite Firehose sa scrie in S3, sa cheme Lambda si sa citeasca catalogul Glue."

  tags = local.tags
}

data "aws_iam_policy_document" "firehose_inline" {
  statement {
    sid    = "WriteDataToS3"
    effect = "Allow"

    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetBucketLocation",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:PutObject",
    ]

    resources = [
      var.data_lake_bucket_arn,
      "${var.data_lake_bucket_arn}/*",
      var.logs_bucket_arn,
      "${var.logs_bucket_arn}/*",
    ]
  }

  statement {
    sid       = "InvokeTransform"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction", "lambda:GetFunctionConfiguration"]
    resources = [var.transform_lambda_arn, "${var.transform_lambda_arn}:*"]
  }

  statement {
    sid    = "ReadGlueCatalog"
    effect = "Allow"

    actions = [
      "glue:GetTable",
      "glue:GetTableVersion",
      "glue:GetTableVersions",
      "glue:GetDatabase",
    ]

    resources = [
      "arn:aws:glue:${var.region}:${var.account_id}:catalog",
      "arn:aws:glue:${var.region}:${var.account_id}:database/${var.glue_database_name}",
      "arn:aws:glue:${var.region}:${var.account_id}:table/${var.glue_database_name}/${var.glue_table_name}",
    ]
  }

  statement {
    sid    = "WriteDeliveryLogs"
    effect = "Allow"

    actions = [
      "logs:PutLogEvents",
      "logs:CreateLogStream",
      "logs:DescribeLogStreams",
      "logs:DescribeLogGroups",
    ]

    resources = ["${aws_cloudwatch_log_group.firehose.arn}:*"]
  }
}

resource "aws_iam_role_policy" "firehose_inline" {
  name   = "${local.name}-firehose-inline"
  role   = aws_iam_role.firehose.id
  policy = data.aws_iam_policy_document.firehose_inline.json
}

# ---------------------------------------------------------------- loguri
resource "aws_cloudwatch_log_group" "firehose" {
  name              = "/aws/firehose/${local.name}"
  retention_in_days = var.log_retention_days

  tags = local.tags
}

resource "aws_cloudwatch_log_group" "iot_rule" {
  name              = "/aws/iot/rule/${local.name}"
  retention_in_days = var.log_retention_days

  tags = local.tags
}

# ---------------------------------------------------------------- catalog Glue
# Coloanele corespund înregistrării îmbogățite produse de Lambda-ul de transformare.
# Cheile de partiționare NU apar între coloane: Firehose le extrage din metadatele Lambda.
resource "aws_glue_catalog_table" "telemetry" {
  count = var.create_glue_table ? 1 : 0

  name          = var.glue_table_name
  database_name = var.glue_database_name
  description   = "Telemetrie CNC îmbogățită cu caracteristici pe fereastră și scor de anomalie."
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "classification" = "parquet"
    "typeOfData"     = "file"
    "EXTERNAL"       = "TRUE"
    # Partition projection: interogările Athena nu au nevoie de MSCK REPAIR sau crawler.
    "projection.enabled"        = "true"
    "projection.dt_year.type"   = "integer"
    "projection.dt_year.range"  = "2025,2035"
    "projection.dt_month.type"  = "integer"
    "projection.dt_month.range" = "1,12"
    "projection.dt_day.type"    = "integer"
    "projection.dt_day.range"   = "1,31"
    "projection.dt_hour.type"   = "integer"
    "projection.dt_hour.range"  = "0,23"
    "storage.location.template" = "s3://${var.data_lake_bucket_name}/raw/telemetry/dt_year=$${dt_year}/dt_month=$${dt_month}/dt_day=$${dt_day}/dt_hour=$${dt_hour}/"
  }

  # Cheile de partiționare sunt de tip string (cerință Firehose); proiecția Athena
  # le tratează ca întregi, deci interogările pot filtra pe intervale.
  partition_keys {
    name = "dt_year"
    type = "string"
  }
  partition_keys {
    name = "dt_month"
    type = "string"
  }
  partition_keys {
    name = "dt_day"
    type = "string"
  }
  partition_keys {
    name = "dt_hour"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${var.data_lake_bucket_name}/raw/telemetry/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "parquet"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name = "schema_version"
      type = "string"
    }
    columns {
      name = "device_id"
      type = "string"
    }
    columns {
      name = "cell_id"
      type = "string"
    }
    columns {
      name = "topic"
      type = "string"
    }
    columns {
      name = "ts_ms"
      type = "bigint"
    }
    columns {
      name = "received_at"
      type = "string"
    }
    columns {
      name = "window_s"
      type = "int"
    }
    columns {
      name = "sample_count"
      type = "int"
    }
    columns {
      name = "machine_state"
      type = "string"
    }
    columns {
      name = "cycle_id"
      type = "string"
    }
    columns {
      name = "part_id"
      type = "string"
    }
    columns {
      name = "spindle_load_mean"
      type = "double"
    }
    columns {
      name = "spindle_load_p95"
      type = "double"
    }
    columns {
      name = "spindle_load_std"
      type = "double"
    }
    columns {
      name = "vibration_rms"
      type = "double"
    }
    columns {
      name = "vibration_peak"
      type = "double"
    }
    columns {
      name = "vibration_std"
      type = "double"
    }
    columns {
      name = "tool_wear_index"
      type = "double"
    }
    columns {
      name = "coolant_flow_lpm"
      type = "double"
    }
    columns {
      name = "temp_c_mean"
      type = "double"
    }
    columns {
      name = "joint_friction_index"
      type = "double"
    }
    columns {
      name = "joint_friction_index_arm1"
      type = "double"
    }
    columns {
      name = "joint_friction_index_arm2"
      type = "double"
    }
    columns {
      name = "anomaly_score"
      type = "double"
    }
    columns {
      name = "anomaly_detected"
      type = "boolean"
    }
    columns {
      name = "model_version"
      type = "string"
    }
    columns {
      name = "is_synthetic_defect"
      type = "boolean"
    }
  }
}

# ---------------------------------------------------------------- Firehose
resource "aws_kinesis_firehose_delivery_stream" "telemetry" {
  name        = local.name
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn            = aws_iam_role.firehose.arn
    bucket_arn          = var.data_lake_bucket_arn
    prefix              = local.s3_prefix
    error_output_prefix = var.error_output_prefix

    buffering_size     = var.buffering_size_mb
    buffering_interval = var.buffering_interval_seconds

    # Conversia în Parquet impune compression_format = UNCOMPRESSED;
    # Firehose aplică singur Snappy la scrierea Parquet.
    compression_format = "UNCOMPRESSED"

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "Lambda"

        parameters {
          parameter_name  = "LambdaArn"
          parameter_value = "${var.transform_lambda_arn}:$LATEST"
        }
        parameters {
          parameter_name  = "BufferSizeInMBs"
          parameter_value = tostring(var.firehose_lambda_buffer_mb)
        }
        parameters {
          parameter_name  = "BufferIntervalInSeconds"
          parameter_value = tostring(var.firehose_lambda_buffer_seconds)
        }
      }
    }

    data_format_conversion_configuration {
      input_format_configuration {
        deserializer {
          # Regula IoT livrează obiecte JSON, câte unul pe linie.
          open_x_json_ser_de {
            convert_dots_in_json_keys_to_underscores = false
            case_insensitive                         = false
          }
        }
      }

      output_format_configuration {
        serializer {
          parquet_ser_de {
            compression = "SNAPPY"
          }
        }
      }

      # `version_id = LATEST` cere ultima versiune a schemei din catalogul Glue.
      schema_configuration {
        database_name = var.glue_database_name
        table_name    = var.glue_table_name
        role_arn      = aws_iam_role.firehose.arn
        version_id    = "LATEST"
      }
    }

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose.name
      log_stream_name = "S3Delivery"
    }

    dynamic "s3_backup_configuration" {
      for_each = var.enable_backup ? [1] : []

      content {
        role_arn           = aws_iam_role.firehose.arn
        bucket_arn         = var.logs_bucket_arn
        prefix             = "firehose-backup/${local.name}/"
        buffering_size     = 5
        buffering_interval = 300
        compression_format = "GZIP"
      }
    }
  }

  depends_on = [
    aws_iam_role_policy.firehose_inline,
    aws_glue_catalog_table.telemetry,
  ]

  tags = local.tags
}

# Firehose are nevoie de permisiune explicită ca să invoce funcția de transformare.
resource "aws_lambda_permission" "firehose_invoke" {
  statement_id   = "AllowExecutionFromFirehose"
  action         = "lambda:InvokeFunction"
  function_name  = var.transform_lambda_name
  principal      = "firehose.amazonaws.com"
  source_arn     = local.firehose_stream_arn
  source_account = var.account_id
}

# ---------------------------------------------------------------- regula IoT
resource "aws_iot_topic_rule" "telemetry" {
  name        = "${var.project_name}_${var.environment}_telemetry"
  description = "Direcționează telemetria CNC către Firehose pentru micro-batching și conversie Parquet."
  enabled     = true
  sql_version = "2016-03-23"
  sql         = "SELECT *, topic() AS mqtt_topic, timestamp() AS iot_timestamp FROM '${var.telemetry_topic}'"

  firehose {
    delivery_stream_name = aws_kinesis_firehose_delivery_stream.telemetry.name
    role_arn             = aws_iam_role.iot_rule.arn
    separator            = "\n"
  }

  error_action {
    cloudwatch_logs {
      log_group_name = aws_cloudwatch_log_group.iot_rule.name
      role_arn       = aws_iam_role.iot_rule.arn
    }
  }

  tags = local.tags
}

# ---------------------------------------------------------------- endpoint
data "aws_iot_endpoint" "data_ats" {
  endpoint_type = "iot:Data-ATS"
}
