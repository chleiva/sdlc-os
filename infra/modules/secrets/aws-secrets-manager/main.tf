# secrets/aws-secrets-manager — provisions empty AWS Secrets Manager secret
# containers + an IAM read policy. No secret *value* is ever set here.
#
# bootstrap.sh's step 3 populates values after this module's apply, via
# `aws secretsmanager put-secret-value` run directly against the CLI/API —
# never through a tofu resource, so no value ever enters a .tf file, a
# .tfvars file, or `tofu state`/`tofu plan` output (spec §14.6, and this
# deliverable's acceptance criterion "no secret ever appears in tofu
# plan/state").

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
  }
}

locals {
  tags = merge(var.tags, {
    "sdlc-auto:environment" = var.environment
    "sdlc-auto:module"      = "secrets/aws-secrets-manager"
    "sdlc-auto:tenant-id"   = coalesce(var.tenant_id, "shared")
  })
}

resource "aws_secretsmanager_secret" "this" {
  for_each = toset(var.secret_names)

  name        = "${var.environment}/${each.value}"
  description = "Container for \"${each.value}\" — value bootstrapped out-of-band by scripts/bootstrap.sh step 3, never by tofu apply."
  kms_key_id  = var.kms_key_arn

  tags = local.tags

  # No aws_secretsmanager_secret_version resource is declared anywhere in
  # this module — that is the deliberate boundary. Populating one here
  # would require a value to flow through a Terraform argument, which is
  # exactly what spec §14.6 forbids.
}

data "aws_iam_policy_document" "read_only" {
  statement {
    sid       = "ReadSecretValues"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [for s in aws_secretsmanager_secret.this : s.arn]
  }
}

resource "aws_iam_policy" "read_only" {
  name        = "${var.environment}-secrets-read-only"
  description = "Read-only access to this environment's secret containers, for runtime injection only (spec §14.6)."
  policy      = data.aws_iam_policy_document.read_only.json
  tags        = local.tags
}

resource "aws_iam_role_policy_attachment" "readers" {
  for_each = toset(var.reader_principal_arns)

  # Assumes each reader_principal_arns entry names an IAM role (IRSA role
  # ARN, one per pod service account) rather than a raw AWS principal, so
  # role_policy_attachment is the correct attachment resource.
  role       = element(split("/", each.value), length(split("/", each.value)) - 1)
  policy_arn = aws_iam_policy.read_only.arn
}
