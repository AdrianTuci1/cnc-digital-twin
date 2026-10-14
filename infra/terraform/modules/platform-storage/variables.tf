variable "project_name" {
  description = "Prefixul numelor de bucket."
  type        = string
}

variable "environment" {
  description = "Mediul: dev, staging sau prod."
  type        = string
}

variable "account_id" {
  description = "ID-ul contului AWS, folosit pentru a garanta unicitatea globală a numelui de bucket."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id trebuie să fie un ID de cont AWS din 12 cifre."
  }
}

variable "kms_key_arn" {
  description = "Cheia KMS pentru criptarea bucket-urilor. Gol folosește SSE-S3 (AES256)."
  type        = string
  default     = ""
}

variable "force_destroy" {
  description = "Permite ștergerea bucket-urilor care conțin obiecte. Recomandat `true` în dev."
  type        = bool
  default     = false
}

variable "noncurrent_version_expiration_days" {
  description = "După câte zile se șterg versiunile vechi ale obiectelor."
  type        = number
  default     = 30
}

variable "raw_transition_ia_days" {
  description = "După câte zile telemetria brută trece în STANDARD_IA."
  type        = number
  default     = 30
}

variable "raw_transition_glacier_days" {
  description = "După câte zile telemetria brută trece în GLACIER_IR."
  type        = number
  default     = 180
}

variable "enable_glue_catalog" {
  description = "Creează baza de date Glue pentru catalogul de telemetrie."
  type        = bool
  default     = true
}

variable "glue_database_name" {
  description = "Numele bazei de date Glue."
  type        = string
  default     = ""
}

variable "athena_result_retention_days" {
  description = "Retenția rezultatelor Athena."
  type        = number
  default     = 14
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
