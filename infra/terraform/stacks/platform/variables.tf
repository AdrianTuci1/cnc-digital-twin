# ------------------------------------------------------------------ identitate
variable "project_name" {
  description = "Prefixul tuturor resurselor."
  type        = string
  default     = "cncdt"
}

variable "environment" {
  description = "Mediul: dev, staging sau prod."
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment trebuie să fie dev, staging sau prod."
  }
}

variable "region" {
  description = "Regiunea AWS."
  type        = string
  default     = "eu-central-1"
}

variable "aws_profile" {
  description = "Profilul AWS CLI. Gol folosește lanțul standard de credențiale."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Etichete suplimentare."
  type        = map(string)
  default     = {}
}

# ------------------------------------------------------------------ stocare
variable "force_destroy_buckets" {
  description = "Permite ștergerea bucket-urilor cu conținut la destroy. Recomandat în dev."
  type        = bool
  default     = false
}

variable "kms_key_arn" {
  description = "Cheie KMS pentru bucket-uri, EFS și endpoint-uri. Gol folosește cheile administrate AWS."
  type        = string
  default     = ""
}

# ------------------------------------------------------------------ ingestie
variable "telemetry_topic" {
  description = "Topicul MQTT de telemetrie."
  type        = string
  default     = "cncdt/+/cnc/+/telemetry"
}

variable "control_topic_prefix" {
  description = "Prefixul topicurilor de control. Gol derivă din project_name/environment."
  type        = string
  default     = ""
}

variable "iot_thing_name" {
  description = "Numele lucrului IoT al celulei CNC."
  type        = string
  default     = "cnc-cell-01"
}

variable "create_iot_thing_and_certificate" {
  description = "Creează lucrul IoT și certificatul mTLS. În producție folosiți fleet provisioning."
  type        = bool
  default     = true
}

variable "firehose_buffering_interval" {
  description = "Fereastra de micro-batching Firehose, în secunde."
  type        = number
  default     = 60
}

variable "firehose_enable_backup" {
  description = "Păstrează copia brută a înregistrărilor în bucket-ul de loguri."
  type        = bool
  default     = true
}

# ------------------------------------------------------------------ hot path
variable "anomaly_threshold" {
  description = "Procentul de ferestre anomale peste care se declanșează alarma de proces."
  type        = number
  default     = 3
}

variable "lambda_log_retention_days" {
  description = "Retenția logurilor Lambda."
  type        = number
  default     = 14
}

variable "lambda_architecture" {
  description = "Arhitectura funcțiilor Lambda."
  type        = string
  default     = "arm64"
}

variable "alarm_email_endpoints" {
  description = "Adrese de e-mail abonate la alarmele CloudWatch."
  type        = list(string)
  default     = []
}

variable "alarm_sms_numbers" {
  description = "Numere de telefon (format E.164) abonate la alarmele CloudWatch, prin SMS. Contul SNS pornește în sandbox: până la ieșirea din sandbox se pot trimite doar către numere verificate."
  type        = list(string)
  default     = []
}

variable "degradation_elements" {
  description = "Elementele urmărite pentru degradare: cheie → etichetă, pragul indicelui și pragul de RUL în minute. Cheia devine dimensiunea Element a metricilor."
  type = map(object({
    label            = string
    warn_threshold   = number
    warn_rul_minutes = number
  }))
  default = {
    cnc = {
      label            = "Mașina CNC (uzura sculei)"
      warn_threshold   = 0.7
      warn_rul_minutes = 15
    }
    robot = {
      label            = "Brațul robotic (frecarea articulațiilor)"
      warn_threshold   = 0.7
      warn_rul_minutes = 15
    }
  }
}

variable "degradation_rates" {
  description = "Pantele degradării, în unități pe secundă, aceleași valori ca în simulare. Din ele se estimează RUL publicat în CloudWatch; zero oprește metrica de RUL."
  type        = map(number)
  default = {
    cnc   = 0.0015
    robot = 0.0008
  }
}

variable "dashboard_anomaly_widget_link" {
  description = "Link afișat în dashboard către notebook-ul de analiză (opțional)."
  type        = string
  default     = ""
}

# ------------------------------------------------------------------ SageMaker Studio
variable "enable_sagemaker_studio" {
  description = "Creează domeniul SageMaker Studio."
  type        = bool
  default     = true
}

variable "sagemaker_vpc_id" {
  description = "VPC-ul domeniului Studio. Gol folosește VPC-ul implicit."
  type        = string
  default     = ""
}

variable "sagemaker_subnet_ids" {
  description = "Subneturile domeniului Studio. Gol alege automat."
  type        = list(string)
  default     = []
}

variable "sagemaker_user_profiles" {
  description = "Profilele de utilizator din domeniu."
  type        = list(string)
  default     = ["engineer"]
}

variable "sagemaker_kernel_instance_type" {
  description = "Tipul de instanță implicit pentru kernel-urile de notebook."
  type        = string
  default     = "ml.t3.medium"
}

