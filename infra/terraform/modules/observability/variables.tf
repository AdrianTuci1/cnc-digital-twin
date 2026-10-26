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

variable "account_id" {
  description = "ID-ul contului AWS."
  type        = string
}

variable "alarm_email_endpoints" {
  description = "Adrese de e-mail abonate la subiectul SNS de alarme."
  type        = list(string)
  default     = []
}

variable "alarm_sms_numbers" {
  description = "Numere de telefon (format E.164) abonate la subiectul SNS de alarme, prin SMS."
  type        = list(string)
  default     = []
}

variable "degradation_elements" {
  description = "Elementele urmărite pentru degradare: cheie → etichetă, pragul indicelui și pragul de RUL în minute."
  type = map(object({
    label            = string
    warn_threshold   = number
    warn_rul_minutes = number
  }))
  default = {}
}

variable "create_sns_topic" {
  description = "Creează subiectul SNS de alarme. Dezactivați dacă folosiți un topic partajat."
  type        = bool
  default     = true
}

variable "external_alarm_topic_arn" {
  description = "ARN-ul unui topic SNS existent, folosit când create_sns_topic este false."
  type        = string
  default     = ""
}

variable "firehose_stream_name" {
  description = "Numele stream-ului Firehose monitorizat."
  type        = string
}

variable "lambda_function_names" {
  description = "Numele funcțiilor Lambda de pe hot path."
  type        = list(string)
  default     = []
}

variable "sagemaker_endpoint_names" {
  description = "Numele endpoint-urilor SageMaker monitorizate."
  type        = list(string)
  default     = []
}

variable "iot_rule_name" {
  description = "Numele regulii IoT de telemetrie."
  type        = string
  default     = ""
}

variable "telemetry_device_id" {
  description = "DeviceId cu care geamănul publică metricele. Trebuie să coincidă cu CNCDT_DEVICE_ID, fiindcă dimensionarea face parte din identitatea metricei în CloudWatch."
  type        = string
}

variable "telemetry_cell_id" {
  description = "CellId cu care geamănul publică metricele. Trebuie să coincidă cu CNCDT_CELL_ID."
  type        = string
}

variable "sim_instance_id" {
  description = "ID-ul instanței de simulare, pentru widget-urile de GPU."
  type        = string
  default     = ""
}

variable "alarm_topic_arn" {
  description = "ARN-ul subiectului SNS unde ajung alarmele (completat după crearea topicului)."
  type        = string
  default     = ""
}

variable "anomaly_alarm_threshold" {
  description = "Numărul de ferestre anomale în 10 minute care declanșează alarma de proces."
  type        = number
  default     = 3
}

variable "backup_vault_name" {
  description = "Numele seifului AWS Backup al cărui joburi eșuate sunt alarmate. Gol nu creează alarma."
  type        = string
  default     = ""
}

variable "enable_anomaly_metric_filters" {
  description = "Creează filtre de metrice pe logurile Lambda-ului de feature engineering."
  type        = bool
  default     = true
}

variable "feature_engineering_log_group" {
  description = "Log group-ul funcției de feature engineering, folosit de filtrele de metrice."
  type        = string
  default     = ""
}

variable "create_dashboard" {
  description = "Creează dashboard-urile CloudWatch."
  type        = bool
  default     = true
}

variable "dashboard_infra_name" {
  description = "Numele dashboard-ului de infrastructură. Gol derivă din numele proiectului și mediu, cu sufixul -infra."
  type        = string
  default     = ""
}

variable "dashboard_factory_name" {
  description = "Numele dashboard-ului operațional de fabrică. Gol derivă din numele proiectului și mediu, cu sufixul -factory."
  type        = string
  default     = ""
}

variable "dashboard_name" {
  description = "Numele dashboard-ului de infrastructură, păstrat pentru compatibilitate. Gol folosește dashboard_infra_name."
  type        = string
  default     = ""
}

variable "dashboard_anomaly_widget_link" {
  description = "Link afișat în dashboard către notebook-ul de analiză. Gol nu adaugă widgetul."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
