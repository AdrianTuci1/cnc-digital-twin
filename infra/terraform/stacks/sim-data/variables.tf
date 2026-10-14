variable "project_name" {
  description = "Prefixul numelui volumului."
  type        = string
  default     = "cncdt"
}

variable "environment" {
  description = "Mediul: dev, staging sau prod."
  type        = string
  default     = "dev"
}

variable "region" {
  description = "Regiunea AWS în care stă volumul."
  type        = string
  default     = "eu-central-1"
}

variable "aws_profile" {
  description = "Profilul AWS folosit de provider. Gol înseamnă credențialele din mediu."
  type        = string
  default     = ""
}

variable "availability_zone" {
  description = <<-EOT
    Zona în care se creează volumul. Este fixă pentru toată viața volumului și trebuie să
    coincidă cu zona instanței, altfel atașarea eșuează; twinctl o alege o singură dată, la
    creare (cea mai ieftină zonă spot) și o scrie în `.env` (SIM_DATA_VOLUME_AZ).
  EOT
  type        = string
}

variable "size_gb" {
  description = "Mărimea volumului gp3, în GB. Se plătește lunar, indiferent dacă instanța rulează."
  type        = number
  default     = 100
}

variable "iops" {
  description = "IOPS provisionate pentru gp3. 3000 este baseline-ul gratuit."
  type        = number
  default     = 3000
}

variable "throughput" {
  description = "Debitul în MiB/s pentru gp3. 125 este baseline-ul gratuit."
  type        = number
  default     = 125
}

variable "kms_key_id" {
  description = "Cheia KMS pentru criptare. Gol înseamnă cheia implicită a regiunii."
  type        = string
  default     = ""
}

variable "snapshot_id" {
  description = <<-EOT
    Snapshot din care se creează volumul. Gol înseamnă volum nou, iar twinctl reia automat cel
    mai nou snapshot al volumului atunci când volumul lipsește; aici se pune o valoare doar
    pentru o restaurare dintr-un snapshot anume. Schimbarea valorii înlocuiește volumul.
  EOT
  type        = string
  default     = ""
}

variable "tags" {
  description = "Etichete suplimentare aplicate resurselor."
  type        = map(string)
  default     = {}
}