variable "sagemaker_training_instance_types" {
  description = "Tipurile de instanță aprobate pentru antrenare din Studio."
  type        = list(string)
  default     = ["ml.t3.medium", "ml.m5.large", "ml.g5.xlarge", "ml.g5.2xlarge"]
}

variable "training_data_bucket_arn" {
  description = "ARN-ul bucket-ului corpusului de antrenament, citit din Studio. Gol nu acordă acces."
  type        = string
  default     = ""
}

variable "enable_studio_idle_shutdown" {
  description = "Închide aplicațiile Studio inactive pentru a reduce costul."
  type        = bool
  default     = true
}

# ------------------------------------------------------------------ endpoint-uri ML
variable "enable_predictive_maintenance_endpoint" {
  description = "Creează endpoint-ul pentru modelul de anomalii pe serii temporale."
  type        = bool
  default     = false
}

variable "predictive_maintenance_model_data_url" {
  description = "URL-ul S3 al model.tar.gz pentru modelul de predictive maintenance."
  type        = string
  default     = ""
}

variable "predictive_maintenance_anomaly_threshold" {
  description = "Pragul scorului RCF; se copiază din metrics.json (percentila 99 pe ferestre sănătoase), nu este o probabilitate."
  type        = number
  default     = 0.65
}

variable "vision_qa_endpoint_enabled" {
  description = "Creează endpoint-ul pentru modelul de inspecție vizuală. Oprit, Lambda-ul de inspecție publică zero verdicte (numara cadrele în QaModelMissing), fiindcă verdictul nu se fabrică din metadate."
  type        = bool
  default     = false
}

variable "vision_qa_model_data_url" {
  description = "URL-ul S3 al model.tar.gz pentru modelul de inspecție vizuală."
  type        = string
  default     = ""
}

variable "vision_qa_defect_threshold" {
  description = "Pragul probabilității de defect; se copiază din deployment.json (pragul ales la antrenare), nu se lasă la 0,5 din reflex."
  type        = number
  default     = 0.5
}

variable "ml_endpoint_deployment_mode" {
  description = "Modul de implementare al endpoint-urilor: serverless sau provisioned."
  type        = string
  default     = "serverless"

  validation {
    condition     = contains(["serverless", "provisioned"], var.ml_endpoint_deployment_mode)
    error_message = "ml_endpoint_deployment_mode trebuie să fie serverless sau provisioned."
  }
}

variable "ml_serverless_memory_mb" {
  description = "Memoria variantelor serverless."
  type        = number
  default     = 2048
}

variable "ml_serverless_max_concurrency" {
  description = "Concurrency maxim pentru variantele serverless."
  type        = number
  default     = 5
}

variable "vision_qa_prebuilt_image_repository" {
  description = "Depozitul de imagini preconstruite pentru inferența de viziune."
  type        = string
  default     = "pytorch-inference"
}

variable "vision_qa_prebuilt_image_tag" {
  description = "Eticheta imaginii preconstruite de viziune. CPU, fiindcă endpoint-ul serverless nu are GPU."
  type        = string
  default     = "2.4.0-cpu-py311-ubuntu22.04-sagemaker-v1.34"
}

variable "model_versions" {
  description = "Versiunile modelelor, publicate ca variabile de mediu către Lambda și în metrici."
  type        = map(string)
  default = {
    predictive_maintenance = "unversioned"
    vision_qa              = "unversioned"
  }
}

# ------------------------------------------------------------------ backup și cost
variable "enable_studio_backup" {
  description = "Salvează zilnic EFS-ul de home al Studio. Are efect doar când Studio este activ."
  type        = bool
  default     = true
}

variable "studio_backup_schedule" {
  description = "Expresia cron a ferestrei de backup, în UTC."
  type        = string
  default     = "cron(0 5 ? * * *)"
}

variable "studio_backup_retention_days" {
  description = "Câte zile se păstrează punctele de restaurare ale EFS-ului Studio."
  type        = number
  default     = 90
}

variable "studio_backup_cold_storage_days" {
  description = "Zilele după care punctele trec în cold storage. 0 dezactivează tranziția."
  type        = number
  default     = 0
}

variable "studio_backup_force_destroy" {
  description = "Permite ștergerea seifului cu puncte de restaurare la `terraform destroy`."
  type        = bool
  default     = true
}

variable "enable_cost_budget" {
  description = "Creează bugetul lunar de cost, cu notificări pe e-mail."
  type        = bool
  default     = true
}

variable "monthly_budget_usd" {
  description = "Plafonul lunar al bugetului, în USD."
  type        = number
  default     = 20
}

variable "budget_scope_to_project" {
  description = "Limitează bugetul la costurile etichetate cu Project (cere eticheta activată în Billing)."
  type        = bool
  default     = false
}

# ------------------------------------------------------------------ simulare
variable "sim_instance_id" {
  description = "ID-ul instanței de simulare, folosit doar pentru widget-urile de GPU din dashboard."
  type        = string
  default     = ""
}
