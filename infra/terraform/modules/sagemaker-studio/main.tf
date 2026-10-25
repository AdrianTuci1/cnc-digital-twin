locals {
  tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Component   = "ml-platform"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  domain_name     = var.domain_name == "" ? "${var.project_name}-${var.environment}" : var.domain_name
  use_default_vpc = var.vpc_id == ""

  # Imaginea publică SageMaker Distribution; API-ul cere un ARN explicit pentru JupyterLab.
  jupyter_lab_image_arn = var.jupyter_lab_image_arn == "" ? (
    "arn:aws:sagemaker:${var.region}:885854791233:image/sagemaker-distribution-cpu"
  ) : var.jupyter_lab_image_arn

  # Aplicațiile Studio se închid singure după inactivitate.
  # Aceasta este funcția nativă a serviciului, nu un script de lifecycle.
  idle_settings = {
    lifecycle_management    = var.enable_idle_shutdown ? "ENABLED" : "DISABLED"
    idle_timeout_in_minutes = var.idle_timeout_minutes
  }
}

data "aws_vpc" "default" {
  count   = local.use_default_vpc ? 1 : 0
  default = true
}

data "aws_subnets" "selected" {
  count = length(var.subnet_ids) == 0 ? 1 : 0

  filter {
    name   = "vpc-id"
    values = [local.use_default_vpc ? data.aws_vpc.default[0].id : var.vpc_id]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

locals {
  vpc_id     = local.use_default_vpc ? data.aws_vpc.default[0].id : var.vpc_id
  subnet_ids = length(var.subnet_ids) > 0 ? var.subnet_ids : data.aws_subnets.selected[0].ids
}

# ---------------------------------------------------------------- rețea
# Domeniul Studio montează EFS prin NFS. Traficul rămâne în interiorul grupului.
resource "aws_security_group" "studio" {
  name_prefix = "${local.domain_name}-studio-"
  # EC2 acceptă doar ASCII în descrierea grupului de securitate.
  description = "SageMaker Studio: acces NFS catre EFS si trafic de iesire."
  vpc_id      = local.vpc_id

  tags = merge(local.tags, { Name = "${local.domain_name}-studio-sg" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "nfs_self" {
  security_group_id            = aws_security_group.studio.id
  description                  = "NFS in interiorul domeniului Studio"
  from_port                    = 2049
  to_port                      = 2049
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.studio.id

  tags = local.tags
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.studio.id
  description       = "Trafic de iesire pentru acces la S3, ECR si endpoint-uri"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"

  tags = local.tags
}

# ---------------------------------------------------------------- IAM
data "aws_iam_policy_document" "assume_sagemaker" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "studio" {
  name               = "${local.domain_name}-studio-role"
  assume_role_policy = data.aws_iam_policy_document.assume_sagemaker.json
  description        = "Rolul de executie al domeniului SageMaker Studio."

  tags = local.tags
}

# Politica administrată acoperă ECR, CloudWatch, job-urile și endpoint-urile.
resource "aws_iam_role_policy_attachment" "sagemaker_full" {
  role       = aws_iam_role.studio.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"
}

data "aws_iam_policy_document" "studio_inline" {
  statement {
    sid    = "DataLakeAccess"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]

    resources = compact([
      var.data_lake_bucket_arn,
      "${var.data_lake_bucket_arn}/*",
      var.assets_bucket_arn,
      var.assets_bucket_arn == "" ? "" : "${var.assets_bucket_arn}/*",
    ])
  }

  # Corpusul de antrenament stă în alt stack și se citește doar; scrierea rămâne la colector.
  dynamic "statement" {
    for_each = length(var.read_only_bucket_arns) > 0 ? [1] : []

    content {
      sid    = "ReadTrainingData"
      effect = "Allow"

      actions = [
        "s3:GetObject",
        "s3:ListBucket",
      ]

      resources = flatten([
        for arn in var.read_only_bucket_arns : [arn, "${arn}/*"]
      ])
    }
  }

  # Doar citire: catalogul Glue este registru de schemă, nu motor de transformare.
  statement {
    sid    = "ReadCatalogAndQuery"
    effect = "Allow"

    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:BatchGetPartition",
    ]

    resources = ["*"]
  }

  # Control de cost: blochează tipurile de instanță care nu sunt aprobate.
  # `ForAnyValue:StringNotEquals` refuză cererea dacă orice tip solicitat iese din listă.
  statement {
    sid    = "DenyUnapprovedTrainingInstances"
    effect = "Deny"

    actions = [
      "sagemaker:CreateTrainingJob",
      "sagemaker:CreateHyperParameterTuningJob",
      "sagemaker:CreateProcessingJob",
    ]

    resources = ["*"]

    condition {
      test     = "ForAnyValue:StringNotEquals"
      variable = "sagemaker:InstanceTypes"
      values   = var.training_instance_types
    }
  }
}

resource "aws_iam_role_policy" "studio_inline" {
  name   = "${local.domain_name}-studio-inline"
  role   = aws_iam_role.studio.id
  policy = data.aws_iam_policy_document.studio_inline.json
}

# ---------------------------------------------------------------- domeniu
resource "aws_sagemaker_domain" "this" {
  domain_name = local.domain_name
  auth_mode   = "IAM"
  vpc_id      = local.vpc_id
  subnet_ids  = local.subnet_ids
  kms_key_id  = var.kms_key_arn == "" ? null : var.kms_key_arn

  default_user_settings {
    execution_role  = aws_iam_role.studio.arn
    security_groups = [aws_security_group.studio.id]

    # Interfața principală Studio: JupyterLab, cu oprire automată pe inactivitate.
    jupyter_lab_app_settings {
      default_resource_spec {
        # API-ul curent nu mai acceptă „system” pentru JupyterLab; aplicația rulează pe
        # același tip mic ca kernelul și se oprește pe inactivitate.
        instance_type       = var.default_kernel_instance_type
        sagemaker_image_arn = local.jupyter_lab_image_arn
      }

      app_lifecycle_management {
        idle_settings {
          lifecycle_management        = local.idle_settings.lifecycle_management
          idle_timeout_in_minutes     = local.idle_settings.idle_timeout_in_minutes
          min_idle_timeout_in_minutes = var.idle_timeout_minutes
          max_idle_timeout_in_minutes = var.idle_timeout_minutes
        }
      }
    }

    kernel_gateway_app_settings {
      default_resource_spec {
        instance_type = var.default_kernel_instance_type
      }
    }

    # Notebook-urile partajate rămân vizibile în echipă.
    sharing_settings {
      notebook_output_option = "Allowed"
    }
  }

  domain_settings {
    security_group_ids = [aws_security_group.studio.id]
  }

  default_space_settings {
    execution_role  = aws_iam_role.studio.arn
    security_groups = [aws_security_group.studio.id]
  }

  # EFS-ul de home rămâne la ștergerea domeniului, ca să nu pierdem notebook-uri.
  retention_policy {
    home_efs_file_system = "Retain"
  }

  tags = local.tags
}

# JupyterLab rulează doar într-un spațiu privat; profilul de utilizator singur nu mai este
# suficient pentru API-ul curent. Spațiul poartă numele profilului și îi aparține.
resource "aws_sagemaker_space" "users" {
  for_each = toset(var.user_profiles)

  domain_id = aws_sagemaker_domain.this.id

  # Numele spațiului trebuie să fie unic în domeniu și diferit de profilul de utilizator.
  space_name         = "${each.value}-space"
  space_display_name = each.value

  space_settings {
    app_type = "JupyterLab"

    jupyter_lab_app_settings {
      default_resource_spec {
        instance_type       = var.default_kernel_instance_type
        sagemaker_image_arn = local.jupyter_lab_image_arn
      }

      app_lifecycle_management {
        idle_settings {
          idle_timeout_in_minutes = var.idle_timeout_minutes
        }
      }
    }

  }

  space_sharing_settings {
    sharing_type = "Private"
  }

  ownership_settings {
    owner_user_profile_name = each.value
  }

  tags = local.tags

  depends_on = [aws_sagemaker_user_profile.users]
}

resource "aws_sagemaker_user_profile" "users" {
  for_each = toset(var.user_profiles)

  domain_id         = aws_sagemaker_domain.this.id
  user_profile_name = each.value

  user_settings {
    execution_role  = aws_iam_role.studio.arn
    security_groups = [aws_security_group.studio.id]

    jupyter_lab_app_settings {
      default_resource_spec {
        # API-ul curent nu mai acceptă „system” pentru JupyterLab; aplicația rulează pe
        # același tip mic ca kernelul și se oprește pe inactivitate.
        instance_type       = var.default_kernel_instance_type
        sagemaker_image_arn = local.jupyter_lab_image_arn
      }

      app_lifecycle_management {
        idle_settings {
          lifecycle_management        = local.idle_settings.lifecycle_management
          idle_timeout_in_minutes     = local.idle_settings.idle_timeout_in_minutes
          min_idle_timeout_in_minutes = var.idle_timeout_minutes
          max_idle_timeout_in_minutes = var.idle_timeout_minutes
        }
      }
    }

    kernel_gateway_app_settings {
      default_resource_spec {
        instance_type = var.default_kernel_instance_type
      }
    }
  }

  tags = local.tags
}
