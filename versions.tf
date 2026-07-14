terraform {
  required_version = ">= 1.11.0"

  # Values are supplied with `terraform init -backend-config=backend.hcl`.
  # The state bucket is bootstrapped separately from this stack.
  backend "s3" {}

  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }

    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
