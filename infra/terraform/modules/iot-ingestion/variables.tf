variable "project_name" {
  description = "Prefixul resurselor."
  type        = string
}

variable "environment" {
  description = "Mediul: dev, staging sau prod."
  type        = string
}

variable "account_id" {
  description = "ID-ul contului AWS, folosit la construirea ARN-urilor care ar crea dependențe circulare."
  type        = string
}

variable "region" {
  description = "Regiunea AWS."
  type        = string
}

variable "telemetry_topic" {
  description = "Topicul MQTT de telemetrie din care se alimentează Firehose. Poate conține wildcard `+`."
  type        = string
  default     = "cncdt/+/cnc/+/telemetry"
}

variable "control_topic_prefix" {
  description = "Prefixul topicurilor de control scrise înapoi către simulare."
  type        = string
  default     = ""
}

variable "thing_name" {
  description = "Numele lucrului IoT care reprezintă celula CNC simulată."
  type        = string
  default     = "cnc-cell-01"
}

variable "create_thing_and_certificate" {
  description = "Creează lucrul IoT, certificatul mTLS și politica. În producție folosiți fleet provisioning."
  type        = bool
  default     = true
}

variable "data_lake_bucket_name" {
  description = "Bucketul în care Firehose scrie telemetria Parquet."
  type        = string
}

variable "data_lake_bucket_arn" {
  description = "ARN-ul bucket-ului data lake."
  type        = string
}

variable "logs_bucket_name" {
  description = "Bucketul pentru backup-ul înregistrărilor neprocesate."
  type        = string
}

variable "logs_bucket_arn" {
  description = "ARN-ul bucket-ului de loguri."
  type        = string
}

variable "glue_database_name" {
  description = "Baza de date Glue pentru tabelul de telemetrie."
  type        = string
}

variable "glue_table_name" {
  description = "Numele tabelului Glue cu schema telemetriei transformate."
  type        = string
  default     = "telemetry_enriched"
}

variable "create_glue_table" {
  description = "Creează tabelul Glue folosit de Firehose pentru conversia în Parquet."
  type        = bool
  default     = true
}

variable "transform_lambda_arn" {
  description = "ARN-ul funcției Lambda de transformare în zbor."
  type        = string
}

variable "transform_lambda_name" {
  description = "Numele funcției Lambda de transformare, pentru permisiunea de invocare din Firehose."
  type        = string
}

variable "buffering_interval_seconds" {
  description = "Fereastra de micro-batching Firehose."
  type        = number
  default     = 60
}

variable "buffering_size_mb" {
  # Cu conversia în Parquet pornită, Firehose cere cel puțin 64 MB pe lot.
  description = "Dimensiunea maximă a lotului Firehose, în MB; conversia în Parquet cere minim 64."
  type        = number
  default     = 64
}

variable "firehose_lambda_buffer_mb" {
  description = "Dimensiunea lotului trimis funcției Lambda de transformare."
  type        = number
  default     = 3
}

variable "firehose_lambda_buffer_seconds" {
  description = "Fereastra în care Firehose acumulează înregistrări înainte de a chema Lambda."
  type        = number
  default     = 60
}

variable "error_output_prefix" {
  description = "Prefixul S3 pentru înregistrările care nu pot fi livrate."
  type        = string
  default     = "replay/errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/hour=!{timestamp:HH}/"
}

variable "enable_backup" {
  description = "Păstrează o copie a înregistrărilor brute în bucket-ul de loguri."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Retenția logurilor CloudWatch."
  type        = number
  default     = 14
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
