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

  # Backend configuration. HCL forbids variables inside `backend` blocks, so these
  # literals are the defaults; override per-environment with:
  #   terraform init -backend-config=backend.hcl   (see backend.hcl.example)
  # Keep them in sync with the terraform_state_* / terraform_locks_table variables,
  # which parameterize the state bucket + lock table created below (#356/#357/#358/#413).
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

  # #377: stable tags — no timestamp() so tags do not churn on every plan.
  default_tags {
    tags = merge(var.tags, {
      Application = "wildframe"
      Environment = var.environment
      ManagedBy   = "terraform"
    })
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

# #354: public subnets are for NAT gateways / load balancers ONLY.
# Workloads MUST NOT be placed here — use private subnets (private + NAT egress).
# map_public_ip_on_launch=false is the guardrail so no instance launched here gets
# a public IP implicitly.
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
  account_id         = data.aws_caller_identity.current.account_id
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

# Node role: managed policies are the AWS-recommended minimum for worker nodes.
# #365: SSM path — AmazonSSMManagedInstanceCore enables Session Manager access to
# nodes instead of SSH key pairs. IAM access is further restricted by the principal
# that assumes this role (node instance profile), so SSM sessions are limited to
# operators with sts:AssumeRole permissions on the role.
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

# #365: SSM Session Manager for node access (replaces SSH)
resource "aws_iam_role_policy_attachment" "eks_ssm_core_policy" {
  role       = aws_iam_role.eks_node_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# #390: deploy-time role — separate from runtime roles (IRSA per-service roles are
# created after the cluster OIDC provider exists; see #367 comment on the cluster).
# Trust is the account root until the CI OIDC provider ARN is known; tighten with:
#   Principal: { Federated: "arn:aws:iam::<acct>:oidc-provider/token.actions.githubusercontent.com" }
resource "aws_iam_role" "deploy" {
  name = "wildframe-${var.environment}-deploy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
      }
    ]
  })

  tags = {
    Name = "wildframe-${var.environment}-deploy"
  }
}

# EKS Cluster security group
resource "aws_security_group" "eks_cluster" {
  name        = "wildframe-${var.environment}-eks-cluster"
  description = "Security group for EKS cluster control plane"
  vpc_id      = aws_vpc.main.id

  # #351: restrict egress to the VPC only. The control plane only needs to reach
  # nodes and VPC endpoints inside the VPC; outbound internet for workloads flows
  # through NAT from the node security group, not from this SG.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr_block]
  }

  # #366: ops access to the control plane (443) restricted to ops CIDRs.
  # Only created when ops_management_cidrs is non-empty.
  dynamic "ingress" {
    for_each = length(var.ops_management_cidrs) > 0 ? [1] : []
    content {
      from_port   = 443
      to_port     = 443
      protocol    = "tcp"
      cidr_blocks = var.ops_management_cidrs
      description = "Ops management access to EKS control plane"
    }
  }

  tags = {
    Name = "wildframe-${var.environment}-eks-cluster"
  }
}

# EKS Node Group security group (for workload ingress to data services)
resource "aws_security_group" "eks_nodes" {
  name        = "wildframe-${var.environment}-eks-nodes"
  description = "Security group for EKS managed node group"
  vpc_id      = aws_vpc.main.id

  # #352: egress restricted to the VPC; node internet egress is via NAT (route
  # table), so no 0.0.0.0/0 SG rule is needed.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr_block]
  }

  tags = {
    Name = "wildframe-${var.environment}-eks-nodes"
  }
}

# EKS cluster encryption KMS key (#389)
resource "aws_kms_key" "eks" {
  description             = "KMS key for EKS secret encryption"
  deletion_window_in_days = var.kms_deletion_window_days
  enable_key_rotation     = true

  # #414: admin (account root) vs app-use (cluster role) split.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow EKS cluster role use"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.eks_cluster_role.arn
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncryptFrom",
          "kms:ReEncryptTo",
          "kms:GenerateDataKey",
          "kms:GenerateDataKeyWithoutPlaintext",
          "kms:DescribeKey",
        ]
        Resource = "*"
      },
    ]
  })

  tags = {
    Name = "wildframe-${var.environment}-eks"
  }
}

