# Terraform variables for Wildframe infrastructure

variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (development, staging, production)"
  type        = string
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Environment must be development, staging, or production."
  }
}

variable "vpc_cidr_block" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones for resources"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "kubernetes_version" {
  description = "Kubernetes version for EKS"
  type        = string
  default     = "1.28"
}

variable "eks_desired_size" {
  description = "Desired number of EKS nodes"
  type        = number
  default     = 5
  validation {
    # #369: desired must stay within the [min, max] bounds
    condition     = var.eks_desired_size >= var.eks_min_size && var.eks_desired_size <= var.eks_max_size
    error_message = "eks_desired_size must be between eks_min_size and eks_max_size."
  }
}

variable "eks_min_size" {
  description = "Minimum number of EKS nodes"
  type        = number
  default     = 3
  validation {
    condition     = var.eks_min_size >= 1
    error_message = "eks_min_size must be at least 1."
  }
}

variable "eks_max_size" {
  description = "Maximum number of EKS nodes"
  type        = number
  default     = 20
  validation {
    # #369: max must not dip below min (prevents impossible scaling configs)
    condition     = var.eks_max_size >= var.eks_min_size
    error_message = "eks_max_size must be >= eks_min_size."
  }
}

variable "eks_instance_types" {
  description = "Instance types for EKS nodes"
  type        = list(string)
  default     = ["t3.xlarge", "t3.2xlarge"]
}

variable "eks_disk_size" {
  description = "Disk size for EKS nodes in GB"
  type        = number
  default     = 100
}

variable "eks_endpoint_public_access" {
  description = "Whether the EKS cluster API endpoint is publicly reachable. #355: default private-only; public is opt-in and gated by eks_public_access_cidrs."
  type        = bool
  default     = false
}

variable "eks_public_access_cidrs" {
  description = "CIDRs allowed to reach the public EKS API endpoint. #327/#379: must be a narrow, explicitly-managed list when public access is enabled."
  type        = list(string)
  default     = ["0.0.0.0/0"]
  validation {
    # #326: if public access is on, do not allow unrestricted 0.0.0.0/0
    condition     = !var.eks_endpoint_public_access || (!contains(var.eks_public_access_cidrs, "0.0.0.0/0") && length(var.eks_public_access_cidrs) > 0)
    error_message = "When eks_endpoint_public_access is true, eks_public_access_cidrs must be a non-empty list of narrow CIDRs (no 0.0.0.0/0)."
  }
}

variable "ops_management_cidrs" {
  description = "CIDRs allowed to reach the EKS control plane on 443 (ops/break-glass access). #366"
  type        = list(string)
  default     = ["10.0.0.0/16"]
}

variable "eks_update_max_unavailable" {
  description = "Max nodes unavailable during node group updates. #370: keep >= 1 so rolling upgrades never strand the cluster below min capacity."
  type        = number
  default     = 1
  validation {
    condition     = var.eks_update_max_unavailable >= 1
    error_message = "eks_update_max_unavailable must be >= 1."
  }
}

variable "enable_cloudwatch_observability" {
  description = "Install the amazon-cloudwatch-observability addon (Container Insights). #378: requires IRSA bootstrap first (see cluster comment); keep false until the OIDC provider and addon role exist."
  type        = bool
  default     = false
}

variable "db_instance_count" {
  description = "Number of RDS instances in cluster"
  type        = number
  default     = 2
}

variable "db_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.r6g.xlarge"
}

variable "db_master_username" {
  description = "RDS master username"
  type        = string
  sensitive   = true
  default     = "postgres"
}

variable "db_backup_retention" {
  description = "RDS backup retention period in days"
  type        = number
  default     = 30
  validation {
    # #359: production must retain >= 7 days of backups
    condition     = var.environment != "production" || var.db_backup_retention >= 7
    error_message = "db_backup_retention must be >= 7 days in production."
  }
}

variable "db_performance_insights_retention" {
  description = "Performance Insights retention in days (7-731). #373"
  type        = number
  default     = 7
  validation {
    condition     = var.db_performance_insights_retention >= 7 && var.db_performance_insights_retention <= 731
    error_message = "db_performance_insights_retention must be between 7 and 731 days."
  }
}

variable "rds_backup_window" {
  description = "Preferred RDS backup window (UTC). #371"
  type        = string
  default     = "03:00-04:00"
}

variable "rds_maintenance_window" {
  description = "Preferred RDS maintenance window (UTC, ddd:hh24:mi-ddd:hh24:mi). #372"
  type        = string
  default     = "sun:04:00-sun:05:00"
}

variable "postgres_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "14.9"
}

variable "redis_node_type" {
  description = "Redis node type"
  type        = string
  default     = "cache.r6g.xlarge"
}

variable "redis_num_nodes" {
  description = "Number of Redis nodes"
  type        = number
  default     = 3
}

variable "redis_version" {
  description = "Redis engine version"
  type        = string
  default     = "7.0"
}

variable "redis_multi_az" {
  description = "Enable multi-AZ for Redis"
  type        = bool
  default     = true
}

variable "redis_maintenance_window" {
  description = "Preferred ElastiCache maintenance window (UTC). #374"
  type        = string
  default     = "sun:05:00-sun:06:00"
}

