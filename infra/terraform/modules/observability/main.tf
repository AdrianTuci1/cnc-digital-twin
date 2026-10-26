locals {
  name = "${var.project_name}-${var.environment}"

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Component   = "observability"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  # Două dashboard-uri, două audiențe: unul pentru infrastructura cloud, unul pentru datele
  # fabricii virtuale. Aceleași metrice pot apărea pe amândouă, dar nu amestecate în același
  # panou: cine caută o eroare de pipeline nu vrea să filtreze printre piese bune și rebut.
  dashboard_infra_name   = var.dashboard_infra_name != "" ? var.dashboard_infra_name : (var.dashboard_name != "" ? var.dashboard_name : "${local.name}-infra")
  dashboard_factory_name = var.dashboard_factory_name != "" ? var.dashboard_factory_name : "${local.name}-factory"
  sns_topic_arn          = var.create_sns_topic ? aws_sns_topic.alarms[0].arn : var.external_alarm_topic_arn

  # ARN-urile de alarmă sunt construite, nu referite: dashboard-ul nu trebuie să creeze
  # dependențe între module care ar putea deveni circulare.
  alarm_arn_prefix = "arn:aws:cloudwatch:${var.region}:${var.account_id}:alarm:"

  # Metricile proprii publicate de Lambda-ul de feature engineering.
  telemetry_namespace = "CNCDT/Telemetry"
  sim_namespace       = "CNCDT/Simulation"

  # Alertele de degradare au aceleași acțiuni: notificare la intrarea în alarmă și
  # notificare la revenirea în OK, după reparația din panoul Isaac Sim.
  alarm_actions = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  # Elementele de degradare, în ordinea în care apar ca panouri pe dashboard-ul de fabrică.
  factory_elements = keys(var.degradation_elements)
}

# ---------------------------------------------------------------- notificări
resource "aws_sns_topic" "alarms" {
  count = var.create_sns_topic ? 1 : 0

  name              = "${local.name}-alarms"
  kms_master_key_id = "alias/aws/sns"

  tags = local.tags
}

resource "aws_sns_topic_subscription" "email" {
  for_each = var.create_sns_topic ? toset(var.alarm_email_endpoints) : toset([])

  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = each.value
}

# SMS-ul este calea rapidă pentru o alarmă de fabrică: ajunge pe telefon în secunde, iar
# e-mailul rămâne urma cu toate detaliile. Contul SNS pornește în sandbox, deci numerele
# trebuie verificate până la cererea de acces de producție.
resource "aws_sns_topic_subscription" "sms" {
  for_each = var.create_sns_topic ? toset(var.alarm_sms_numbers) : toset([])

  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "sms"
  endpoint  = each.value
}

# ---------------------------------------------------------------- alarme de pipeline
resource "aws_cloudwatch_metric_alarm" "firehose_delivery_failures" {
  alarm_name          = "${local.name}-firehose-delivery-failures"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DeliveryToS3.DataFreshness"
  namespace           = "AWS/Firehose"
  period              = 600
  statistic           = "Maximum"
  threshold           = 300
  treat_missing_data  = "notBreaching"
  alarm_description   = "Firehose has not delivered data to S3 in the last 5 minutes."

  dimensions = {
    DeliveryStreamName = var.firehose_stream_name
  }

  alarm_actions = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]
  ok_actions    = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "firehose_throttled" {
  alarm_name          = "${local.name}-firehose-throttled"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ThrottledRecords"
  namespace           = "AWS/Firehose"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "Firehose throttled records: check the throughput limit or the Lambda invocation limit."

  dimensions = {
    DeliveryStreamName = var.firehose_stream_name
  }

  alarm_actions = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  tags = local.tags
}