resource "aws_kms_alias" "eks" {
  name          = "alias/wildframe-${var.environment}-eks"
  target_key_id = aws_kms_key.eks.key_id
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
    # #355/#326/#327/#379: private-only by default; public access is opt-in and
    # always restricted to eks_public_access_cidrs when enabled.
    endpoint_public_access = var.eks_endpoint_public_access
    public_access_cidrs    = var.eks_public_access_cidrs
  }

  # #367: IRSA — after first apply, bootstrap OIDC and attach per-service roles:
  #   eksctl utils associate-iam-oidc-provider --cluster wildframe-<env> --approve
  #   eksctl create iamserviceaccount --cluster wildframe-<env> \
  #     --namespace <ns> --name <svc> --attach-policy-arn <least-privilege-arn>
  # Do NOT attach broad managed policies to the node role for workload access.

  encryption_config {
    provider {
      key_arn = aws_kms_key.eks.arn
    }
    resources = ["secrets"]
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
    aws_iam_role_policy_attachment.eks_service_policy,
  ]
}

# #368: IMDSv2-only launch template for nodes; also pins the node security group
# so data-service SGs can scope ingress to node group members only.
resource "aws_launch_template" "eks_nodes" {
  name = "wildframe-${var.environment}-eks-nodes"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2
  }

  vpc_security_group_ids = [aws_security_group.eks_nodes.id]

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "wildframe-${var.environment}-eks-node"
      Tier = "private"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name = "wildframe-${var.environment}-eks-node"
    }
  }
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

  # #369/#370: rolling upgrade policy — bound by max_unavailable so the node
  # group never drops below capacity during updates. Bounds validated in
  # variables.tf (min >= 1, desired within [min, max]).
  update_config {
    max_unavailable = var.eks_update_max_unavailable
  }

  launch_template {
    id      = aws_launch_template.eks_nodes.id
    version = aws_launch_template.eks_nodes.latest_version
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

# #378: node-level CloudWatch observability (Container Insights) via the EKS
# add-on. Requires an IRSA role for the add-on once the cluster OIDC provider
# exists; enable only after bootstrap (see #367). Alerting SNS topics are
# defined in the cloudwatch_alerts resource group further down.
resource "aws_eks_addon" "cloudwatch_observability" {
  count = var.enable_cloudwatch_observability ? 1 : 0

  cluster_name = aws_eks_cluster.main.name
  addon_name   = "amazon-cloudwatch-observability"
  # service_account_role_arn = <IRSA role ARN>  # set after OIDC bootstrap
  resolve_conflicts_on_create = "OVERWRITE"

  depends_on = [aws_eks_node_group.general]
}

# RDS KMS key
resource "aws_kms_key" "rds" {
  description             = "KMS key for RDS encryption"
  deletion_window_in_days = var.kms_deletion_window_days # #361: parameterized, aligned with retention
  enable_key_rotation     = true

  # #414: admin vs app-use — root is admin; rds.amazonaws.com is granted use
  # actions only (no key admin).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow RDS service use"
        Effect = "Allow"
        Principal = {
          Service = "rds.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncryptFrom",
          "kms:ReEncryptTo",
          "kms:GenerateDataKey",
          "kms:GenerateDataKeyWithoutPlaintext",
          "kms:DescribeKey",
          "kms:CreateGrant",
        ]
        Resource = "*"
      },
    ]
  })

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
    security_groups = [aws_security_group.eks_nodes.id]
    description     = "Allow PostgreSQL access from EKS node group"
  }

  # #352: egress restricted to VPC — Aurora never needs outbound internet; any
  # S3 backup access goes through VPC endpoints.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr_block]
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
  # #371/#372: maintenance windows parameterized
  preferred_backup_window      = var.rds_backup_window
  preferred_maintenance_window = var.rds_maintenance_window
  # #359/#360/#362/#363: prod is deletion-protected, keeps a final snapshot, and
  # cannot be destroyed by terraform. Non-prod is throwaway.
  skip_final_snapshot     = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "wildframe-${var.environment}-final" : null
  deletion_protection     = var.environment == "production"

  db_subnet_group_name            = aws_db_subnet_group.postgres.name
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.postgres.name
  vpc_security_group_ids          = [aws_security_group.postgres.id]

  enabled_cloudwatch_logs_exports = ["postgresql"]

  storage_encrypted = true
  kms_key_id        = aws_kms_key.rds.arn

  lifecycle {
    # #400: never destroy the prod database via terraform
    prevent_destroy = var.environment == "production"
  }

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

  performance_insights_enabled          = true
  performance_insights_retention_period = var.db_performance_insights_retention # #373

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
    security_groups = [aws_security_group.eks_nodes.id]
    description     = "Allow Redis access from EKS node group"
  }

  # #353: egress restricted to VPC — ElastiCache never needs outbound internet.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr_block]
  }

  tags = {
    Name = "wildframe-${var.environment}-redis"
  }
}

