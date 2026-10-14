output "alarm_topic_arn" {
  description = "ARN-ul subiectului SNS pentru alarme."
  value       = local.sns_topic_arn
}

output "alarm_topic_name" {
  description = "Numele subiectului SNS."
  value       = var.create_sns_topic ? aws_sns_topic.alarms[0].name : ""
}

output "dashboard_names" {
  description = "Numele dashboard-urilor CloudWatch: infrastructură și fabrică."
  value = {
    infra   = var.create_dashboard ? aws_cloudwatch_dashboard.infra[0].dashboard_name : ""
    factory = var.create_dashboard ? aws_cloudwatch_dashboard.factory[0].dashboard_name : ""
  }
}

output "dashboard_urls" {
  description = "Linkuri directe către dashboard-uri, pe nume."
  value = {
    for kind, name in {
      infra   = local.dashboard_infra_name
      factory = local.dashboard_factory_name
    } :
    kind => var.create_dashboard ? "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${name}" : ""
  }
}

output "dashboard_url" {
  description = "Link către dashboard-ul de infrastructură, păstrat pentru compatibilitate."
  value       = var.create_dashboard ? "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${local.dashboard_infra_name}" : ""
}

output "dashboard_name" {
  description = "Numele dashboard-ului de infrastructură, păstrat pentru compatibilitate."
  value       = var.create_dashboard ? aws_cloudwatch_dashboard.infra[0].dashboard_name : ""
}

output "composite_alarm_name" {
  description = "Alarma compusă care semnalează degradarea pipeline-ului."
  value       = aws_cloudwatch_composite_alarm.pipeline_degraded.alarm_name
}

output "composite_alarm_arn" {
  description = "ARN-ul alarmei compuse."
  value       = aws_cloudwatch_composite_alarm.pipeline_degraded.arn
}

output "alarm_arns" {
  description = "Toate alarmele de metrice create."
  value = compact(concat(
    [
      aws_cloudwatch_metric_alarm.firehose_delivery_failures.arn,
      aws_cloudwatch_metric_alarm.firehose_throttled.arn,
      aws_cloudwatch_metric_alarm.process_anomaly.arn,
    ],
    [for a in aws_cloudwatch_metric_alarm.endpoint_latency : a.arn],
    [for a in aws_cloudwatch_metric_alarm.endpoint_5xx : a.arn],
    [for a in aws_cloudwatch_metric_alarm.backup_jobs_failed : a.arn],
    [for a in aws_cloudwatch_metric_alarm.degradation_index : a.arn],
    [for a in aws_cloudwatch_metric_alarm.degradation_rul : a.arn],
  ))
}

output "telemetry_namespace" {
  description = "Namespace-ul metricilor proprii de telemetrie."
  value       = local.telemetry_namespace
}

output "simulation_namespace" {
  description = "Namespace-ul metricilor de GPU ale instanței de simulare."
  value       = local.sim_namespace
}
