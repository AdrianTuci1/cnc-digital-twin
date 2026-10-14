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

variable "model_key" {
  description = "Identificatorul scurt al modelului: `predictive-maintenance` sau `vision-qa`."
  type        = string
}

variable "model_data_url" {
  description = "URL-ul S3 al arhivei model.tar.gz produse de antrenare."
  type        = string
}

variable "image_uri" {
  description = "URI-ul imaginii de inferență. Gol înseamnă că se folosește o imagine preconstruită SageMaker."
  type        = string
  default     = ""
}

variable "prebuilt_image_repository" {
  description = "Numele depozitului de imagini preconstruite, folosit când image_uri este gol."
  type        = string
  default     = "sagemaker-scikit-learn"
}

variable "prebuilt_image_tag" {
  description = "Eticheta imaginii preconstruite."
  type        = string
  default     = "1.2-1-cpu-py3"
}

variable "deployment_mode" {
  description = "`serverless` pentru cost zero în repaus, `provisioned` pentru latență constantă."
  type        = string
  default     = "serverless"

  validation {
    condition     = contains(["serverless", "provisioned"], var.deployment_mode)
    error_message = "deployment_mode trebuie să fie serverless sau provisioned."
  }
}

variable "serverless_memory_mb" {
  description = "Memoria variantei serverless, în MB."
  type        = number
  default     = 2048
}

variable "serverless_max_concurrency" {
  description = "Concurrency maxim al variantei serverless."
  type        = number
  default     = 5
}

variable "instance_type" {
  description = "Tipul de instanță pentru modul provisioned."
  type        = string
  default     = "ml.m5.large"
}

variable "initial_instance_count" {
  description = "Numărul inițial de instanțe pentru modul provisioned."
  type        = number
  default     = 1
}

variable "enable_autoscaling" {
  description = "Activează auto-scalarea pentru modul provisioned."
  type        = bool
  default     = false
}

variable "autoscaling_min_capacity" {
  description = "Capacitatea minimă de auto-scalare."
  type        = number
  default     = 1
}

variable "autoscaling_max_capacity" {
  description = "Capacitatea maximă de auto-scalare."
  type        = number
  default     = 4
}

variable "autoscaling_target_invocations" {
  description = "Ținta de invocări per instanță pentru politica de scalare."
  type        = number
  default     = 100
}

variable "container_environment" {
  description = "Variabile de mediu transmise containerului de inferență."
  type        = map(string)
  default     = {}
}

variable "enable_data_capture" {
  description = "Captează cererile și răspunsurile în S3, pentru monitorizare și reantrenare."
  type        = bool
  default     = true
}

variable "data_capture_bucket_name" {
  description = "Bucketul pentru captura de date."
  type        = string
  default     = ""
}

variable "data_capture_prefix" {
  description = "Prefixul S3 al capturii de date."
  type        = string
  default     = "inference-logs"
}

variable "data_capture_sampling_percentage" {
  description = "Procentul de cereri capturate."
  type        = number
  default     = 100
}

variable "create_execution_role" {
  description = "Creează un rol de execuție dedicat endpoint-ului."
  type        = bool
  default     = true
}

variable "execution_role_arn" {
  description = "Rol de execuție existent, folosit când create_execution_role este false."
  type        = string
  default     = ""
}

variable "model_data_bucket_arn" {
  description = "ARN-ul bucket-ului care conține model.tar.gz."
  type        = string
  default     = ""
}

variable "kms_key_id" {
  description = "Cheia KMS pentru captura de date și volume."
  type        = string
  default     = ""
}

variable "endpoint_name" {
  description = "Numele endpoint-ului. Gol derivă din proiect, mediu și cheia modelului."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