# ElastiCache Redis KMS key (#374/#375: at-rest encryption with customer key)
resource "aws_kms_key" "redis" {
  description             = "KMS key for ElastiCache Redis encryption"
  deletion_window_in_days = var.kms_deletion_window_days
  enable_key_rotation     = true

  # #414: admin vs app-use split (service principal gets use actions only)
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow ElastiCache service use"
        Effect = "Allow"
        Principal = {
          Service = "elasticache.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncryptFrom",
          "kms:ReEncryptTo",
          "kms:GenerateDataKey",
          "kms:GenerateDataKeyWithoutPlaintext",
          "kms:DescribeKey",
          "kms:CreateGrant",
        ]
        Resource = "*"
      },
    ]
  })

  tags = {
    Name = "wildframe-${var.environment}-redis"
  }
}

resource "aws_kms_alias" "redis" {
  name          = "alias/wildframe-${var.environment}-redis"
  target_key_id = aws_kms_key.redis.key_id
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

  # #374/#375: customer-managed KMS encryption + maintenance window + snapshots
  at_rest_encryption_enabled = true
  kms_key_id                 = aws_kms_key.redis.arn
  maintenance_window         = var.redis_maintenance_window
  snapshot_retention_limit   = var.redis_snapshot_retention_limit
  snapshot_window            = var.redis_snapshot_window

  log_delivery_configuration {
    destination      = aws_cloudwatch_log_group.redis_slow_log.name
    destination_type = "cloudwatch-logs"
    log_format       = "json"
    log_type         = "slow-log"
  }

  lifecycle {
    # #400: never destroy the prod cache via terraform
    prevent_destroy = var.environment == "production"
  }

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

# ---------------------------------------------------------------------------
# S3 — shared KMS key (#380/#381/#382: BPA, BucketOwnerEnforced, versioning,
# encryption, logging, lifecycle on every bucket)
# ---------------------------------------------------------------------------
resource "aws_kms_key" "s3" {
  description             = "KMS key for S3 bucket encryption"
  deletion_window_in_days = var.kms_deletion_window_days
  enable_key_rotation     = true

  # #414: admin vs app-use — root admin; cloudfront (OAC reads SSE-KMS objects)
  # and cloudtrail (log encryption) get use-only grants.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow CloudFront to read SSE-KMS objects"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.videos.arn
          }
        }
      },
      {
        Sid    = "Allow CloudTrail to encrypt logs"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
        ]
        Resource = "*"
      },
    ]
  })

  tags = {
    Name = "wildframe-${var.environment}-s3"
  }
}

resource "aws_kms_alias" "s3" {
  name          = "alias/wildframe-${var.environment}-s3"
  target_key_id = aws_kms_key.s3.key_id
}

# Access-logging destination bucket (same account; S3 delivers without ACLs)
resource "aws_s3_bucket" "logs" {
  bucket = "wildframe-logs-${var.environment}-${local.account_id}"

  tags = {
    Name = "wildframe-logs-${var.environment}"
  }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
  }
}

# #382: logs are ephemeral by design but still retained >= s3_log_retention_days
# (validated >= 30 in production).
resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    id     = "logs-expiration"
    status = "Enabled"
    expiration {
      days = var.s3_log_retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = var.s3_log_retention_days
    }
  }
}

# S3 bucket for video storage
resource "aws_s3_bucket" "videos" {
  bucket = "wildframe-videos-${var.environment}-${local.account_id}"
  force_destroy = false

  lifecycle {
    # #400: never destroy the videos bucket (contains user content)
    prevent_destroy = var.environment == "production"
  }

  tags = {
    Name = "wildframe-videos-${var.environment}"
  }
}

