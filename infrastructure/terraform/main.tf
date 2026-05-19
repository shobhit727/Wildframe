"""
Terraform root module for Wildframe infrastructure.
Defines all infrastructure resources for the OTT platform.
"""

terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.10"
    }
  }

  backend "s3" {
    bucket         = "wildframe-terraform-state"
    key            = "prod/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Application = "wildframe"
      Environment = var.environment
      ManagedBy   = "terraform"
      CreatedAt   = timestamp()
    }
  }
}

provider "kubernetes" {
  host                   = aws_eks_cluster.main.endpoint
  cluster_ca_certificate = base64decode(aws_eks_cluster.main.certificate_authority[0].data)
  token                  = data.aws_eks_cluster_auth.main.token
}

provider "helm" {
  kubernetes {
    host                   = aws_eks_cluster.main.endpoint
    cluster_ca_certificate = base64decode(aws_eks_cluster.main.certificate_authority[0].data)
    token                  = data.aws_eks_cluster_auth.main.token
  }
}

# VPC for Wildframe
module "vpc" {
  source = "./modules/vpc"

  name_prefix        = "wildframe"
  cidr_block         = var.vpc_cidr_block
  availability_zones = var.availability_zones
  environment        = var.environment
}

# EKS Cluster
resource "aws_eks_cluster" "main" {
  name            = "wildframe-${var.environment}"
  role_arn        = aws_iam_role.eks_cluster_role.arn
  version         = var.kubernetes_version
  vpc_config {
    subnet_ids              = module.vpc.private_subnet_ids
    security_groups         = [aws_security_group.eks_cluster.id]
    endpoint_private_access = true
    endpoint_public_access  = true
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
  ]
}

# EKS Node Group
resource "aws_eks_node_group" "general" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "wildframe-general-${var.environment}"
  node_role_arn   = aws_iam_role.eks_node_role.arn
  subnet_ids      = module.vpc.private_subnet_ids
  version         = var.kubernetes_version

  scaling_config {
    desired_size = var.eks_desired_size
    max_size     = var.eks_max_size
    min_size     = var.eks_min_size
  }

  instance_types = var.eks_instance_types
  disk_size      = var.eks_disk_size

  depends_on = [
    aws_iam_role_policy_attachment.eks_node_policy,
  ]

  tags = {
    Name = "wildframe-general-${var.environment}"
  }
}

# RDS PostgreSQL Cluster
resource "aws_rds_cluster" "postgres" {
  cluster_identifier      = "wildframe-${var.environment}"
  engine                  = "aurora-postgresql"
  engine_version          = var.postgres_version
  database_name           = "wildframe"
  master_username         = var.db_master_username
  master_password         = random_password.db_master_password.result
  backup_retention_period = var.db_backup_retention
  preferred_backup_window = "03:00-04:00"
  skip_final_snapshot     = var.environment != "production"

  db_subnet_group_name            = aws_db_subnet_group.postgres.name
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.postgres.name
  vpc_security_group_ids          = [aws_security_group.postgres.id]

  enabled_cloudwatch_logs_exports = ["postgresql"]

  storage_encrypted = true
  kms_key_id        = aws_kms_key.rds.arn

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

resource "aws_rds_cluster_instance" "postgres" {
  count              = var.db_instance_count
  cluster_identifier = aws_rds_cluster.postgres.id
  instance_class     = var.db_instance_class
  engine             = aws_rds_cluster.postgres.engine
  engine_version     = aws_rds_cluster.postgres.engine_version

  performance_insights_enabled = true

  tags = {
    Name = "wildframe-${var.environment}-${count.index + 1}"
  }
}

# ElastiCache Redis
resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "wildframe-${var.environment}"
  engine               = "redis"
  node_type            = var.redis_node_type
  num_cache_nodes      = var.redis_num_nodes
  parameter_group_name = aws_elasticache_parameter_group.redis.name
  engine_version       = var.redis_version
  port                 = 6379

  subnet_group_name = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  automatic_failover_enabled = true
  multi_az_enabled          = var.redis_multi_az

  log_delivery_configuration {
    destination      = aws_cloudwatch_log_group.redis_slow_log.name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "slow-log"
  }

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

# S3 bucket for video storage
resource "aws_s3_bucket" "videos" {
  bucket = "wildframe-videos-${var.environment}-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "wildframe-videos-${var.environment}"
  }
}

resource "aws_s3_bucket_versioning" "videos" {
  bucket = aws_s3_bucket.videos.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "videos" {
  bucket = aws_s3_bucket.videos.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
  }
}

# CloudFront distribution for video delivery
resource "aws_cloudfront_distribution" "videos" {
  origin {
    domain_name = aws_s3_bucket.videos.bucket_regional_domain_name
    origin_id   = "s3-videos"

    s3_origin_config {
      origin_access_identity = aws_cloudfront_origin_access_identity.videos.cloudfront_access_identity_path
    }
  }

  enabled = true

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "s3-videos"

    forwarded_values {
      query_string = false

      cookies {
        forward = "none"
      }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 0
    default_ttl            = 3600
    max_ttl                = 86400
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Name = "wildframe-videos-${var.environment}"
  }
}

# ACM certificate for API
resource "aws_acm_certificate" "api" {
  domain_name       = "api.${var.domain_name}"
  validation_method = "DNS"

  tags = {
    Name = "wildframe-api-${var.environment}"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Data source for current AWS account
data "aws_caller_identity" "current" {}

# Data source for EKS cluster auth
data "aws_eks_cluster_auth" "main" {
  name = aws_eks_cluster.main.name
}
