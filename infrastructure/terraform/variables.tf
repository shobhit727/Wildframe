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
}

variable "eks_min_size" {
  description = "Minimum number of EKS nodes"
  type        = number
  default     = 3
}

variable "eks_max_size" {
  description = "Maximum number of EKS nodes"
  type        = number
  default     = 20
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

variable "domain_name" {
  description = "Domain name for the platform"
  type        = string
  default     = "wildframe.com"
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
