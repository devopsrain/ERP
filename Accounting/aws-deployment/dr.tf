# ── Disaster Recovery & High-Availability additions ───────────────────────────
# Apply alongside main.tf:
#   cd aws-deployment
#   terraform apply -var-file=production.tfvars
#
# What this module adds:
#   1. RDS Multi-AZ        — automatic standby + failover < 30 s
#   2. RDS Read Replica    — offload SIEM queries and report generation
#   3. KMS key             — dedicated encryption key for RDS volumes
#   4. Enhanced monitoring — 10-second CloudWatch metrics for the primary DB
#   5. S3 cross-region replication — backup archives replicated to a second region
#   6. CloudWatch alarms   — CPU, storage, and replica-lag alerts
#   7. SSM parameters      — publish replica endpoint for app config auto-discovery

# ── Variables ─────────────────────────────────────────────────────────────────

variable "secondary_region" {
  description = "Secondary AWS region for DR (S3 replica bucket + future standby)"
  default     = "eu-west-1"   # Ireland — well-connected to af-south-1 Cape Town
}

variable "db_password" {
  description = "RDS master password — override via TF_VAR_db_password env var"
  type        = string
  sensitive   = true
  default     = "ChangeMe!InProduction1"
}

variable "enable_multi_az" {
  description = "Enable RDS Multi-AZ standby (auto-failover < 30 s)"
  type        = bool
  default     = true
}

variable "enable_read_replica" {
  description = "Create a read replica for SIEM + report queries"
  type        = bool
  default     = true
}

variable "enable_s3_replication" {
  description = "Enable cross-region S3 replication for backup archives"
  type        = bool
  default     = true
}

variable "alarm_sns_arn" {
  description = "SNS topic ARN for CloudWatch alarm notifications (email / PagerDuty)"
  type        = string
  default     = ""
}

# ── Secondary region provider ─────────────────────────────────────────────────
# Required for the S3 replication destination bucket.
provider "aws" {
  alias  = "secondary"
  region = var.secondary_region
}

# ── 1. KMS key for RDS encryption ─────────────────────────────────────────────

resource "aws_kms_key" "rds" {
  description             = "${var.project_name} RDS encryption key"
  deletion_window_in_days = 14
  enable_key_rotation     = true

  tags = {
    Name        = "${var.project_name}-rds-kms"
    Environment = var.environment
  }
}

resource "aws_kms_alias" "rds" {
  name          = "alias/${var.project_name}-rds"
  target_key_id = aws_kms_key.rds.key_id
}

# ── 2. RDS Enhanced Monitoring IAM role ───────────────────────────────────────