variable "redis_snapshot_retention_limit" {
  description = "Days to retain automatic Redis snapshots (0 disables). #375"
  type        = number
  default     = 7
  validation {
    condition     = var.redis_snapshot_retention_limit >= 0 && var.redis_snapshot_retention_limit <= 35
    error_message = "redis_snapshot_retention_limit must be between 0 and 35 days."
  }
}

variable "redis_snapshot_window" {
  description = "Daily time range (UTC) for Redis automatic snapshots. #375"
  type        = string
  default     = "02:00-03:00"
}

variable "domain_name" {
  description = "Domain name for the platform"
  type        = string
  default     = "wildframe.com"
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets. #354: public subnets host NAT gateways / load balancers only — workloads must stay in private subnets (map_public_ip_on_launch=false is enforced on the resource)."
  type        = list(string)
  default     = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]
}

variable "db_master_password_length" {
  description = "Length of the generated RDS master password"
  type        = number
  default     = 32
}

variable "kms_deletion_window_days" {
  description = "KMS key deletion window in days (7-30). #361: aligned across all keys; keep high for prod so keys cannot be deleted quickly."
  type        = number
  default     = 30
  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "kms_deletion_window_days must be between 7 and 30."
  }
}

variable "s3_log_retention_days" {
  description = "Retention for the access-logs bucket. #382: production must retain >= 30 days."
  type        = number
  default     = 90
  validation {
    condition     = var.environment != "production" || var.s3_log_retention_days >= 30
    error_message = "s3_log_retention_days must be >= 30 in production."
  }
}

variable "s3_videos_standard_ia_days" {
  description = "Days until videos transition to STANDARD_IA. #411"
  type        = number
  default     = 30
}

variable "s3_videos_glacier_days" {
  description = "Days until videos transition to GLACIER. #411"
  type        = number
  default     = 90
}

variable "s3_versions_expiration_days" {
  description = "Days after which noncurrent versions of videos expire. #382/#411/#637: current versions are never auto-expired; only deleted-version history is pruned."
  type        = number
  default     = 90
  validation {
    condition     = var.s3_versions_expiration_days >= 7
    error_message = "s3_versions_expiration_days must be >= 7."
  }
}

variable "state_versions_expiration_days" {
  description = "Days after which noncurrent versions of the state bucket expire. #382: production minimum 90."
  type        = number
  default     = 90
  validation {
    condition     = var.environment != "production" || var.state_versions_expiration_days >= 90
    error_message = "state_versions_expiration_days must be >= 90 in production."
  }
}

variable "cloudtrail_retention_days" {
  description = "Retention for CloudTrail logs in days. #409: production minimum 365."
  type        = number
  default     = 365
  validation {
    condition     = var.environment != "production" || var.cloudtrail_retention_days >= 365
    error_message = "cloudtrail_retention_days must be >= 365 in production."
  }
}

variable "vpc_flow_log_retention" {
  description = "Retention for VPC Flow Logs in days. #409"
  type        = number
  default     = 90
  validation {
    condition     = var.vpc_flow_log_retention >= 1 && var.vpc_flow_log_retention <= 3653
    error_message = "vpc_flow_log_retention must be between 1 and 3653 days."
  }
}

variable "waf_web_acl_arn" {
  description = "ARN of a WAFv2 Web ACL (CLOUDFRONT scope, us-east-1) to attach to the CloudFront distribution. #333: leave empty until the ACL exists; recommended managed rules: AWSManagedRulesCommonRuleSet + AWSManagedRulesBotControlRuleSet."
  type        = string
  default     = ""
}

variable "cloudfront_aliases" {
  description = "Custom CNAME aliases for the CloudFront distribution. #334: requires cloudfront_certificate_arn."
  type        = list(string)
  default     = []
  validation {
    condition     = length(var.cloudfront_aliases) == 0 || var.cloudfront_certificate_arn != ""
    error_message = "cloudfront_aliases requires cloudfront_certificate_arn to be set."
  }
}

variable "cloudfront_certificate_arn" {
  description = "ACM certificate ARN (must be in us-east-1) for custom CloudFront aliases. #334"
  type        = string
  default     = ""
}

variable "terraform_state_bucket" {
  description = "S3 bucket name for the terraform state backend. #356/#357/#358/#413: created in IaC below; keep in sync with the backend \"s3\" block / backend.hcl."
  type        = string
  default     = "wildframe-terraform-state"
}

variable "terraform_state_key" {
  description = "State file key inside the backend bucket."
  type        = string
  default     = "prod/terraform.tfstate"
}

variable "terraform_state_region" {
  description = "Region of the state bucket (used by backend.hcl / -backend-config)."
  type        = string
  default     = "us-east-1"
}

variable "terraform_locks_table" {
  description = "DynamoDB table for state locking. Created in IaC below."
  type        = string
  default     = "terraform-locks"
}

variable "alerts_email" {
  description = "Email to subscribe to GuardDuty/CloudWatch alert SNS topics. Empty disables email subscriptions. #410/#378"
  type        = string
  default     = ""
}

variable "secrets_recovery_window_days" {
  description = "Recovery window for deleted Secrets Manager secrets (7-30). #443"
  type        = number
  default     = 30
  validation {
    condition     = var.secrets_recovery_window_days >= 7 && var.secrets_recovery_window_days <= 30
    error_message = "secrets_recovery_window_days must be between 7 and 30."
  }
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default = {
    Project     = "Wildframe"
    ManagedBy   = "Terraform"
    CostCenter  = "Engineering"
  }
}
