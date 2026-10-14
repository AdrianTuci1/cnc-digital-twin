locals {
  name = "${var.project_name}-${var.environment}-monthly"

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Component   = "cost-control"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  # AWS respinge o notificare fără abonați, deci notificările apar doar când există
  # cel puțin o adresă configurată.
  notifications = length(var.email_endpoints) > 0 ? var.notifications : []

  # `format` este folosit pentru că `$` face parte din sintaxa filtrului de etichete.
  cost_filters = var.scope_to_project ? [{
    name   = "TagKeyValue"
    values = [format("user:Project$%s", var.project_name)]
  }] : []
}

# Bugetul este singurul control de cost care privește contul întreg: alarmele CloudWatch
# acoperă componentele tehnice, iar acesta prinde cheltuiala care scapă de ele.
resource "aws_budgets_budget" "monthly" {
  name         = local.name
  budget_type  = "COST"
  limit_amount = tostring(var.limit_amount_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Perioadă recurentă: AWS cere un interval, iar finalul este valoarea convențională
  # „niciodată” din documentație.
  time_period_start = "2024-01-01_00:00"
  time_period_end   = "2087-06-15_00:00"

  dynamic "cost_filter" {
    for_each = local.cost_filters

    content {
      name   = cost_filter.value.name
      values = cost_filter.value.values
    }
  }

  dynamic "notification" {
    for_each = local.notifications

    content {
      comparison_operator        = notification.value.comparison_operator
      threshold                  = notification.value.threshold
      threshold_type             = notification.value.threshold_type
      notification_type          = notification.value.notification_type
      subscriber_email_addresses = var.email_endpoints
    }
  }
}
