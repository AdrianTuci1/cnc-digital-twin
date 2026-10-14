variable "project_name" {
  description = "Prefixul resurselor proiectului."
  type        = string
}

variable "environment" {
  description = "Mediul (dev, staging, prod)."
  type        = string
}

variable "vpc_id" {
  description = "VPC-ul în care se creează endpoint-ul."
  type        = string
}

variable "subnet_id" {
  description = "Subrețea asociată endpoint-ului (de unde clientul preia rutele)."
  type        = string
}

variable "client_cidr_block" {
  description = "Pulul de adrese al clienților VPN. Nu se suprapune cu VPC-ul. Minim /22."
  type        = string
  default     = "10.100.0.0/22"

  validation {
    condition = (
      can(cidrnetmask(var.client_cidr_block))
      && can(tonumber(split("/", var.client_cidr_block)[1]))
      && tonumber(split("/", var.client_cidr_block)[1]) <= 22
    )
    error_message = "client_cidr_block trebuie să fie un CIDR valid de cel mult /22."
  }
}

variable "authorized_cidr" {
  description = "CIDR-ul către care clientul are voie să meargă prin tunel (de regulă VPC-ul)."
  type        = string
}

variable "allowed_cidrs" {
  description = "CIDR-urile publice care pot iniția tunelul (de regulă IP-ul operatorului)."
  type        = list(string)
  default     = []
}

variable "port" {
  description = "Portul endpoint-ului. 443 trece prin majoritatea rețelelor."
  type        = number
  default     = 443
}

variable "transport_protocol" {
  description = "udp sau tcp. UDP evită încapsularea video în TCP."
  type        = string
  default     = "udp"

  validation {
    condition     = contains(["udp", "tcp"], var.transport_protocol)
    error_message = "transport_protocol trebuie să fie udp sau tcp."
  }
}

variable "client_common_name" {
  description = "CN-ul certificatului de client (numele dispozitivului operatorului)."
  type        = string
  default     = "operator"
}

variable "ca_validity_hours" {
  description = "Valabilitatea CA-ului, în ore."
  type        = number
  default     = 43800 # 5 ani
}

variable "certificate_validity_hours" {
  description = "Valabilitatea certificatelor de server și de client, în ore."
  type        = number
  default     = 8760 # 1 an
}

variable "tags" {
  description = "Etichete suplimentare."
  type        = map(string)
  default     = {}
}
