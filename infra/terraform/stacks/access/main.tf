data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition

  common_tags = merge(
    {
      Project     = var.project_name
      Environment = var.environment
      Repository  = "cnc-digital-twin"
      ManagedBy   = "terraform"
    },
    var.tags,
  )

  role_name = var.role_name == "" ? "${var.project_name}-${var.environment}-operator" : var.role_name

  # Rădăcina contului în trust înseamnă „orice principal din cont care are deja
  # `sts:AssumeRole`", adică identitatea de admin folosită la bootstrap. Nu expune rolul
  # în afara contului.
  trust_principals = length(var.trusted_principal_arns) > 0 ? var.trusted_principal_arns : [
    "arn:${local.partition}:iam::${local.account_id}:root",
  ]

  state_bucket_name = var.state_bucket_name == "" ? "${var.project_name}-terraform-state-${local.account_id}" : var.state_bucket_name

  role_arn = "arn:${local.partition}:iam::${local.account_id}:role/${local.role_name}"

  # Toate rolurile de serviciu create de module încep cu prefixul proiectului.
  project_role_arn_pattern = "arn:${local.partition}:iam::${local.account_id}:role/${var.project_name}-*"
}

# ---------------------------------------------------------------- rolul de operator
data "aws_iam_policy_document" "assume_role" {
  statement {
    sid     = "AllowTrustedPrincipals"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = local.trust_principals
    }

    # Condiția de MFA lipsește complet când `require_mfa` este false, deci rolul se poate
    # asuma fără token. Cu `true`, nicio cerere fără MFA nu este permisă.
    dynamic "condition" {
      for_each = var.require_mfa ? [1] : []

      content {
        test     = "Bool"
        variable = "aws:MultiFactorAuthPresent"
        values   = ["true"]
      }
    }
  }
}

# Rolul cu care se creează resursele proiectului. Fiecare apel API făcut cu o sesiune asumată
# apare în CloudTrail cu acest rol ca principal, separat de identitatea umană de admin.
resource "aws_iam_role" "operator" {
  name = local.role_name
  # IAM acceptă în `description` doar ASCII și Latin-1 (U+0020-U+007E, U+00A1-U+00FF), iar
  # diacriticele românești `ă`, `ș`, `ț` sunt în afara intervalului. De aceea textul e fără ele.
  description = "Rolul cu care se lanseaza resursele proiectului; apare in CloudTrail ca principal al sesiunilor de operator."

  assume_role_policy   = data.aws_iam_policy_document.assume_role.json
  max_session_duration = var.max_session_duration_hours * 3600

  tags = merge(local.common_tags, { Component = "operator-access" })
}

resource "aws_iam_role_policy_attachment" "base" {
  count = length(var.base_managed_policy_arns)

  role       = aws_iam_role.operator.name
  policy_arn = var.base_managed_policy_arns[count.index]
}

resource "aws_iam_role_policy_attachment" "additional" {
  count = length(var.additional_managed_policy_arns)

  role       = aws_iam_role.operator.name
  policy_arn = var.additional_managed_policy_arns[count.index]
}

# ---------------------------------------------------------------- permisiuni IAM
# `PowerUserAccess` lasă în afara lui exact administrarea IAM și a contului, iar Terraform are
# nevoie de ea: creează roluri de serviciu, le atașează politici și le pasează către Lambda,
# SageMaker și EC2. Acesta este singurul loc unde rolul primește permisiuni în plus.
data "aws_iam_policy_document" "operator" {
  statement {
    sid    = "ManageServiceRoles"
    effect = "Allow"

    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:ListRoles",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRoleTags",
      "iam:PutRolePolicy",
      "iam:GetRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:ListRolePolicies",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:CreateInstanceProfile",
      "iam:DeleteInstanceProfile",
      "iam:GetInstanceProfile",
      "iam:ListInstanceProfiles",
      "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:ListInstanceProfilesForRole",
      "iam:TagInstanceProfile",
      "iam:UntagInstanceProfile",
      "iam:ListInstanceProfileTags",
      "iam:CreateServiceLinkedRole",
      "iam:DeleteServiceLinkedRole",
      "iam:GetServiceLinkedRoleDeletionStatus",
      "iam:ListPolicies",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:ListEntitiesForPolicy",
    ]

    # Politicile AWS administrate sunt globale; restul acțiunilor le putem limita la rolurile
    # proiectului.
    resources = ["*"]
  }

  statement {
    sid       = "PassProjectRolesOnly"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [local.project_role_arn_pattern]
  }

  # ---------------------------------------------------------------- garduri
  # Cu `enable_guardrails = false` rolul rămâne la fel de puternic, dar fără aceste limite.
  statement {
    sid    = "DenyPermanentIdentities"
    effect = "Deny"

    actions = [
      "iam:CreateUser",
      "iam:DeleteUser",
      "iam:CreateAccessKey",
      "iam:UpdateAccessKey",
      "iam:CreateLoginProfile",
      "iam:UpdateLoginProfile",
      "iam:CreateSAMLProvider",
      "iam:UpdateSAMLProvider",
      "iam:CreateOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:UpdateAccountPasswordPolicy",
      "iam:DeleteAccountPasswordPolicy",
    ]

    resources = ["*"]
  }

  # Rolul nu își poate lărgi sau slăbi propria politică: schimbările de acces se fac doar cu
  # identitatea de admin, prin stack-ul `access`.
  statement {
    sid    = "DenySelfEscalation"
    effect = "Deny"

    actions = [
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
      "iam:TagRole",
      "iam:DeleteRole",
    ]

    resources = [local.role_arn]
  }

  statement {
    sid    = "DenyAuditTampering"
    effect = "Deny"

    actions = [
      "cloudtrail:StopLogging",
      "cloudtrail:DeleteTrail",
      "cloudtrail:UpdateTrail",
      "cloudtrail:PutEventSelectors",
      "cloudtrail:DeleteEventDataStore",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "DenyAccountLevelChanges"
    effect = "Deny"

    actions = [
      "organizations:*",
      "account:*",
    ]

    resources = ["*"]
  }

  # Bucket-ul de state ține istoricul întregii infrastructuri; nu se șterge din sesiunea de
  # operator. Îl administrează doar stack-ul `bootstrap`.
  statement {
    sid       = "DenyStateBucketDeletion"
    effect    = "Deny"
    actions   = ["s3:DeleteBucket", "s3:PutBucketPolicy"]
    resources = ["arn:${local.partition}:s3:::${local.state_bucket_name}"]
  }
}

resource "aws_iam_role_policy" "operator" {
  name   = "${local.role_name}-permissions"
  role   = aws_iam_role.operator.id
  policy = data.aws_iam_policy_document.operator.json
}
