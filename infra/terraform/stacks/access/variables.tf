# ------------------------------------------------------------------ identitate
variable "project_name" {
  description = "Prefixul resurselor."
  type        = string
  default     = "cncdt"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,24}$", var.project_name))
    error_message = "project_name trebuie să conțină doar litere mici, cifre și cratime."
  }
}

variable "environment" {
  description = "Mediul pentru care se creează rolul: dev, staging sau prod."
  type        = string
  default     = "dev"

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
  description = <<-EOT
    Profilul AWS CLI al identității care creează rolul. Trebuie să fie identitatea de admin,
    nu rolul de operator: stack-ul de acces se aplică înainte ca rolul să existe.
  EOT
  type        = string
  default     = ""
}

# ------------------------------------------------------------------ rolul de operator
variable "role_name" {
  description = "Numele rolului. Gol derivă din numele proiectului și mediu."
  type        = string
  default     = ""
}

variable "trusted_principal_arns" {
  description = <<-EOT
    Identitățile care pot asuma rolul. Gol înseamnă rădăcina contului, adică orice principal
    administrativ din cont. Îngustați lista la utilizatorul vostru (ex.
    `arn:aws:iam::123456789012:user/admin`) dacă vreți un trust cât mai mic.
  EOT
  type        = list(string)
  default     = []
}

variable "max_session_duration_hours" {
  description = "Durata maximă a unei sesiuni asumate. AWS permite între 1 și 12 ore."
  type        = number
  default     = 4

  validation {
    condition     = var.max_session_duration_hours >= 1 && var.max_session_duration_hours <= 12
    error_message = "Durata sesiunii trebuie să fie între 1 și 12 ore."
  }
}

variable "require_mfa" {
  description = <<-EOT
    Cere MFA prezent la asumarea rolului. Providerul Terraform nu poate trimite tokenul MFA
    (blocul `assume_role` nu are `mfa_serial`), deci cu MFA activat credențialele se obțin cu
    `scripts/assume-operator.sh` și se exportă înainte de `terraform apply`.
  EOT
  type        = bool
  default     = false
}

variable "base_managed_policy_arns" {
  description = <<-EOT
    Politicile atașate rolului. Implicit `PowerUserAccess`: acces complet la servicii, fără
    administrarea utilizatorilor și a contului. Terraform are nevoie de un rol larg pentru că
    el însuși creează roluri de serviciu și le pasează.
  EOT
  type        = list(string)
  default     = ["arn:aws:iam::aws:policy/PowerUserAccess"]
}

variable "additional_managed_policy_arns" {
  description = "Politici suplimentare atașate rolului (ex. citire Billing pentru rapoarte)."
  type        = list(string)
  default     = []
}

variable "enable_guardrails" {
  description = "Adaugă interdicțiile explicite: fără identități permanente, fără modificarea rolului propriu, fără atingerea dovezilor de audit."
  type        = bool
  default     = true
}

variable "enable_modal_oidc" {
  description = <<-EOT
    Creează furnizorul OIDC `oidc.modal.com`, pe care se sprijină rolul asumat de containerele
    Modal care scriu corpusul de antrenament (ADR 0015). Se creează doar din identitatea de
    admin: gardul `DenyPermanentIdentities` refuză `iam:CreateOpenIDConnectProvider` în sesiunile
    de operator.
  EOT
  type        = bool
  default     = true
}

variable "modal_oidc_url" {
  description = "Adresa furnizorului OIDC. `https://oidc.modal.com` este emițătorul de tokenuri al Modal."
  type        = string
  default     = "https://oidc.modal.com"
}

variable "modal_oidc_audience" {
  description = "Audiența (`aud`) cerută în trust-ul rolului. Trebuie să fie identică în furnizor, în token și în condiția rolului."
  type        = string
  default     = "oidc.modal.com"
}

variable "state_bucket_name" {
  description = "Bucket-ul de state Terraform, protejat de ștergere. Gol derivă din numele proiectului și ID-ul de cont."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Etichete aplicate resurselor."
  type        = map(string)
  default     = {}
}
