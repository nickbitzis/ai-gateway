terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Partial config: bucket, key, region and lock table come from
  # -backend-config=backend.hcl (see backend.hcl.example).
  backend "s3" {}
}
