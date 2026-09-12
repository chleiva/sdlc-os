# pilot-aws-g7e

Phase 0 reference environment (spec §14.3, §21 Phase 0): one
`g7e.2xlarge`, one region (live-discovered, never hardcoded), spot
capacity with on-demand fallback. Bring up with
`../../scripts/bootstrap.sh pilot-aws-g7e`.

## Composition

`eks.tf` creates the EKS control plane and a small non-GPU "system" node
group (spec §14.11's always-on control-plane compute); `main.tf` composes
`modules/network/aws`, `modules/secrets/aws-secrets-manager`,
`modules/gpu-node-pool/aws`, `modules/sandbox-runtime/firecracker`,
`modules/spot-lifecycle`, `modules/model-serving`,
`modules/orchestrator`, and `modules/observability` on top of it.

## Explicit gaps in this composition (read before a real apply)

1. **Karpenter controller IAM policy is `PowerUserAccess`** (`eks.tf`,
   `aws_iam_role_policy_attachment.karpenter_controller`) — a deliberately
   flagged placeholder, not a real least-privilege policy. Replace with
   Karpenter's documented controller policy (the JSON AWS/Karpenter
   publish for the pinned Karpenter version) before any real apply. Left
   broad here only so the module composition itself is complete and
   reviewable; shipping this as-is would violate the spirit of §17's
   scoped-credential principle even though it's outside this
   deliverable's specific "no secret in state" acceptance criterion.

2. **Grafana admin password sync is referenced, not implemented.**
   `modules/observability`'s `grafana_admin_password_secret_name` names a
   Kubernetes Secret for the Grafana chart's `admin.existingSecret`, but
   nothing in this module tree yet syncs the AWS Secrets Manager entry
   `modules/secrets` provisions into an actual Kubernetes Secret of that
   name. That sync is normally done by the External Secrets Operator or
   AWS's Secrets Store CSI Driver — installing and configuring one of
   those is a real, scoped follow-up (arguably its own small piece of
   work), not a design decision made here. Until it exists, the Grafana
   release will fail to find that Kubernetes Secret on a real apply.

3. **`endpoint_public_access = true`** on the EKS cluster (`eks.tf`) so
   `bootstrap.sh` run from a developer machine can reach the API server
   without a bastion/VPN. Fine for a Phase 0 pilot; production
   compositions should narrow this to a CIDR allowlist or private-only
   plus a bastion, per whatever the organization's own network policy
   requires — intentionally not decided here since it's an operational
   choice, not an infra-module-contract concern.

4. **Nothing in this repo has been run against a real AWS account.**
   Every module and this composition is written to be internally
   consistent, syntactically valid OpenTofu using real provider resource
   types — but `tofu validate`/`tofu plan`/`tofu apply` were not run here
   (no AWS credentials, no cluster, in this build environment). Treat it
   as reviewed-by-construction, not apply-tested.
