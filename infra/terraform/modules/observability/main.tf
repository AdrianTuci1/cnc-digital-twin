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
  alarm_description   = "Firehose nu a livrat date în S3 în ultimele 5 minute."

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
  alarm_description   = "Firehose a limitat înregistrări: verificați limita de debit sau de invocări Lambda."

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
  alarm_description   = "Modelul de predictive maintenance a marcat ferestre anomale repetate: posibilă uzură de sculă sau defect mecanic."

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
  alarm_description   = "Latența p95 a modelului ${each.value} depășește 800 ms: bucla de control devine prea lentă."

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
  alarm_description   = "Endpoint-ul ${each.value} returnează erori 5xx."

  dimensions = {
    EndpointName = each.value
    VariantName  = "AllTraffic"
  }

  alarm_actions = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  tags = local.tags
}

# ---------------------------------------------------------------- degradare
# Alarma urmărește metrica, iar metrica o resetează reparația din panoul Isaac Sim
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
  alarm_description   = "${each.value.label}: indicele de degradare a depășit ${each.value.warn_threshold}. După intervenție, rulați „Repară tot” în panoul Isaac Sim; alarma se stinge când indicele scade sub prag."

  dimensions = {
    Element = each.key
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
  alarm_description   = "${each.value.label}: mai puțin de ${each.value.warn_rul_minutes} minute de operare până la pragul de avarie. Programați intervenția."

  dimensions = {
    Element = each.key
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

  alarm_description = "Un job de backup AWS a eșuat în ultimele 24 de ore."
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
            title = "Starea infrastructurii"
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
            title  = "Ingestie IoT și micro-batching Firehose"
            region = var.region
            view   = "timeSeries"
            stat   = "Sum"
            period = 60
            metrics = [
              ["AWS/Firehose", "IncomingRecords", "DeliveryStreamName", var.firehose_stream_name],
              ["AWS/Firehose", "IncomingBytes", "DeliveryStreamName", var.firehose_stream_name],
              ["AWS/Firehose", "DeliveryToS3.Records", "DeliveryStreamName", var.firehose_stream_name],
            ]
            yAxis = { left = { label = "înregistrări", showUnits = false } }
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 4
          width  = 12
          height = 6
          properties = {
            title  = "Lambda de feature engineering"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = concat(
              [for fn in var.lambda_function_names : ["AWS/Lambda", "Invocations", "FunctionName", fn, { stat = "Sum" }]],
              [for fn in var.lambda_function_names : ["AWS/Lambda", "Errors", "FunctionName", fn, { stat = "Sum", color = "#d62728" }]],
              [for fn in var.lambda_function_names : ["AWS/Lambda", "Duration", "FunctionName", fn, { stat = "p95" }]],
            )
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
            title  = "Endpoint-uri SageMaker"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = concat(
              [for ep in var.sagemaker_endpoint_names : ["AWS/SageMaker", "Invocations", "EndpointName", ep, "VariantName", "AllTraffic", { stat = "Sum" }]],
              [for ep in var.sagemaker_endpoint_names : ["AWS/SageMaker", "ModelLatency", "EndpointName", ep, "VariantName", "AllTraffic", { stat = "p95", yAxis = "right" }]],
              [for ep in var.sagemaker_endpoint_names : ["AWS/SageMaker", "Invocation5XXErrors", "EndpointName", ep, "VariantName", "AllTraffic", { stat = "Sum", color = "#d62728" }]],
            )
            yAxis = {
              left  = { label = "invocări", showUnits = false }
              right = { label = "latență (µs)", showUnits = false }
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
            title  = "Sănătatea pipeline-ului de date"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = [
              [local.telemetry_namespace, "SamplesAggregated", { stat = "Sum" }],
              [local.telemetry_namespace, "WindowsProcessed", { stat = "Sum" }],
              [local.telemetry_namespace, "EndpointFailures", { stat = "Sum", color = "#d62728" }],
              [local.telemetry_namespace, "QaEndpointFailures", { stat = "Sum", color = "#ff7f0e" }],
            ]
            yAxis = { left = { label = "pe minut", showUnits = false } }
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
            title  = "Instanța de simulare (GPU)"
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
            title  = "Erori recente în pipeline"
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
            markdown = "### Analiză\n[Deschide notebook-ul de analiză](${var.dashboard_anomaly_widget_link})"
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
            title = "Sănătatea celulei"
            alarms = compact(concat(
              [for key, element in var.degradation_elements : "${local.alarm_arn_prefix}${local.name}-${key}-degradation"],
              [for key, element in var.degradation_elements : element.warn_rul_minutes > 0 ? "${local.alarm_arn_prefix}${local.name}-${key}-rul-low" : ""],
            ))
          }
        },
      ],

      # Producția: câte piese bune și câte rebuturi ies pe oră. Când degradarea este mică,
      # ambele cifre stau la valoarea nominală a celulei.
      [
        {
          type   = "metric"
          x      = 0
          y      = 4
          width  = 12
          height = 6
          properties = {
            title   = "Producție pe oră: piese bune și rebut"
            region  = var.region
            view    = "timeSeries"
            period  = 3600
            stat    = "Sum"
            stacked = true
            metrics = [
              [local.telemetry_namespace, "PartsGood", { label = "piese bune", color = "#2ca02c" }],
              [local.telemetry_namespace, "PartsScrap", { label = "rebut", color = "#d62728" }],
            ]
            yAxis = { left = { label = "piese pe oră", showUnits = false } }
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 4
          width  = 12
          height = 6
          properties = {
            title  = "Rata de rebut"
            region = var.region
            view   = "timeSeries"
            period = 3600
            metrics = [
              [{ expression = "100 * scrap / (good + scrap)", label = "rebut (%)", id = "rate" }],
              [local.telemetry_namespace, "PartsGood", { id = "good", stat = "Sum", visible = false }],
              [local.telemetry_namespace, "PartsScrap", { id = "scrap", stat = "Sum", visible = false }],
            ]
            yAxis = { left = { label = "%", min = 0, showUnits = false } }
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
          title  = "Degradare: ${var.degradation_elements[key].label}"
          region = var.region
          view   = "gauge"
          period = 60
          stat   = "Maximum"
          metrics = [
            [local.telemetry_namespace, "DegradationIndex", "Element", key, { label = key }],
          ]
          yAxis = { left = { min = 0, max = 1, label = "indice", showUnits = false } }
          annotations = {
            horizontal = [
              { label = "prag de alarmă", value = var.degradation_elements[key].warn_threshold, fill = "above" },
            ]
          }
        }
      }],

      # Cât timp de operare a mai rămas până la pragul de avarie: estimarea din panta
      # degradării, adică eticheta pe care o prezice modelul de predictive maintenance.
      [
        {
          type   = "metric"
          x      = 0
          y      = 16
          width  = 12
          height = 6
          properties = {
            title  = "Timp până la pragul de avarie (minute de operare)"
            region = var.region
            view   = "timeSeries"
            period = 60
            stat   = "Minimum"
            metrics = [
              for key in local.factory_elements :
              [local.telemetry_namespace, "RULMinutes", "Element", key, { label = key }]
            ]
            yAxis = { left = { label = "minute", min = 0, showUnits = false } }
            annotations = length(var.degradation_elements) == 0 ? {} : {
              horizontal = [
                {
                  label = "prag de intervenție"
                  value = min([for element in values(var.degradation_elements) : element.warn_rul_minutes]...)
                  fill  = "below"
                },
              ]
            }
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 16
          width  = 12
          height = 6
          properties = {
            title  = "Anomalii detectate de model"
            region = var.region
            view   = "timeSeries"
            period = 60
            metrics = [
              [local.telemetry_namespace, "AnomalyDetectedWindows", { stat = "Sum", label = "ferestre anomale" }],
              [local.telemetry_namespace, "AnomalyScore", { stat = "Average", label = "scor", yAxis = "right" }],
            ]
            annotations = {
              horizontal = [
                { label = "prag alarmă", value = var.anomaly_alarm_threshold },
              ]
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
              "### Cum se citesc panourile",
              "**Producție pe oră** — `PartsGood` și `PartsScrap`, însumate pe o oră de ceas.",
              "**Degradare** — indice de la 0 la 1, cu pragul de alarmă marcat; reparația din panoul Isaac Sim îl readuce la 0, iar alarma se stinge singură.",
              "**Timp până la pragul de avarie** — minute de operare continuă, calculate din panta degradării; cât timp celula stă, estimarea nu scade.",
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
  alarm_description = "Cel puțin o componentă critică a pipeline-ului este în stare de alarmă."

  alarm_rule = join(" OR ", concat(
    ["ALARM(\"${aws_cloudwatch_metric_alarm.firehose_delivery_failures.alarm_name}\")"],
    ["ALARM(\"${aws_cloudwatch_metric_alarm.process_anomaly.alarm_name}\")"],
    [for ep in var.sagemaker_endpoint_names : "ALARM(\"${local.name}-${ep}-5xx\")"],
  ))

  alarm_actions = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]
  ok_actions    = local.sns_topic_arn == "" ? [] : [local.sns_topic_arn]

  tags = local.tags
}
