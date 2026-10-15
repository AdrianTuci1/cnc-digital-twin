variable "project_name" {
  description = "Prefixul numelor de resursă."
  type        = string
  default     = "cncdt"
}

variable "environment" {
  description = "Mediul: dev, staging sau prod."
  type        = string
  default     = "dev"
}

variable "region" {
  description = "Regiunea AWS în care stă bucket-ul. Aceeași cu a restului proiectului, ca antrenarea să nu scoată datele din regiune."
  type        = string
  default     = "eu-central-1"
}

variable "aws_profile" {
  description = "Profilul AWS folosit de provider. Gol înseamnă credențialele din mediu, adică sesiunea rolului de operator."
  type        = string
  default     = ""
}

variable "launch_confirmed" {
  description = <<-EOT
    Confirmarea explicită că lansarea resurselor plătite este deliberată. Implicit `false`, iar
    `terraform_data.launch_guard` refuză atât planul, cât și aplicarea, cât timp valoarea rămâne
    `false`. Se pune pe `true` cu `-var launch_confirmed=true`, ceea ce face `make
    training-data-up` după ce `INFRA_LAUNCH_ENABLED=true` în `.env`. Detalii:
    ops/runbooks/launch-guard.md.
  EOT
  type        = bool
  default     = false
}

variable "force_destroy" {
  description = "Lasă `terraform destroy` să golească bucket-ul cu obiecte cu tot. Recomandat `true` în `dev`."
  type        = bool
  default     = true
}

variable "kms_key_arn" {
  description = "Cheia KMS pentru criptare. Gol folosește SSE-S3 (AES256)."
  type        = string
  default     = ""
}

variable "noncurrent_version_expiration_days" {
  description = "După câte zile se șterg versiunile vechi ale obiectelor. Corpusul se rescrie la fiecare colectare, deci versiunile se adună."
  type        = number
  default     = 30
}

variable "modal_workspace_id" {
  description = <<-EOT
    ID-ul spațiului de lucru Modal din care se poate asuma rolul (`ac-...`). Se citește cu
    `modal token info` ori din https://modal.com/settings/workspaces. Trust-ul rolului se leagă
    de el, ca un alt spațiu Modal să nu poată intra.
  EOT
  type        = string
}

variable "modal_oidc_provider_arn" {
  description = <<-EOT
    ARN-ul furnizorului OIDC `oidc.modal.com` din cont. Gol îl derivă din cont și partiție.
    Furnizorul se creează în stack-ul `access` (`make access-up`), fiindcă gardul rolului de
    operator refuză `iam:CreateOpenIDConnectProvider`; de aceea `access` se aplică înaintea
    acestui stack.
  EOT
  type        = string
  default     = ""
}

variable "modal_role_name" {
  description = "Numele rolului pe care Modal îl asumă. Gol derivă din numele proiectului și mediu."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Etichete suplimentare aplicate resurselor."
  type        = map(string)
  default     = {}
}
