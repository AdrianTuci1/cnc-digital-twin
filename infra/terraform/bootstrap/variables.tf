variable "project_name" {
  description = "Prefixul numelui bucket-ului de state."
  type        = string
  default     = "cncdt"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,24}$", var.project_name))
    error_message = "project_name trebuie să conțină doar litere mici, cifre și cratime."
  }
}

variable "region" {
  description = "Regiunea AWS pentru bucket-ul de state."
  type        = string
  default     = "eu-central-1"
}

variable "aws_profile" {
  description = "Profilul AWS CLI."
  type        = string
  default     = ""
}

variable "state_bucket_name" {
  description = "Nume explicit pentru bucket-ul de state. Gol derivă din project_name și ID-ul de cont."
  type        = string
  default     = ""
}

variable "force_destroy" {
  description = "Permite ștergerea bucket-ului de state cu tot cu versiuni."
  type        = bool
  default     = false
}

variable "noncurrent_version_expiration_days" {
  description = "Retenția versiunilor vechi ale fișierelor de state."
  type        = number
  default     = 90
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
