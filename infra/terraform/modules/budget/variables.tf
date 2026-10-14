variable "project_name" {
  description = "Prefixul resurselor."
  type        = string
}

variable "environment" {
  description = "Mediul: dev, staging sau prod."
  type        = string
}

variable "limit_amount_usd" {
  description = "Plafonul lunar al bugetului, în USD."
  type        = number
  default     = 20

  validation {
    condition     = var.limit_amount_usd > 0
    error_message = "Plafonul bugetului trebuie să fie pozitiv."
  }
}

variable "notifications" {
  description = <<-EOT
    Pragurile la care se trimite notificare. Fiecare element are:
    `threshold` (procent), `threshold_type` (PERCENTAGE sau ABSOLUTE_VALUE),
    `notification_type` (ACTUAL sau FORECASTED) și `comparison_operator`.
  EOT
  type = list(object({
    threshold           = number
    threshold_type      = optional(string, "PERCENTAGE")
    notification_type   = optional(string, "ACTUAL")
    comparison_operator = optional(string, "GREATER_THAN")
  }))
  default = [
    { threshold = 50 },
    { threshold = 80 },
    { threshold = 100, notification_type = "FORECASTED" },
  ]
}

variable "email_endpoints" {
  description = "Adresele care primesc notificările de buget. Fără ele nu se creează notificări."
  type        = list(string)
  default     = []
}

variable "scope_to_project" {
  description = <<-EOT
    Limitează bugetul la costurile etichetate cu `Project`. Filtrul pe etichete
    funcționează doar dacă eticheta este activată ca etichetă de alocare a costurilor
    în consola Billing; altfel bugetul raportează zero cheltuială.
  EOT
  type        = bool
  default     = false
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