resource "aws_s3_bucket_public_access_block" "videos" {
  bucket                  = aws_s3_bucket.videos.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "videos" {
  bucket = aws_s3_bucket.videos.id
  rule {
    object_ownership = "BucketOwnerEnforced"
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

# #411/#637: cost lifecycle — hot videos transition to IA/Glacier over time;
# current versions are never expired, only noncurrent (deleted) versions are.
resource "aws_s3_bucket_lifecycle_configuration" "videos" {
  bucket = aws_s3_bucket.videos.id

  rule {
    id     = "videos-cost-lifecycle"
    status = "Enabled"

    transition {
      days          = var.s3_videos_standard_ia_days
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = var.s3_videos_glacier_days
      storage_class = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = var.s3_versions_expiration_days # #382: production minimum validated
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Access logging for videos bucket
resource "aws_s3_bucket_logging" "videos" {
  bucket        = aws_s3_bucket.videos.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "videos/"
}

# #383/#331: OAC bucket policy scoped to the exact CloudFront distribution.
# (See data.aws_iam_policy_document.cloudfront_videos below — defined after the
# distribution resource to break the otherwise circular reference.)
resource "aws_cloudfront_origin_access_control" "videos" {
  name                              = "wildframe-${var.environment}-videos"
  description                       = "OAC for wildframe-${var.environment} videos"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
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

# CloudFront distribution for video delivery
resource "aws_cloudfront_distribution" "videos" {
  origin {
    domain_name = aws_s3_bucket.videos.bucket_regional_domain_name
    origin_id   = "s3-videos"

    origin_access_control_id = aws_cloudfront_origin_access_control.videos.id
  }

  enabled = true

  # #333: attach AWS WAFv2 Web ACL ARN (CLOUDFRONT scope, must be in us-east-1)
  # e.g. with managed rule groups AWSManagedRulesCommonRuleSet +
  # AWSManagedRulesBotControlRuleSet. Leave empty until the ACL exists.
  web_acl_id = var.waf_web_acl_arn

  # #334: custom aliases/certificate — set cloudfront_aliases +
  # cloudfront_certificate_arn (ACM cert must be in us-east-1) to serve a
  # custom domain.
  aliases = var.cloudfront_aliases

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

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # #332: TLS 1.2+ (2021 profile) on the custom certificate; CloudFront's default
  # certificate already enforces TLSv1.2_2021 when no custom cert is supplied.
  dynamic "viewer_certificate" {
    for_each = var.cloudfront_certificate_arn != "" ? [1] : []
    content {
      acm_certificate_arn      = var.cloudfront_certificate_arn
      ssl_support_method       = "sni-only"
      minimum_protocol_version = "TLSv1.2_2021"
    }
  }

  dynamic "viewer_certificate" {
    for_each = var.cloudfront_certificate_arn == "" ? [1] : []
    content {
      cloudfront_default_certificate = true
    }
  }

  tags = {
    Name = "wildframe-videos-${var.environment}"
  }
}

# OAC bucket policy — scoped to the distribution ARN (least privilege)
data "aws_iam_policy_document" "cloudfront_videos" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.videos.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.videos.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "videos" {
  bucket = aws_s3_bucket.videos.id
  policy = data.aws_iam_policy_document.cloudfront_videos.json
}

# #411/#637: recovery-read role is SEPARATE from app write access. App writes
# should use an IRSA role with videos_app_write_policy once OIDC is bootstrapped
# (#367); this role is for ops/backup recovery only.
resource "aws_iam_policy" "videos_app_write_policy" {
  name        = "wildframe-${var.environment}-videos-app-write"
  description = "Least-privilege write access to the videos bucket (app IRSA role)"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = ["${aws_s3_bucket.videos.arn}/*"]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
        ]
        Resource = [aws_s3_bucket.videos.arn]
      },
    ]
  })
}

resource "aws_iam_policy" "videos_recovery_read_policy" {
  name        = "wildframe-${var.environment}-videos-recovery-read"
  description = "Read-only recovery access to the videos bucket"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
        ]
        Resource = ["${aws_s3_bucket.videos.arn}/*"]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketVersioning",
        ]
        Resource = [aws_s3_bucket.videos.arn]
      },
    ]
  })
}

resource "aws_iam_role" "videos_recovery_reader" {
  name = "wildframe-${var.environment}-videos-recovery-reader"

  # Trusted by the account root until IRSA exists; tighten to the ops/CI
  # identity (OIDC federated principal) after bootstrap.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
      }
    ]
  })

  tags = {
    Name = "wildframe-${var.environment}-videos-recovery-reader"
  }
}

