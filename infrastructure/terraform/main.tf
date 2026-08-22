# Terraform root module for Wildframe infrastructure.
# Defines all infrastructure resources for the OTT platform.

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
  map_public_ip_on_launch = false

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

# KMS key for EKS secret envelope encryption (#389).
resource "aws_kms_key" "eks_secrets" {
  description             = "Cluster secrets encryption at rest"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name = "wildframe-${var.environment}-eks-secrets"
  }
}

resource "aws_kms_alias" "eks_secrets" {
  name          = "alias/wildframe-${var.environment}-eks-secrets"
  target_key_id = aws_kms_key.eks_secrets.key_id
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
    cidr_blocks = [var.vpc_cidr_block]
    description = "Egress limited to the VPC (#351)"
  }

  tags = {
    Name = "wildframe-${var.environment}-eks-cluster"
  }
}

# EKS Cluster
resource "aws_eks_cluster" "main" {
  name     = "wildframe-${var.environment}"
  role_arn = aws_iam_role.eks_cluster_role.arn
  version  = var.kubernetes_version
  vpc_config {
    subnet_ids              = local.private_subnet_ids
    security_group_ids      = [aws_security_group.eks_cluster.id]
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.eks_public_access_cidrs
  }

  # Envelope-encrypt Kubernetes Secrets with a customer-managed key (#389);
  # external secret management (ExternalSecrets/SM) remains the policy for
  # application credentials — this covers anything that still lands in etcd.
  encryption_config {
    resources = ["secrets"]
    provider {
      key_arn = aws_kms_key.eks_secrets.arn
    }
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
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name = "wildframe-${var.environment}-rds"
  }
}

resource "aws_kms_alias" "rds" {
  name          = "alias/wildframe-${var.environment}-rds"
  target_key_id = aws_kms_key.rds.key_id
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

  # Aggregate connection budget: sum of all service pool sizes (max + overflow) across
  # all replicas must stay below max_connections. Enforce per-service limits in app config.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  # #429/#430: enforce statement/lock/idle timeouts to prevent unbounded connection hold
  parameter {
    name  = "statement_timeout"
    value = "30000"
  }

  parameter {
    name  = "lock_timeout"
    value = "10000"
  }

  parameter {
    name  = "idle_in_transaction_session_timeout"
    value = "60000"
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

  # No egress rule: databases never initiate outbound connections (#352).
  tags = {
    Name = "wildframe-${var.environment}-postgres"
  }
}

# RDS PostgreSQL Cluster
resource "aws_rds_cluster" "postgres" {
  cluster_identifier = "wildframe-${var.environment}"
  engine             = "aurora-postgresql"
  engine_version     = var.postgres_version
  database_name      = "wildframe"
  master_username    = var.db_master_username
  # Master password is managed by RDS in Secrets Manager — never materialized
  # in Terraform state (#328). Rotation is handled by the secrets manager.
  manage_master_user_password = true
  backup_retention_period     = var.db_backup_retention
  preferred_backup_window     = "03:00-04:00"

  # Deletion protection + final snapshot in production (#362/#364/#400).
  deletion_protection       = var.environment == "production"
  skip_final_snapshot       = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "wildframe-${var.environment}-final" : null
  copy_tags_to_snapshot     = true

  # Aurora maintenance window declared explicitly (#371).
  preferred_maintenance_window = "sun:05:00-sun:06:00"

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
  # Explicit retention control (#373): 7 days is the free-tier maximum.
  performance_insights_retention_period = 7
  # Instance maintenance window declared explicitly (#372).
  preferred_maintenance_window = "sun:05:00-sun:06:00"
  copy_tags_to_snapshot        = true

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

  # No egress rule (#353).
  tags = {
    Name = "wildframe-${var.environment}-redis"
  }
}

# ElastiCache slow log group
resource "aws_cloudwatch_log_group" "redis_slow_log" {
  name              = "/aws/elasticache/wildframe-${var.environment}/slow-log"
  retention_in_days = 30

  tags = {
    Name = "wildframe-${var.environment}-redis-slow-log"
  }
}

# ElastiCache Redis replication group
resource "aws_kms_key" "redis" {
  description             = "KMS key (CMK) for ElastiCache at-rest encryption (#375)"
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name = "wildframe-${var.environment}-redis"
  }
}

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
  kms_key_id                 = aws_kms_key.redis.arn

  # Snapshot policy declared explicitly (#374).
  snapshot_retention_limit = 7
  snapshot_window          = "02:00-03:00"

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