resource "aws_iam_role" "rds_monitoring" {
  name = "${var.project_name}-rds-monitoring"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "rds_monitoring" {
  role       = aws_iam_role.rds_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# ── 3. RDS Primary (Multi-AZ HA) ──────────────────────────────────────────────
# This overrides the single-AZ aws_db_instance.main in main.tf.
# In practice: set multi_az = true directly in main.tf and add the options below.
# Shown here as a separate named resource for clarity and for new deployments.

resource "aws_db_instance" "primary" {
  identifier     = "${var.project_name}-db-ha"
  engine         = "postgres"
  engine_version = "16.6"
  # Upgrade from t3.small — Multi-AZ requires at least t3.medium for failover IOPS
  instance_class = "db.t3.medium"

  allocated_storage     = 100
  max_allocated_storage = 500
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.rds.arn

  db_name  = "ethiopian_business"
  username = "business_admin"
  password = var.db_password

  # ── High Availability ──────────────────────────────────────────────────────
  multi_az = var.enable_multi_az   # Standby replica in a different AZ; auto-failover

  vpc_security_group_ids = [aws_security_group.database.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name

  # ── Backup / recovery ──────────────────────────────────────────────────────
  backup_retention_period   = 14              # 14-day point-in-time recovery window
  backup_window             = "03:00-04:00"
  maintenance_window        = "sun:04:00-sun:05:00"
  copy_tags_to_snapshot     = true
  deletion_protection       = true            # prevent accidental drops in production
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.project_name}-db-final"

  # ── Enhanced monitoring (10-second metric granularity in CloudWatch) ────────
  monitoring_interval = 10
  monitoring_role_arn = aws_iam_role.rds_monitoring.arn

  # ── Performance Insights (slow query analysis) ─────────────────────────────
  performance_insights_enabled          = true
  performance_insights_retention_period = 7

  tags = {
    Name        = "${var.project_name}-db-ha"
    Environment = var.environment
    Role        = "primary"
  }
}

# ── 4. RDS Read Replica ───────────────────────────────────────────────────────
# Offloads heavy read workloads:
#   • SIEM event queries (large table scans)
#   • Trial balance / income statement / balance sheet generation
#   • Report exports
#
# App config: set READ_DATABASE_URL to the replica endpoint.
# In async_db.py: use the read pool for SELECT-only queries.

resource "aws_db_instance" "read_replica" {
  count = var.enable_read_replica ? 1 : 0

  identifier          = "${var.project_name}-db-replica"
  instance_class      = "db.t3.small"       # smaller — read-only traffic
  replicate_source_db = aws_db_instance.primary.identifier

  storage_encrypted = true
  kms_key_id        = aws_kms_key.rds.arn

  # Read replicas cannot specify backup_retention_period
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false

  vpc_security_group_ids = [aws_security_group.database.id]

  tags = {
    Name        = "${var.project_name}-db-replica"
    Environment = var.environment
    Role        = "read-replica"
  }
}

# Publish replica endpoint as an SSM SecureString so app containers
# can discover it without hard-coding addresses in environment variables.
resource "aws_ssm_parameter" "read_db_url" {
  count = var.enable_read_replica ? 1 : 0

  name  = "/${var.project_name}/${var.environment}/READ_DATABASE_URL"
  type  = "SecureString"
  value = join("", [
    "postgresql://",
    aws_db_instance.primary.username, ":",
    var.db_password, "@",
    aws_db_instance.read_replica[0].address,
    ":5432/",
    aws_db_instance.primary.db_name,
  ])

  tags = { Environment = var.environment }
}

# ── 5. S3 Cross-Region Replication ───────────────────────────────────────────
# Replicates backup archives and DOCX exports to a second region.
# Recovery: if af-south-1 is unavailable, access backups from secondary bucket.

# Versioning must be enabled on both source and destination buckets.
resource "aws_s3_bucket_versioning" "main_versioning" {
  bucket = aws_s3_bucket.main.id
  versioning_configuration { status = "Enabled" }
}

# Destination bucket in secondary region
resource "aws_s3_bucket" "backup_replica" {
  count    = var.enable_s3_replication ? 1 : 0
  provider = aws.secondary
  bucket   = "${var.project_name}-backup-dr-${var.secondary_region}"

  tags = {
    Name        = "${var.project_name}-backup-dr"
    Environment = var.environment
    Role        = "s3-replication-target"
  }
}

resource "aws_s3_bucket_versioning" "replica_versioning" {
  count    = var.enable_s3_replication ? 1 : 0
  provider = aws.secondary
  bucket   = aws_s3_bucket.backup_replica[0].id
  versioning_configuration { status = "Enabled" }
}

# IAM role used by S3 to replicate objects
resource "aws_iam_role" "s3_replication" {
  count = var.enable_s3_replication ? 1 : 0
  name  = "${var.project_name}-s3-replication"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "s3_replication" {
  count = var.enable_s3_replication ? 1 : 0
  role  = aws_iam_role.s3_replication[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
        Resource = [aws_s3_bucket.main.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObjectVersionForReplication",
                    "s3:GetObjectVersionAcl",
                    "s3:GetObjectVersionTagging"]
        Resource = ["${aws_s3_bucket.main.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
        Resource = ["${aws_s3_bucket.backup_replica[0].arn}/*"]
      },
    ]
  })
}

resource "aws_s3_bucket_replication_configuration" "main" {
  count  = var.enable_s3_replication ? 1 : 0
  bucket = aws_s3_bucket.main.id
  role   = aws_iam_role.s3_replication[0].arn

  rule {
    id     = "replicate-all"
    status = "Enabled"
    filter { prefix = "" }   # replicate everything

    destination {
      bucket        = aws_s3_bucket.backup_replica[0].arn
      storage_class = "STANDARD_IA"   # cheaper for DR copies
    }

    delete_marker_replication { status = "Enabled" }
  }

  depends_on = [
    aws_s3_bucket_versioning.main_versioning,
    aws_s3_bucket_versioning.replica_versioning,
  ]
}

# ── 6. CloudWatch alarms ──────────────────────────────────────────────────────

locals {
  alarm_actions = var.alarm_sns_arn != "" ? [var.alarm_sns_arn] : []
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${var.project_name}-rds-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 120
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "RDS CPU > 80% for 4 minutes — consider scaling up"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.primary.id }
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "rds_storage_low" {
  alarm_name          = "${var.project_name}-rds-storage-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 10737418240   # 10 GB in bytes
  alarm_description   = "RDS free storage < 10 GB"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.primary.id }
  alarm_actions       = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "replica_lag" {
  count               = var.enable_read_replica ? 1 : 0
  alarm_name          = "${var.project_name}-replica-lag"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "ReplicaLag"
  namespace           = "AWS/RDS"
  period              = 60
  statistic           = "Average"
  threshold           = 30   # seconds
  alarm_description   = "Read replica lag > 30 s — reports may show stale data"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.read_replica[0].id }
  alarm_actions       = local.alarm_actions
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "ha_db_endpoint" {
  value       = aws_db_instance.primary.address
  description = "Primary HA RDS endpoint (read/write)"
}

output "read_replica_endpoint" {
  value       = var.enable_read_replica ? aws_db_instance.read_replica[0].address : null
  description = "Read replica endpoint — set READ_DATABASE_URL to this value"
}

output "backup_dr_bucket" {
  value       = var.enable_s3_replication ? aws_s3_bucket.backup_replica[0].id : null
  description = "Cross-region DR backup bucket in ${var.secondary_region}"
}

output "rds_kms_key_arn" {
  value       = aws_kms_key.rds.arn
  description = "KMS key ARN used for RDS volume encryption"
}
