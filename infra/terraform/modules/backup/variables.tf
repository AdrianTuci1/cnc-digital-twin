variable "project_name" {
  description = "Prefixul resurselor."
  type        = string
}

variable "environment" {
  description = "Mediul: dev, staging sau prod."
  type        = string
}

variable "selection_name" {
  description = "Sufixul care descrie ce se salvează (ex. `studio-home-efs`)."
  type        = string
}

variable "resource_arns" {
  description = "ARN-urile resurselor salvate de acest plan."
  type        = list(string)

  validation {
    condition     = length(var.resource_arns) > 0
    error_message = "Planul de backup are nevoie de cel puțin o resursă."
  }
}

variable "schedule" {
  description = "Expresia cron a ferestrei de backup, în UTC."
  type        = string
  default     = "cron(0 5 ? * * *)"
}

variable "rule_name" {
  description = "Numele regulii din plan."
  type        = string
  default     = "daily"
}

variable "retention_days" {
  description = "Câte zile se păstrează punctele de restaurare."
  type        = number
  default     = 90

  validation {
    condition     = var.retention_days >= 1
    error_message = "Retenția trebuie să fie de cel puțin o zi."
  }
}

variable "cold_storage_after_days" {
  description = "Zilele după care punctele trec în cold storage. 0 dezactivează tranziția."
  type        = number
  default     = 0

  validation {
    condition     = var.cold_storage_after_days == 0 || var.cold_storage_after_days >= 90
    error_message = "AWS permite trecerea în cold storage după minimum 90 de zile."
  }
}

variable "start_window_minutes" {
  description = "Fereastra în care poate începe jobul de backup."
  type        = number
  default     = 60
}

variable "completion_window_minutes" {
  description = "Fereastra în care trebuie să se termine jobul de backup."
  type        = number
  default     = 180
}

variable "force_destroy" {
  description = <<-EOT
    Permite ștergerea seifului împreună cu punctele de restaurare. Necesar în dev,
    altfel planul de backup blochează `terraform destroy`.
  EOT
  type        = bool
  default     = true
}

variable "kms_key_arn" {
  description = "Cheia KMS a seifului. Gol folosește cheia administrată AWS."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
