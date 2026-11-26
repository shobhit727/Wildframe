# Cloudflare edge for Wildframe (ADR-0001 option B, issue #1109).
#
# This is a separate Terraform root from ../ (the AWS root). It manages only
# Cloudflare zone and Access configuration, so a plan here can never touch
# EKS, Aurora, S3 or CloudFront, and the AWS root never needs a Cloudflare
# token.
#
# Nothing in this directory has been applied to any account. See README.md
# before the first plan.

terraform {
  required_version = ">= 1.6"

  required_providers {
    cloudflare = {
      source = "cloudflare/cloudflare"
      # v5 is the current major. Resource names and attribute shapes here
      # (cloudflare_zone_setting, cloudflare_dns_record, `rules = [...]`
      # lists) are v5-only and do not exist in v4. Schemas were checked
      # against the provider source at v5.29.0. Commit the
      # .terraform.lock.hcl that the first `terraform init` writes.
      version = "~> 5.29"
    }
  }

  # Same state bucket and lock table as the AWS root, separate key. Both are
  # created by ../main.tf behind `manage_terraform_state_bucket`.
  backend "s3" {
    bucket         = "wildframe-terraform-state"
    key            = "cloudflare/edge.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

# Credentials come from the environment only: CLOUDFLARE_API_TOKEN.
# Never put a token in a .tfvars file or in this directory. README.md lists
# the minimum token permissions.
provider "cloudflare" {}
