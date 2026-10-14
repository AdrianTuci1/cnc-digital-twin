variable "name_prefix" {
  description = "Prefixul numelui funcției, de regulă numele proiectului urmat de mediu."
  type        = string
}

variable "function_name" {
  description = "Numele scurt al funcției, fără prefix."
  type        = string
}

variable "source_dirs" {
  description = "Directoarele împachetate în arhiva de deployment (prima are prioritate la conflicte)."
  type        = list(string)

  validation {
    condition     = length(var.source_dirs) > 0
    error_message = "Este nevoie de cel puțin un director sursă."
  }
}

variable "source_files" {
  description = "Fișiere individuale adăugate în arhivă, sub forma { cale_locală = cale_în_arhivă }."
  type        = map(string)
  default     = {}
}

variable "handler" {
  description = "Handler-ul Lambda, de forma `modul.funcție`."
  type        = string
  default     = "handler.lambda_handler"
}

variable "runtime" {
  description = "Runtime-ul Lambda."
  type        = string
  default     = "python3.12"
}

variable "architecture" {
  description = "Arhitectura: arm64 sau x86_64."
  type        = string
  default     = "arm64"
}

variable "memory_mb" {
  description = "Memoria alocată, în MB."
  type        = number
  default     = 512
}

variable "timeout_seconds" {
  description = "Timeout-ul funcției, în secunde."
  type        = number
  default     = 30
}

variable "environment_variables" {
  description = "Variabile de mediu."
  type        = map(string)
  default     = {}
}

variable "policy_json" {
  description = "Politica IAM inline suplimentară (JSON) pentru accesul la alte servicii."
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "Retenția logurilor CloudWatch."
  type        = number
  default     = 14
}

variable "reserved_concurrency" {
  description = "Concurrency rezervat. -1 înseamnă fără rezervare."
  type        = number
  default     = -1
}

variable "layers" {
  description = "ARN-urile straturilor Lambda atașate."
  type        = list(string)
  default     = []
}

variable "alarm_actions" {
  description = "ARN-uri notificate la alarmele de erori și durată."
  type        = list(string)
  default     = []
}

variable "error_alarm_threshold" {
  description = "Numărul de erori într-o fereastră de 5 minute care declanșează alarma."
  type        = number
  default     = 1
}

variable "dlq_arn" {
  description = "ARN-ul unei cozi SQS folosite ca dead letter queue pentru invocările asincrone."
  type        = string
  default     = ""
}

variable "tracing_mode" {
  description = "Modul X-Ray: PassThrough sau Active."
  type        = string
  default     = "Active"
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
