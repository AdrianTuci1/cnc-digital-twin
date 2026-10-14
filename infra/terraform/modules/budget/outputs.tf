output "budget_name" {
  description = "Numele bugetului lunar."
  value       = aws_budgets_budget.monthly.name
}

output "budget_id" {
  description = "Identificatorul bugetului, în forma <cont>:<nume>."
  value       = aws_budgets_budget.monthly.id
}

output "notification_count" {
  description = "Numărul de praguri de notificare create."
  value       = length(local.notifications)
}
