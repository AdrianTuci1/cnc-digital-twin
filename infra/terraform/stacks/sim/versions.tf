terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.4"
    }
    # PKI-ul Client VPN (CA, certificat de server, certificat de client).
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Stack-ul de simulare este efemer, deci state-ul stă local (fișier gitignorat).
  # Pentru lucru în echipă, decomentați blocul de mai jos după `make bootstrap`
  # și rulați `terraform init -migrate-state`.
  #
  # backend "s3" {
  #   bucket       = "cncdt-terraform-state"
  #   key          = "sim/terraform.tfstate"
  #   region       = "eu-central-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}
