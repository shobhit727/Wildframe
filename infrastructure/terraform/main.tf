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
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
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
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

resource "aws_subnet" "public" {
  count = length(var.availability_zones)

  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "wildframe-${var.environment}-public-${count.index + 1}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  count = length(var.availability_zones)

  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = {
    Name = "wildframe-${var.environment}-private-${count.index + 1}"
    Tier = "private"
  }
}

resource "aws_eip" "nat" {
  count = length(var.availability_zones)

  domain = "vpc"

  tags = {
    Name = "wildframe-${var.environment}-nat-${count.index + 1}"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count = length(var.availability_zones)

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "wildframe-${var.environment}-${count.index + 1}"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "wildframe-${var.environment}-public"
  }
}

resource "aws_route_table_association" "public" {
  count = length(var.availability_zones)

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count = length(var.availability_zones)

  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = {
    Name = "wildframe-${var.environment}-private-${count.index + 1}"
  }
}

resource "aws_route_table_association" "private" {
  count = length(var.availability_zones)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

locals {
  public_subnet_ids  = aws_subnet.public[*].id
  private_subnet_ids = aws_subnet.private[*].id
}

# EKS IAM Roles
resource "aws_iam_role" "eks_cluster_role" {
  name = "wildframe-${var.environment}-eks-cluster"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "eks.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "wildframe-${var.environment}-eks-cluster"
  }
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  role       = aws_iam_role.eks_cluster_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_iam_role_policy_attachment" "eks_service_policy" {
  role       = aws_iam_role.eks_cluster_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSServicePolicy"
}

resource "aws_iam_role" "eks_node_role" {
  name = "wildframe-${var.environment}-eks-node"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "wildframe-${var.environment}-eks-node"
  }
}

resource "aws_iam_role_policy_attachment" "eks_node_policy" {
  role       = aws_iam_role.eks_node_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "eks_cni_policy" {
  role       = aws_iam_role.eks_node_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "eks_ecr_read_only_policy" {
  role       = aws_iam_role.eks_node_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# EKS Cluster security group
resource "aws_security_group" "eks_cluster" {
  name        = "wildframe-${var.environment}-eks-cluster"
  description = "Security group for EKS cluster control plane"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "wildframe-${var.environment}-eks-cluster"
  }
}

# EKS Cluster
resource "aws_eks_cluster" "main" {
  name            = "wildframe-${var.environment}"
  role_arn        = aws_iam_role.eks_cluster_role.arn
  version         = var.kubernetes_version
  vpc_config {
    subnet_ids              = local.private_subnet_ids
    security_group_ids      = [aws_security_group.eks_cluster.id]
    endpoint_private_access = true
    endpoint_public_access  = true
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
    aws_iam_role_policy_attachment.eks_service_policy,
  ]
}

# EKS Node Group
resource "aws_eks_node_group" "general" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "wildframe-general-${var.environment}"
  node_role_arn   = aws_iam_role.eks_node_role.arn
  subnet_ids      = local.private_subnet_ids
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
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_ecr_read_only_policy,
  ]

  tags = {
    Name = "wildframe-general-${var.environment}"
  }
}

# RDS KMS key
resource "aws_kms_key" "rds" {
  description             = "KMS key for RDS encryption"
  deletion_window_in_days = 10
  enable_key_rotation     = true

  tags = {
    Name = "wildframe-${var.environment}-rds"
  }
}

resource "aws_kms_alias" "rds" {
  name          = "alias/wildframe-${var.environment}-rds"
  target_key_id = aws_kms_key.rds.key_id
}

# RDS master password
resource "random_password" "db_master_password" {
  length           = var.db_master_password_length
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

# RDS subnet group
resource "aws_db_subnet_group" "postgres" {
  name       = "wildframe-${var.environment}"
  subnet_ids = local.private_subnet_ids

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

# RDS cluster parameter group
resource "aws_rds_cluster_parameter_group" "postgres" {
  name        = "wildframe-${var.environment}"
  family      = "aurora-postgresql14"
  description = "Wildframe Aurora PostgreSQL cluster parameter group"

  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

# RDS security group
resource "aws_security_group" "postgres" {
  name        = "wildframe-${var.environment}-postgres"
  description = "Security group for Aurora PostgreSQL"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_cluster.id]
    description     = "Allow PostgreSQL access from EKS cluster"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "wildframe-${var.environment}-postgres"
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

# ElastiCache subnet group
resource "aws_elasticache_subnet_group" "redis" {
  name       = "wildframe-${var.environment}"
  subnet_ids = local.private_subnet_ids

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

# ElastiCache parameter group
resource "aws_elasticache_parameter_group" "redis" {
  name        = "wildframe-${var.environment}"
  family      = "redis7"
  description = "Wildframe Redis parameter group"

  parameter {
    name  = "maxmemory-policy"
    value = "allkeys-lru"
  }

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

# ElastiCache security group
resource "aws_security_group" "redis" {
  name        = "wildframe-${var.environment}-redis"
  description = "Security group for ElastiCache Redis"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.eks_cluster.id]
    description     = "Allow Redis access from EKS cluster"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "wildframe-${var.environment}-redis"
  }
}

# ElastiCache slow log group
resource "aws_cloudwatch_log_group" "redis_slow_log" {
  name              = "/aws/elasticache/wildframe-${var.environment}/slow-log"
  retention_in_days = 7

  tags = {
    Name = "wildframe-${var.environment}-redis-slow-log"
  }
}

# ElastiCache Redis replication group
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "wildframe-${var.environment}"
  description          = "Wildframe Redis cluster"
  engine               = "redis"
  engine_version       = var.redis_version
  node_type            = var.redis_node_type
  port                 = 6379

  parameter_group_name = aws_elasticache_parameter_group.redis.name
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [aws_security_group.redis.id]

  num_cache_clusters         = var.redis_num_nodes
  multi_az_enabled           = var.redis_multi_az
  automatic_failover_enabled = true

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true

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

# S3 KMS key
resource "aws_kms_key" "s3" {
  description             = "KMS key for S3 bucket encryption"
  deletion_window_in_days = 10
  enable_key_rotation     = true

  tags = {
    Name = "wildframe-${var.environment}-s3"
  }
}

resource "aws_kms_alias" "s3" {
  name          = "alias/wildframe-${var.environment}-s3"
  target_key_id = aws_kms_key.s3.key_id
}

# CloudFront Origin Access Identity for video bucket
resource "aws_cloudfront_origin_access_identity" "videos" {
  comment = "OAI for wildframe-${var.environment} videos"
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
