terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # State local, ca bootstrap-ul: stack-ul de acces se aplică o singură dată per cont, cu
  # identitatea de admin, înainte să existe orice altă resursă a proiectului.
  # `terraform.tfstate` este gitignorat și rămâne pe mașina operatorului.
}
