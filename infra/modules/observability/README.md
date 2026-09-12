# observability

Prometheus, Grafana, Loki, Tempo — cloud-agnostic, deployed via their
public Helm charts (spec §14.3, §16.4). Real, `tofu validate`-clean HCL.

## Grafana admin-password sync (External Secrets Operator)

This module references a Kubernetes Secret by name
(`grafana.admin.existingSecret`, set to `var.grafana_admin_password_secret_name`)
for Grafana's admin credentials, and (new in this pass, `external_secrets_enabled
= true`) declares the `SecretStore` + `ExternalSecret` CRD instances that
actually populate that Secret from the AWS Secrets Manager entry
`modules/secrets/aws-secrets-manager` provisions a container for. This
closes `infra/README.md` known-gap #6 ("Grafana admin-password secret is
referenced by name but nothing syncs it").

### Prerequisite: ESO's controller

Same pattern as `gpu-node-pool/aws`'s Karpenter assumption and this
deliverable's KEDA assumption: **External Secrets Operator's controller
(CRDs + controller Deployment) is assumed already installed on the
cluster**, cluster-wide, via its own Helm chart — not installed by this
module. This module only declares the per-namespace `SecretStore` and
`ExternalSecret` CRD instances that tell an already-running ESO
controller what to sync.

### IRSA wiring

The `SecretStore` authenticates to AWS via IRSA: a `ServiceAccount` this
module creates (`var.eso_service_account_name`), annotated with
`eks.amazonaws.com/role-arn` = `var.eso_service_account_role_arn`. This
module does **not** create that IAM role itself (an IAM role is not a
Kubernetes-namespaced concept the way everything else here is) — the
environment composition is expected to create it and attach
`modules/secrets/aws-secrets-manager`'s own `access_policy_arn` output
(the exact least-privilege "read these secret containers" policy that
module already produces), then pass the resulting role ARN in. See
`environments/pilot-aws-g7e/eks.tf`'s `aws_iam_role.external_secrets_grafana`
for the reference implementation — it carries the identical "IRSA trust
policy narrowed via the cluster's OIDC provider in a real apply" caveat
`aws_iam_role.karpenter_controller` in that same file already documents,
since neither this build nor that one has a live cluster/OIDC provider
to narrow the trust policy against.

If `eso_service_account_role_arn` is left `null` (the default), the
ServiceAccount is still created but carries no IRSA annotation — ESO
will have no real AWS credentials and the sync will fail at runtime.
This is a deliberate "wire it or see it fail loudly", not a silent gap.

### Why `admin-user` is not synced from Secrets Manager

Grafana's chart wants both an `admin-user` and an `admin-password` key
in the target Secret. The natural design would sync both from one JSON-
shaped Secrets Manager entry (`{"admin-user": "...", "admin-password":
"..."}`, read via `remoteRef.property`). This module does **not** do
that, because it isn't what this repo's actual secret-seeding path
produces: `infra/scripts/lib/seed-secrets.sh` (verified by reading it
before writing this) calls `aws secretsmanager put-secret-value
--secret-string "$VALUE"` with one plain opaque string per logical
secret name — there is no JSON-property structure to read a sub-key out
of. Inventing a `property: admin-user` read against a secret that will
actually contain a single flat string would silently fail (or read
garbage) once someone actually seeds it via the existing script.

Instead: `admin-user` is a static, non-secret value templated directly
onto the `ExternalSecret`'s `target.template.data` (default `"admin"`,
Grafana's own conventional default — configurable via
`var.grafana_admin_username`), and only `admin-password` is a genuine
synced secret, read from the single opaque string
`modules/secrets/aws-secrets-manager` provisions under
`"${environment}/grafana-admin-password"`. If a real deployment later
wants a distinct admin username treated as equally sensitive, that's a
`seed-secrets.sh` shape change (out of this module's scope) plus a
follow-up here to read it via `remoteRef` instead of the static
template — not solved in this pass.

### Exact resources declared

- `kubernetes_service_account_v1.eso_grafana` — the IRSA-annotated
  ServiceAccount.
- `kubernetes_manifest` `SecretStore` (`external-secrets.io/v1beta1`) —
  points ESO at AWS Secrets Manager in `var.aws_region`, authenticating
  via the ServiceAccount above.
- `kubernetes_manifest` `ExternalSecret` (`external-secrets.io/v1beta1`)
  — targets the Kubernetes Secret named `var.grafana_admin_password_secret_name`
  (`creationPolicy: Owner`), reads `admin-password` from
  `"${environment}/${grafana_admin_password_secret_name}"` in Secrets
  Manager (matching `modules/secrets/aws-secrets-manager`'s own naming
  convention exactly), and templates `admin-user` statically.

## Real vs. not yet validated

- Real: every `helm_release` here, plus the `SecretStore`/`ExternalSecret`
  HCL.
- Not validated against a live cluster in this build environment (no AWS
  credentials, no live cluster) — see `infra/README.md`'s top-level
  caveat. `kubernetes_manifest` resources specifically (the ESO CRD
  instances here) require a reachable Kubernetes API serving ESO's
  actual CRD schema during `tofu plan`; expect a connection error there,
  not a structural one, without a live, ESO-equipped cluster.
- No KMS/secret-decryption boundary is implemented anywhere in this
  repo's code (a cross-deliverable gap noted in the top-level
  `CLAUDE.md`) — this module closes the "nothing syncs the Kubernetes
  Secret" gap specifically, not that broader one.
