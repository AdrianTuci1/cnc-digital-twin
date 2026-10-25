variable "project_name" {
  description = "Prefixul resurselor."
  type        = string
}

variable "environment" {
  description = "Mediul: dev, staging sau prod."
  type        = string
}

variable "region" {
  description = "Regiunea AWS."
  type        = string
}

variable "vpc_id" {
  description = "VPC-ul în care se creează domeniul. Gol folosește VPC-ul implicit."
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Subneturile domeniului. Gol folosește toate subneturile VPC-ului ales."
  type        = list(string)
  default     = []
}

variable "domain_name" {
  description = "Numele domeniului SageMaker Studio. Gol derivă din numele proiectului și mediu."
  type        = string
  default     = ""
}

variable "user_profiles" {
  description = "Profilele de utilizator create în domeniu."
  type        = list(string)
  default     = ["engineer"]
}

variable "default_kernel_instance_type" {
  description = "Tipul de instanță implicit pentru kernel-urile de notebook."
  type        = string
  default     = "ml.t3.medium"
}

variable "jupyter_lab_image_arn" {
  description = "Imaginea JupyterLab pentru spațiile private. Gol folosește SageMaker Distribution CPU."
  type        = string
  default     = ""
}

variable "default_kernel_volume_gb" {
  description = "Dimensiunea volumului EBS al kernel-ului, în GB."
  type        = number
  default     = 30
}

variable "training_instance_types" {
  description = "Tipurile de instanță permise pentru job-uri de antrenare din Studio."
  type        = list(string)
  default     = ["ml.t3.medium", "ml.g5.xlarge", "ml.g5.2xlarge", "ml.m5.large"]
}

variable "data_lake_bucket_arn" {
  description = "ARN-ul bucket-ului data lake, la care are acces rolul de execuție."
  type        = string
}

variable "assets_bucket_arn" {
  description = "ARN-ul bucket-ului de asset-uri."
  type        = string
  default     = ""
}

variable "read_only_bucket_arns" {
  description = "Bucketuri din care rolul de execuție poate citi, fără drept de scriere (corpusul de antrenament)."
  type        = list(string)
  default     = []
}

variable "kms_key_arn" {
  description = "Cheia KMS pentru EFS-ul domeniului. Gol folosește cheia implicită."
  type        = string
  default     = ""
}

variable "enable_idle_shutdown" {
  description = "Închide automat aplicațiile Studio inactive (recomandat pentru controlul costurilor)."
  type        = bool
  default     = true
}

variable "idle_timeout_minutes" {
  description = "Minutele de inactivitate după care se închide aplicația."
  type        = number
  default     = 60

  # SageMaker cere cel puțin 60 de minute pentru fereastra de inactivitate.
  validation {
    condition     = var.idle_timeout_minutes >= 60
    error_message = "idle_timeout_minutes trebuie să fie cel puțin 60."
  }
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
