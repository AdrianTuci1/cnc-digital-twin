terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # Volumul de date trăiește mai mult decât instanța, dar state-ul lui stă tot local, ca la
  # stack-ul `sim`: nu se comită. Pentru lucru în echipă, mutați-l într-un backend S3.
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile == "" ? null : var.aws_profile

  default_tags {
    tags = local.common_tags
  }
}
