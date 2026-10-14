terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

# Stack-ul de bootstrap creează backend-ul de state pentru celelalte stack-uri.
# Se aplică o singură dată per cont, cu state local (acest director este gitignorat parțial:
# fișierul terraform.tfstate rămâne local și nu se comite).