# Filtrul de metrice numără ferestrele marcate ca anomale de model.
resource "aws_cloudwatch_log_metric_filter" "anomaly_detected" {
  count = var.enable_anomaly_metric_filters && var.feature_engineering_log_group != "" ? 1 : 0

  name           = "${local.name}-anomaly-detected"
  log_group_name = var.feature_engineering_log_group
  pattern        = "{ $.event = \"anomaly_detected\" && $.anomaly_detected = true }"

  metric_transformation {
    name          = "AnomalyDetectedWindows"
    namespace     = local.telemetry_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_metric_alarm" "process_anomaly" {
  alarm_name          = "${local.name}-process-anomaly"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "AnomalyDetectedWindows"
  namespace           = local.telemetry_namespace
  period              = 600
  statistic           = "Sum"
  threshold           = var.anomaly_alarm_threshold
  treat_missing_data  = "notBreaching"
  alarm_description   = "The predictive-maintenance model flagged repeated anomalous windows: possible tool wear or mechanical fault."

  alarm_actions = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]
  ok_actions    = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "endpoint_latency" {
  for_each = toset(var.sagemaker_endpoint_names)

  alarm_name          = "${local.name}-${each.value}-model-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "ModelLatency"
  namespace           = "AWS/SageMaker"
  period              = 300
  extended_statistic  = "p95"
  threshold           = 800000 # microsecunde: 800 ms
  treat_missing_data  = "notBreaching"
  alarm_description   = "The ${each.value} model p95 latency exceeds 800 ms: the control loop becomes too slow."

  dimensions = {
    EndpointName = each.value
    VariantName  = "AllTraffic"
  }

  alarm_actions = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  tags = local.tags
}

resource "aws_cloudwatch_metric_alarm" "endpoint_5xx" {
  for_each = toset(var.sagemaker_endpoint_names)

  alarm_name          = "${local.name}-${each.value}-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Invocation5XXErrors"
  namespace           = "AWS/SageMaker"
  period              = 300
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_description   = "The ${each.value} endpoint returns 5xx errors."

  dimensions = {
    EndpointName = each.value
    VariantName  = "AllTraffic"
  }

  alarm_actions = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  tags = local.tags
}

# ---------------------------------------------------------------- degradare
# Alarma urmărește metrica, iar metrica o resetează reparația din panoul MuJoCo
# (replace_tool / repair_joints / repair_all). De aceea alarma se stinge singură, în cel mult
# o perioadă de evaluare după intervenție, și trimite și notificarea de revenire în OK.
resource "aws_cloudwatch_metric_alarm" "degradation_index" {
  for_each = var.degradation_elements

  alarm_name          = "${local.name}-${each.key}-degradation"
  namespace           = local.telemetry_namespace
  metric_name         = "DegradationIndex"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = each.value.warn_threshold
  treat_missing_data  = "notBreaching"
  alarm_description   = "${each.value.label}: degradation index exceeded ${each.value.warn_threshold}. After service, choose 'Repair all' in the MuJoCo viewer; the alarm clears when the index drops below the threshold."

  # CloudWatch identifică o metrică prin setul complet de dimensiuni: fără DeviceId și CellId,
  # alarma ar căuta o serie care nu există și ar sta în OK pe date lipsă.
  dimensions = {
    DeviceId = var.telemetry_device_id
    CellId   = var.telemetry_cell_id
    Element  = each.key
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = local.tags
}

# Alarma predictivă: „mai sunt 10 minute până la pragul de avarie". Valoarea care a încălcat
# pragul apare în notificarea SNS, deci mesajul de pe telefon spune singur cât timp a rămas.
# RUL este în minute de operare continuă: cât timp celula stă, estimarea nu scade.
resource "aws_cloudwatch_metric_alarm" "degradation_rul" {
  for_each = { for key, element in var.degradation_elements : key => element if element.warn_rul_minutes > 0 }

  alarm_name          = "${local.name}-${each.key}-rul-low"
  namespace           = local.telemetry_namespace
  metric_name         = "RULMinutes"
  comparison_operator = "LessThanThreshold"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = each.value.warn_rul_minutes
  treat_missing_data  = "notBreaching"
  alarm_description   = "${each.value.label}: less than ${each.value.warn_rul_minutes} operating minutes until the failure threshold. Schedule service."

  dimensions = {
    DeviceId = var.telemetry_device_id
    CellId   = var.telemetry_cell_id
    Element  = each.key
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions

  tags = local.tags
}

# ---------------------------------------------------------------- backup
# AWS Backup publică metrice în namespace-ul AWS/Backup, pe dimensiunea seifului.
# O singură zi fără backup reușit este un semnal, deci pragul este 1.
resource "aws_cloudwatch_metric_alarm" "backup_jobs_failed" {
  count = var.backup_vault_name == "" ? 0 : 1

  alarm_name          = "${local.name}-backup-jobs-failed"
  namespace           = "AWS/Backup"
  metric_name         = "NumberOfBackupJobsFailed"
  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    BackupVaultName = var.backup_vault_name
  }

  alarm_description = "An AWS Backup job failed in the last 24 hours."
  alarm_actions     = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  tags = local.tags
}

# ---------------------------------------------------------------- dashboard-uri
# Două dashboard-uri, două audiențe. Cel de infrastructură răspunde la „merge pipeline-ul?",
# cel de fabrică la „cum arată celula?". Amestecate, ambele devin greu de citit.
resource "aws_cloudwatch_dashboard" "infra" {
  count = var.create_dashboard ? 1 : 0

  dashboard_name = local.dashboard_infra_name

  dashboard_body = jsonencode({
    widgets = concat(
      # Starea alarmelor de pipeline, primul lucru vizibil în demo.
      [
        {
          type   = "alarm"
          x      = 0
          y      = 0
          width  = 24
          height = 4
          properties = {
            title = "Infrastructure health"
            alarms = compact([
              "${local.alarm_arn_prefix}${local.name}-firehose-delivery-failures",
              "${local.alarm_arn_prefix}${local.name}-firehose-throttled",
              var.sim_instance_id == "" ? "" : "${local.alarm_arn_prefix}${local.name}-sim-status-check-failed",
              var.backup_vault_name == "" ? "" : "${local.alarm_arn_prefix}${local.name}-backup-jobs-failed",
            ])
          }
        },
      ],

      # Hot path: câte înregistrări intră și cât de repede sunt procesate.
      [
        {
          type   = "metric"
          x      = 0
          y      = 4
          width  = 12
          height = 6
          properties = {
            title  = "IoT ingestion and Firehose micro-batching"
            region = var.region
            view   = "timeSeries"
            stat   = "Sum"
            period = 60
            metrics = [
              ["AWS/Firehose", "IncomingRecords", "DeliveryStreamName", var.firehose_stream_name],
              ["AWS/Firehose", "IncomingBytes", "DeliveryStreamName", var.firehose_stream_name],
              ["AWS/Firehose", "DeliveryToS3.Records", "DeliveryStreamName", var.firehose_stream_name],
            ]
            yAxis = { left = { label = "records", showUnits = false } }
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 4
          width  = 12
          height = 6
          properties = {
            title  = "Hot path Lambdas"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = concat(
              [for fn in var.lambda_function_names : ["AWS/Lambda", "Invocations", "FunctionName", fn, { stat = "Sum" }]],
              [for fn in var.lambda_function_names : ["AWS/Lambda", "Errors", "FunctionName", fn, { stat = "Sum", color = "#d62728" }]],
              [for fn in var.lambda_function_names : ["AWS/Lambda", "Duration", "FunctionName", fn, { stat = "p95", yAxis = "right" }]],
            )
            yAxis = {
              left  = { label = "invocations, errors", showUnits = false }
              right = { label = "duration p95 (ms)", showUnits = false }
            }
          }
        },
      ],

      # ML: volumul de inferență și latența endpoint-urilor.
      [
        {
          type   = "metric"
          x      = 0
          y      = 10
          width  = 12
          height = 6
          properties = {
            title  = "SageMaker endpoints"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = concat(
              [for ep in var.sagemaker_endpoint_names : ["AWS/SageMaker", "Invocations", "EndpointName", ep, "VariantName", "AllTraffic", { stat = "Sum" }]],
              [for ep in var.sagemaker_endpoint_names : ["AWS/SageMaker", "ModelLatency", "EndpointName", ep, "VariantName", "AllTraffic", { stat = "p95", yAxis = "right" }]],
              [for ep in var.sagemaker_endpoint_names : ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", ep, "VariantName", "AllTraffic", { stat = "Sum", color = "#d62728" }]],
            )
            yAxis = {
              left  = { label = "invocations", showUnits = false }
              right = { label = "latency (µs)", showUnits = false }
            }
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 10
          width  = 12
          height = 6
          properties = {
            title  = "Data pipeline health"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = [
              # Dimensiunile trebuie să fie exact cele publicate de Lambda: altfel seria nu
              # există și panoul rămâne gol deși datele ajung.
              [local.telemetry_namespace, "SamplesAggregated", "DeviceId", var.telemetry_device_id, "CellId", var.telemetry_cell_id, { stat = "Sum" }],
              [local.telemetry_namespace, "WindowsProcessed", "Stage", "aggregate", { stat = "Sum" }],
              [local.telemetry_namespace, "EndpointFailures", { stat = "Sum", color = "#d62728" }],
              [local.telemetry_namespace, "QaEndpointFailures", { stat = "Sum", color = "#ff7f0e" }],
              # Cadre rămase fără verdict fiindcă nu există model. Verdictul nu se inventează
              # local, deci lipsa modelului trebuie să fie vizibilă, nu tăcută.
              [local.telemetry_namespace, "QaModelMissing", { stat = "Sum", color = "#9467bd" }],
            ]
            yAxis = { left = { label = "per minute", showUnits = false } }
          }
        },
      ],

      # Simulare: GPU și memorie, ca să se vadă costul real al instanței.
      var.sim_instance_id == "" ? [] : [
        {
          type   = "metric"
          x      = 0
          y      = 16
          width  = 12
          height = 6
          properties = {
            title  = "Simulation instance (GPU)"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = [
              [local.sim_namespace, "nvidia_smi_utilization_gpu", "InstanceId", var.sim_instance_id],
              [local.sim_namespace, "nvidia_smi_memory_used", "InstanceId", var.sim_instance_id],
              [local.sim_namespace, "nvidia_smi_temperature_gpu", "InstanceId", var.sim_instance_id, { yAxis = "right" }],
              ["AWS/EC2", "CPUUtilization", "InstanceId", var.sim_instance_id],
            ]
          }
        },
      ],

      # Erori recente, ca să nu fie nevoie de căutare manuală în loguri.
      [
        {
          type   = "log"
          x      = 12
          y      = 16
          width  = 12
          height = 6
          properties = {
            title  = "Recent pipeline errors"
            region = var.region
            view   = "table"
            query  = "SOURCE '/aws/lambda/${var.project_name}-${var.environment}-feature-engineering' | fields @timestamp, level, message, cycle_id\n| filter level = \"ERROR\" or level = \"WARNING\"\n| sort @timestamp desc\n| limit 25"
          }
        },
      ],

      # Link opțional către notebook-ul de analiză. Vizualizarea rămâne în CloudWatch:
      # linkul duce la analiza din Studio, nu introduce un al doilea instrument de BI.
      var.dashboard_anomaly_widget_link == "" ? [] : [
        {
          type   = "text"
          x      = 0
          y      = 22
          width  = 24
          height = 2
          properties = {
            markdown = "### Analysis\n[Open the analysis notebook](${var.dashboard_anomaly_widget_link})"
          }
        },
      ],
    )
  })
}

# Dashboard-ul operațional: doar datele celulei virtuale. Aici nu se vede nicio metrică de
# serviciu AWS, ca un operator de fabrică să nu filtreze printre Firehose și Lambda.
resource "aws_cloudwatch_dashboard" "factory" {
  count = var.create_dashboard ? 1 : 0

  dashboard_name = local.dashboard_factory_name

  dashboard_body = jsonencode({
    widgets = concat(
      # Degradarea și timpul rămas: alarmele care interesează celula.
      [
        {
          type   = "alarm"
          x      = 0
          y      = 0
          width  = 24
          height = 4
          properties = {
            title = "Cell health"
            alarms = compact(concat(
              [for key, element in var.degradation_elements : "${local.alarm_arn_prefix}${local.name}-${key}-degradation"],
              [for key, element in var.degradation_elements : element.warn_rul_minutes > 0 ? "${local.alarm_arn_prefix}${local.name}-${key}-rul-low" : ""],
            ))
          }
        },
      ],

      # Datele celulei: câte eșantioane ajung și câte ferestre închide Lambda-ul pe minut.
      # Aici se vede dacă geamănul trimite; o linie plată înseamnă că pipeline-ul nu are date.
      [
        {
          type   = "metric"
          x      = 0
          y      = 4
          width  = 12
          height = 6
          properties = {
            title  = "Data reaching CloudWatch"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = [
              [local.telemetry_namespace, "SamplesAggregated", "DeviceId", var.telemetry_device_id, "CellId", var.telemetry_cell_id, { stat = "Sum", label = "samples/min" }],
              [local.telemetry_namespace, "WindowsProcessed", "Stage", "aggregate", { stat = "Sum", label = "windows/min", yAxis = "right" }],
            ]
            yAxis = {
              left  = { label = "samples per minute", showUnits = false }
              right = { label = "windows per minute", showUnits = false }
            }
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 4
          width  = 12
          height = 6
          properties = {
            title  = "Time to failure threshold (operating minutes)"
            region = var.region
            view   = "timeSeries"
            period = 60
            stat   = "Minimum"
            metrics = [
              for key in local.factory_elements :
              [local.telemetry_namespace, "RULMinutes", "DeviceId", var.telemetry_device_id, "CellId", var.telemetry_cell_id, "Element", key, { label = key }]
            ]
            yAxis = { left = { label = "minutes", min = 0, showUnits = false } }
            annotations = length(var.degradation_elements) == 0 ? {} : {
              horizontal = [
                {
                  label = "service threshold"
                  value = min([for element in values(var.degradation_elements) : element.warn_rul_minutes]...)
                  fill  = "below"
                },
              ]
            }
          }
        },
      ],

      # Câte un ceas per element: indicele de degradare, cu pragul de alarmă marcat.
      [for index, key in local.factory_elements : {
        type   = "metric"
        x      = index * 8
        y      = 10
        width  = 8
        height = 6
        properties = {
          title  = "Degradation: ${var.degradation_elements[key].label}"
          region = var.region
          view   = "gauge"
          period = 60
          stat   = "Maximum"
          metrics = [
            [local.telemetry_namespace, "DegradationIndex", "DeviceId", var.telemetry_device_id, "CellId", var.telemetry_cell_id, "Element", key, { label = key }],
          ]
          yAxis = { left = { min = 0, max = 1, label = "index", showUnits = false } }
          annotations = {
            horizontal = [
              { label = "alarm threshold", value = var.degradation_elements[key].warn_threshold, fill = "above" },
            ]
          }
        }
      }],

      # Ferestrele marcate de model, plus scorul care le-a produs. Pragul scris în titlu este
      # cel al alarmei, care adună 10 minute; panoul arată suma pe minut, ca să reacționeze
      # imediat în demo.
      [
        {
          type   = "metric"
          x      = 0
          y      = 16
          width  = 24
          height = 6
          properties = {
            title  = "Model-detected anomalies (alarm: ${var.anomaly_alarm_threshold} windows in 10 minutes)"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = [
              [local.telemetry_namespace, "AnomalyDetectedWindows", { stat = "Sum", label = "anomalous windows", color = "#d62728" }],
              [local.telemetry_namespace, "AnomalyScore", "DeviceId", var.telemetry_device_id, "CellId", var.telemetry_cell_id, { stat = "Average", label = "score", yAxis = "right" }],
            ]
            yAxis = {
              left  = { label = "windows per minute", min = 0, showUnits = false }
              right = { label = "score", showUnits = false }
            }
          }
        },
      ],

      [
        {
          type   = "text"
          x      = 0
          y      = 22
          width  = 24
          height = 2
          properties = {
            markdown = join("\n", [
              "### How to read the panels",
              "**Data reaching CloudWatch** — samples aggregated and windows closed per minute. A flat line means the twin is not sending.",
              "**Degradation** — index from 0 to 1, with the alarm threshold drawn; repair from the MuJoCo viewer brings it down and the alarm clears by itself.",
              "**Time to failure threshold** — continuous operating minutes, derived from the degradation slope; while the cell is idle, the estimate does not drop.",
              "**Model-detected anomalies** — anomalous windows per minute; the alarm fires at ${var.anomaly_alarm_threshold} windows in 10 minutes.",
            ])
          }
        },
      ],
    )
  })
}

# ---------------------------------------------------------------- alarmă compusă
# Un singur semnal de „pipeline degradat”, folosit în demo și în notificări.
resource "aws_cloudwatch_composite_alarm" "pipeline_degraded" {
  alarm_name        = "${local.name}-pipeline-degraded"
  alarm_description = "At least one critical pipeline component is in alarm."

  alarm_rule = join(" OR ", concat(
    ["ALARM(\"${aws_cloudwatch_metric_alarm.firehose_delivery_failures.alarm_name}\")"],
    ["ALARM(\"${aws_cloudwatch_metric_alarm.process_anomaly.alarm_name}\")"],
    [for ep in var.sagemaker_endpoint_names : "ALARM(\"${local.name}-${ep}-5xx\")"],
  ))

  alarm_actions = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]
  ok_actions    = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  tags = local.tags
}
