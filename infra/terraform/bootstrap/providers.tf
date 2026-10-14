provider "aws" {
  region  = var.region
  profile = var.aws_profile == "" ? null : var.aws_profile
}