# Block every public-access path on the video bucket (#382/#330).
resource "aws_s3_bucket_public_access_block" "videos" {
  bucket = aws_s3_bucket.videos.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Bucket-owner enforcement for objects written by CloudFront/OAC (#382).
resource "aws_s3_bucket_ownership_controls" "videos" {
  bucket = aws_s3_bucket.videos.id

  rule {
    object_ownership = "BucketOwnerEnforced"
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
  deletion_window_in_days = 30
  enable_key_rotation     = true

  tags = {
    Name = "wildframe-${var.environment}-s3"
  }
}

resource "aws_kms_alias" "s3" {
  name          = "alias/wildframe-${var.environment}-s3"
  target_key_id = aws_kms_key.s3.key_id
}
# CloudFront response headers policy with security headers (#384)
resource "aws_cloudfront_response_headers_policy" "security_headers" {
  name    = "wildframe-security-headers"
  comment = "Security headers for Wildframe CDN (HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy)"

  security_headers_config {
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    xss_protection {
      protection = true
      mode_block = true
      override   = true
    }
  }
}

# CloudFront Origin Access Control (modern replacement for legacy OAI, #331)
resource "aws_cloudfront_origin_access_control" "videos" {
  name                              = "wildframe-${var.environment}-videos-oac"
  description                       = "OAC for the videos bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# CloudFront distribution for video delivery
resource "aws_cloudfront_distribution" "videos" {
  origin {
    domain_name = aws_s3_bucket.videos.bucket_regional_domain_name
    origin_id   = "s3-videos"

    origin_access_control_id = aws_cloudfront_origin_access_control.videos.id
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

    viewer_protocol_policy     = "redirect-to-https"
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security_headers.id
    min_ttl                    = 0
    default_ttl                = 3600
    max_ttl                    = 86400
  }

  # AWS WAF association (#332): managed common rules + rate limiting.
  web_acl_id = aws_wafv2_web_acl.videos.arn

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Explicit certificate policy (#333): use the platform ACM cert when
  # provided; otherwise fall back to the CloudFront default with TLS 1.2+.
  viewer_certificate {
    acm_certificate_arn            = var.cloudfront_acm_certificate_arn != "" ? var.cloudfront_acm_certificate_arn : null
    cloudfront_default_certificate = var.cloudfront_acm_certificate_arn == ""
    minimum_protocol_version       = "TLSv1.2_2021"
    ssl_support_method             = var.cloudfront_acm_certificate_arn != "" ? "sni-only" : null
  }

  tags = {
    Name = "wildframe-videos-${var.environment}"
  }
}

# WAF for the video distribution (#332).
resource "aws_wafv2_web_acl" "videos" {
  name  = "wildframe-${var.environment}-videos-waf"
  scope = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "wildframe-common-rules"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 2

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "wildframe-known-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "RateLimitPerIP"
    priority = 10

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 10000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "wildframe-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "wildframe-videos-waf"
    sampled_requests_enabled   = true
  }

  tags = {
    Name = "wildframe-${var.environment}-videos-waf"
  }
}

# Bucket policy granting ONLY the distribution's OAC access (#334).
resource "aws_s3_bucket_policy" "videos" {
  bucket = aws_s3_bucket.videos.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CloudFrontOACRead"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.videos.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.videos.arn
          }
        }
      }
    ]
  })

  depends_on = [
    aws_s3_bucket_public_access_block.videos,
    aws_s3_bucket_ownership_controls.videos,
  ]
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

# VPC flow logs (all traffic) to CloudWatch (#409).
resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  name              = "/aws/vpc/wildframe-${var.environment}-flow-logs"
  retention_in_days = 90

  tags = {
    Name = "wildframe-${var.environment}-vpc-flow-logs"
  }
}

resource "aws_iam_role" "vpc_flow_logs" {
  name = "wildframe-${var.environment}-vpc-flow-logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "vpc_flow_logs" {
  name = "wildframe-${var.environment}-vpc-flow-logs"
  role = aws_iam_role.vpc_flow_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogStreams",
      ]
      Resource = "${aws_cloudwatch_log_group.vpc_flow_logs.arn}:*"
    }]
  })
}

resource "aws_flow_log" "main" {
  iam_role_arn    = aws_iam_role.vpc_flow_logs.arn
  log_destination = aws_cloudwatch_log_group.vpc_flow_logs.arn
  traffic_type    = "ALL"
  vpc_id          = aws_vpc.main.id

  tags = {
    Name = "wildframe-${var.environment}-vpc-flow-logs"
  }
}

# Data source for current AWS account
data "aws_caller_identity" "current" {}

# Data source for EKS cluster auth
data "aws_eks_cluster_auth" "main" {
  name = aws_eks_cluster.main.name
}

# ---------------------------------------------------------------------------
# Terraform state hardening (#380/#356/#329).
#
# The S3 backend block references these by name; the resources themselves are
# declared here behind a flag so the bootstrap account can manage them with
# the same reviewed configuration. Run once with
# `-var="manage_terraform_state_bucket=true"`, then flip back to false.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "terraform_state" {
  count = var.manage_terraform_state_bucket ? 1 : 0

  bucket = "wildframe-terraform-state"

  tags = {
    Name        = "wildframe-terraform-state"
    Environment = "shared"
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  count = var.manage_terraform_state_bucket ? 1 : 0

  bucket = aws_s3_bucket.terraform_state[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  count = var.manage_terraform_state_bucket ? 1 : 0

  bucket = aws_s3_bucket.terraform_state[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  count = var.manage_terraform_state_bucket ? 1 : 0

  bucket = aws_s3_bucket.terraform_state[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "terraform_locks" {
  count = var.manage_terraform_state_bucket ? 1 : 0

  name         = "terraform-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Name        = "terraform-locks"
    Environment = "shared"
  }
}