resource "aws_iam_role_policy_attachment" "videos_recovery_reader" {
  role       = aws_iam_role.videos_recovery_reader.name
  policy_arn = aws_iam_policy.videos_recovery_read_policy.arn
}

# ---------------------------------------------------------------------------
# Terraform state backend resources (#356/#357/#358/#413): the bucket and lock
# table are managed by the same module that stores its state in them. Backend
# config itself is static HCL (see comment at top of file + backend.hcl.example).
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "terraform_state" {
  bucket        = var.terraform_state_bucket
  force_destroy = false

  lifecycle {
    # #400: the state bucket is the source of truth — never destroy it
    prevent_destroy = var.environment == "production"
  }

  tags = {
    Name = "wildframe-terraform-state"
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket                  = aws_s3_bucket.terraform_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    id     = "state-noncurrent-expiration"
    status = "Enabled"

    noncurrent_version_expiration {
      noncurrent_days = var.state_versions_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_logging" "terraform_state" {
  bucket        = aws_s3_bucket.terraform_state.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "terraform-state/"
}

resource "aws_dynamodb_table" "terraform_locks" {
  name         = var.terraform_locks_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name = "wildframe-terraform-locks"
  }
}

# ---------------------------------------------------------------------------
# Audit & monitoring (#409/#410/#415/#416)
# ---------------------------------------------------------------------------
# VPC Flow Logs → CloudWatch (retention parameterized)
resource "aws_cloudwatch_log_group" "vpc_flow_logs" {
  name              = "/aws/vpc/wildframe-${var.environment}/flow-logs"
  retention_in_days = var.vpc_flow_log_retention

  tags = {
    Name = "wildframe-${var.environment}-vpc-flow-logs"
  }
}

resource "aws_iam_role" "vpc_flow_logs" {
  name = "wildframe-${var.environment}-vpc-flow-logs"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "vpc-flow-logs.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name = "wildframe-${var.environment}-vpc-flow-logs"
  }
}

# ARN-scoped: the flow-logs role may only write to its own log group (#390)
resource "aws_iam_role_policy" "vpc_flow_logs" {
  name = "wildframe-${var.environment}-vpc-flow-logs"
  role = aws_iam_role.vpc_flow_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [
          aws_cloudwatch_log_group.vpc_flow_logs.arn,
          "${aws_cloudwatch_log_group.vpc_flow_logs.arn}:log-stream:*",
        ]
      }
    ]
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

# CloudTrail — multi-region, validated, encrypted, with management + data events
# (#415/#416). Data events on S3/KMS are noisy; keep them while costs allow,
# drop the KMS selector if the bill spikes (see OPERATIONS.md).
resource "aws_s3_bucket" "cloudtrail" {
  bucket        = "wildframe-cloudtrail-${var.environment}-${local.account_id}"
  force_destroy = false

  tags = {
    Name = "wildframe-cloudtrail-${var.environment}"
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket                  = aws_s3_bucket.cloudtrail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.s3.arn
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    id     = "cloudtrail-retention"
    status = "Enabled"

    expiration {
      days = var.cloudtrail_retention_days # #409: production minimum >= 365 validated
    }

    noncurrent_version_expiration {
      noncurrent_days = var.cloudtrail_retention_days
    }
  }
}

resource "aws_s3_bucket_logging" "cloudtrail" {
  bucket        = aws_s3_bucket.cloudtrail.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "cloudtrail-access/"
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AWSCloudTrailAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.cloudtrail.arn
      },
      {
        Sid    = "AWSCloudTrailWrite"
        Effect = "Allow"
        Principal = {
          Service = "cloudtrail.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.cloudtrail.arn}/cloudtrail/AWSLogs/${local.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
    ]
  })
}

resource "aws_cloudtrail" "main" {
  name                          = "wildframe-${var.environment}"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  s3_key_prefix                 = "cloudtrail"
  is_multi_region_trail         = true
  enable_log_file_validation    = true
  include_global_service_events = true
  kms_key_id                    = aws_kms_key.s3.arn
  is_organization_trail         = false

  # Management events (read + write)
  advanced_event_selector {
    name = "management-events"

    field_selector {
      field  = "eventCategory"
      equals = ["Management"]
    }
  }

  # S3 object-level data events
  advanced_event_selector {
    name = "s3-data-events"

    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }

    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
  }

  # KMS key data events
  advanced_event_selector {
    name = "kms-data-events"

    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }

    field_selector {
      field  = "resources.type"
      equals = ["AWS::KMS::Key"]
    }
  }

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

