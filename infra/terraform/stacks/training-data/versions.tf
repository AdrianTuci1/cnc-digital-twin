terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # Corpusul de antrenament trăiește mai mult decât orice instanță, dar state-ul lui stă tot
  # local, ca la stack-urile `sim` și `sim-data`: nu se comită. Pentru lucru în echipă, mutați-l
  # într-un backend S3, cu `terraform init -migrate-state`.
}
