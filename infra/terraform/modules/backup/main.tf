locals {
  name = "${var.project_name}-${var.environment}-${var.selection_name}"

  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Component   = "backup"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  # Limita inferioară de 90 de zile este impusă de AWS; 0 înseamnă tranziție dezactivată.
  cold_storage_after = var.cold_storage_after_days > 0 ? var.cold_storage_after_days : null
}

# ---------------------------------------------------------------- rol de serviciu
# AWS Backup are nevoie de un rol propriu: seiful nu moștenește permisiunile
# Terraform, iar rolul de execuție al Studio nu poate fi folosit în acest scop.
data "aws_iam_policy_document" "assume_backup" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["backup.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup" {
  name               = "${local.name}-role"
  assume_role_policy = data.aws_iam_policy_document.assume_backup.json
  description        = "Rolul folosit de AWS Backup pentru copiile de siguranta."

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_iam_role_policy_attachment" "restore" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
}

# ---------------------------------------------------------------- seif, plan, selecție
resource "aws_backup_vault" "this" {
  name        = local.name
  kms_key_arn = var.kms_key_arn == "" ? null : var.kms_key_arn

  # În dev vrem ca `terraform destroy` să treacă. În producție, `false` protejează
  # punctele de restaurare de o ștergere accidentală.
  force_destroy = var.force_destroy

  tags = local.tags
}

resource "aws_backup_plan" "this" {
  name = local.name

  rule {
    rule_name         = var.rule_name
    target_vault_name = aws_backup_vault.this.name
    schedule          = var.schedule
    start_window      = var.start_window_minutes
    completion_window = var.completion_window_minutes

    lifecycle {
      cold_storage_after = local.cold_storage_after
      delete_after       = var.retention_days
    }
  }

  tags = local.tags
}

resource "aws_backup_selection" "this" {
  name         = local.name
  plan_id      = aws_backup_plan.this.id
  iam_role_arn = aws_iam_role.backup.arn

  # EFS-ul de home este creat de SageMaker, nu de Terraform. ARN-ul este derivat din
  # atributul `home_efs_file_system_id` al domeniului, deci urmărește domeniul recreat.
  resources = var.resource_arns
}
