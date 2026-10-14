terraform {
  # use_lockfile în backend necesită Terraform >= 1.10 (lock fără tabel DynamoDB).
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Backend-ul se configurează per mediu; fișierul este generat de `make bootstrap`:
  #   terraform init -backend-config=envs/<mediu>.backend.hcl
  backend "s3" {}
}