# GuardDuty (#410): detector + findings routed to SNS via EventBridge
resource "aws_guardduty_detector" "main" {
  enable                       = true
  finding_publishing_frequency = "FIFTEEN_MINUTES"

  tags = {
    Name = "wildframe-${var.environment}"
  }
}

resource "aws_sns_topic" "guardduty_alerts" {
  name = "wildframe-${var.environment}-guardduty-alerts"

  tags = {
    Name = "wildframe-${var.environment}-guardduty-alerts"
  }
}

resource "aws_sns_topic_policy" "guardduty_alerts" {
  arn = aws_sns_topic.guardduty_alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEventBridgePublish"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
        Action   = "sns:Publish"
        Resource = aws_sns_topic.guardduty_alerts.arn
      }
    ]
  })
}

resource "aws_cloudwatch_event_rule" "guardduty" {
  name = "wildframe-${var.environment}-guardduty-findings"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
  })
}

resource "aws_cloudwatch_event_target" "guardduty" {
  rule      = aws_cloudwatch_event_rule.guardduty.name
  arn       = aws_sns_topic.guardduty_alerts.arn
  target_id = "guardduty-sns"
}

# Alerting scaffold (#378): email subscription for GuardDuty findings and the
# CloudWatch alerts topic (alarms publish here — add aws_cloudwatch_metric_alarm
# resources pointing at this topic as alert rules are defined).
resource "aws_sns_topic" "cloudwatch_alerts" {
  name = "wildframe-${var.environment}-cloudwatch-alerts"

  tags = {
    Name = "wildframe-${var.environment}-cloudwatch-alerts"
  }
}

resource "aws_sns_topic_subscription" "guardduty_email" {
  count     = var.alerts_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.guardduty_alerts.arn
  protocol  = "email"
  endpoint  = var.alerts_email
}

resource "aws_sns_topic_subscription" "cloudwatch_email" {
  count     = var.alerts_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.cloudwatch_alerts.arn
  protocol  = "email"
  endpoint  = var.alerts_email
}

# ---------------------------------------------------------------------------
# Secrets Manager scaffold (#440/#442/#443/#444/#445)
# Full rotation (Lambda) intentionally not implemented: it requires deploying a
# rotation function + scheduler; document the flow and enable once the Lambda
# exists:
#   resource "aws_secretsmanager_secret_rotation" "db_master" {
#     secret_id           = aws_secretsmanager_secret.db_master.id
#     rotation_lambda_arn = <lambda>
#     rotation_rules {
#       automatically_after_days = 30
#     }
#   }
# Also add aws_lambda_permission + secretsmanager:RotateSecret grant to the
# Lambda execution role. The RDS master password is managed by terraform today
# (random_password), so rotate by replacing the value here, not via the API.
# ---------------------------------------------------------------------------
resource "aws_kms_key" "secrets" {
  description             = "KMS key for Secrets Manager"
  deletion_window_in_days = var.kms_deletion_window_days
  enable_key_rotation     = true

  # #414: admin-only key — secrets are read through IAM, no service principal
  # needs direct use of this key.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${local.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
    ]
  })

  tags = {
    Name = "wildframe-${var.environment}-secrets"
  }
}

resource "aws_kms_alias" "secrets" {
  name          = "alias/wildframe-${var.environment}-secrets"
  target_key_id = aws_kms_key.secrets.key_id
}

resource "aws_secretsmanager_secret" "db_master" {
  name                    = "wildframe-${var.environment}/db-master"
  description             = "RDS master credentials for wildframe-${var.environment}"
  kms_key_id              = aws_kms_key.secrets.arn
  recovery_window_in_days = var.secrets_recovery_window_days

  tags = {
    Name = "wildframe-${var.environment}-db-master"
  }
}

resource "aws_secretsmanager_secret_version" "db_master" {
  secret_id = aws_secretsmanager_secret.db_master.id

  secret_string = jsonencode({
    username = var.db_master_username
    password = random_password.db_master_password.result
  })
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
