# observability — Prometheus, Grafana, Loki, Tempo, cloud-agnostic (spec
# §14.3, §16.4). No per-cloud variant: this deploys via Helm only.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\"."
}

variable "namespace" {
  type        = string
  description = "Kubernetes namespace for the observability stack."
  default     = "observability"
}

variable "metrics_retention" {
  type        = string
  description = "Prometheus metrics retention window (e.g. \"15d\")."
  default     = "15d"
}

variable "storage_class" {
  type        = string
  description = "StorageClass for Prometheus/Loki/Tempo persistent volumes. Left to the environment composition since it is cluster/cloud-specific (e.g. gp3 on AWS EBS CSI)."
}

variable "grafana_admin_password_secret_name" {
  type        = string
  description = <<-EOT
    Logical name of the secret (from the secrets module's `secret_ids`
    output) holding the Grafana admin password. The password VALUE is
    never a variable here (spec §14.6) — only its secret-store reference,
    injected into the Grafana pod at runtime via a secret volume/env-from,
    never through a tofu resource argument.
  EOT
}

# --- External Secrets Operator: syncs the AWS Secrets Manager entry into
# the Kubernetes Secret `grafana_admin_password_secret_name` names, which
# the kube-prometheus-stack helm_release above already references via
# `grafana.admin.existingSecret` (closes infra/README.md known-gap #6).
# See README.md for the exact contract and what's still an assumption.

variable "external_secrets_enabled" {
  type        = bool
  description = <<-EOT
    Whether to declare the `SecretStore`/`ExternalSecret` CRD instances
    that sync the Grafana admin password. Default false: a
    `kubernetes_manifest` for a CRD External Secrets Operator (ESO)
    defines will fail at `tofu plan`/`apply` time against any cluster
    where ESO's controller (assumed already installed cluster-wide, same
    KEDA/Karpenter-controller assumption pattern this repo already uses
    — see gpu-node-pool/aws's README) isn't actually running yet, so
    this is opt-in rather than always-on.
  EOT
  default     = false
}

variable "aws_region" {
  type        = string
  description = "AWS region the SecretStore's `provider.aws.region` targets. Required when external_secrets_enabled = true (validated via a `precondition`, not a variable validation block, to stay on OpenTofu >= 1.6)."
  default     = null
}

variable "secret_store_name" {
  type        = string
  description = "Name of the (namespaced) SecretStore this module declares."
  default     = "aws-secrets-manager"
}

variable "eso_service_account_name" {
  type        = string
  description = "Name of the ServiceAccount ESO's SecretStore authenticates as (IRSA)."
  default     = "external-secrets-grafana"
}

variable "eso_service_account_role_arn" {
  type        = string
  description = <<-EOT
    IAM role ARN to annotate the ESO ServiceAccount with
    (`eks.amazonaws.com/role-arn`, IRSA) so it can call
    `secretsmanager:GetSecretValue` for the Grafana admin-password
    secret. `null` (the default) creates the ServiceAccount with no IRSA
    annotation — it will exist, but ESO will have no real AWS
    credentials to authenticate with, so the sync will fail at runtime.
    The environment composition is expected to create this role
    (attaching the secrets module's own `access_policy_arn` output) and
    pass its ARN in — see environments/pilot-aws-g7e/eks.tf's
    `aws_iam_role.external_secrets_grafana` for the reference
    implementation.
  EOT
  default     = null
}

variable "grafana_admin_username" {
  type        = string
  description = <<-EOT
    Grafana admin username. Templated as a static, non-secret value on
    the ExternalSecret's target (see README "Why admin-user is not
    synced from Secrets Manager") rather than read from the secret
    store, since infra/scripts/lib/seed-secrets.sh stores one opaque
    string per logical secret name, not a JSON object with separate
    username/password properties. Only the password is a genuine
    synced secret.
  EOT
  default     = "admin"
}

variable "grafana_admin_password_remote_key" {
  type        = string
  description = <<-EOT
    AWS Secrets Manager secret name (or ARN) holding the Grafana admin
    password value. `null` (the default) derives
    "$${var.environment}/$${var.grafana_admin_password_secret_name}",
    matching modules/secrets/aws-secrets-manager's own naming
    convention (`"$${environment}/$${logical_name}"`) exactly, so this
    only needs overriding if a caller provisions that secret under a
    different name/module.
  EOT
  default     = null
}

variable "external_secret_refresh_interval" {
  type        = string
  description = "ExternalSecret `refreshInterval` -- how often ESO re-polls Secrets Manager for a rotated value."
  default     = "1h"
}

variable "tags" {
  type        = map(string)
  description = "Common resource labels applied to every resource this module creates."
  default     = {}
}
