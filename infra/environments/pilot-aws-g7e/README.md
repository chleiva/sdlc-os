# pilot-aws-g7e

Phase 0 reference environment (spec §14.3, §21 Phase 0): one
`g7e.2xlarge`, one region (live-discovered, never hardcoded), spot
capacity with on-demand fallback. Bring up with
`../../scripts/bootstrap.sh pilot-aws-g7e`.

## Composition

`eks.tf` creates the EKS control plane and a small non-GPU "system" node
group (spec §14.11's always-on control-plane compute); `main.tf` composes
`modules/network/aws`, `modules/secrets/aws-secrets-manager`,
`modules/gpu-node-pool/aws` (twice — the shared vLLM/orchestrator pool,
and, when `ollama_enabled = true`, a second spot-only/scale-to-zero
instance for the Ollama tier), `modules/sandbox-runtime/firecracker`,
`modules/spot-lifecycle`, `modules/model-serving`, optionally
`modules/model-serving-ollama`, `modules/orchestrator`, and
`modules/observability` on top of it.

## Optional: Ollama serving tier alongside vLLM

Set `ollama_enabled = true` (see `terraform.tfvars.example`) to also
deploy `modules/model-serving-ollama` on its own dedicated, spot-only,
scale-to-zero `gpu-node-pool/aws` instance (`pool_name_suffix =
"ollama"`) — a real pilot that wants to compare serving backends gets
both side by side; the existing vLLM path (`module.model_serving`) is
completely unaffected either way. Read
`modules/model-serving-ollama/README.md` before turning this on — it
documents the pull-on-every-pod-start cost, the model
context-length/VRAM caveat, and the concrete KEDA/run-registry
metrics-endpoint gap (`ollama_keda_enabled` should stay `false` until
that endpoint is real). Requires KEDA's controller pre-installed
cluster-wide if `ollama_keda_enabled = true` — this composition does not
install it.

## Explicit gaps in this composition (read before a real apply)

1. ~~**Karpenter controller IAM policy is `PowerUserAccess`**~~ —
   **closed.** `eks.tf`'s `data.aws_iam_policy_document.karpenter_controller`
   now reproduces Karpenter's own documented least-privilege controller
   policy (EC2 instance/fleet/launch-template create/tag/terminate
   scoped by this cluster's ownership tag, `iam:PassRole` scoped to
   exactly this environment's node role ARNs, dynamic instance-profile
   management scoped the same way, SSM/pricing read-only calls), not a
   renamed copy of `PowerUserAccess`. See that file's comment for the
   reasoning behind each statement.

2. ~~**Grafana admin password sync is referenced, not implemented.**~~ —
   **closed.** `modules/observability` now declares a real
   `SecretStore`/`ExternalSecret` (External Secrets Operator) that syncs
   the AWS Secrets Manager entry into the Kubernetes Secret Grafana's
   chart expects, using `aws_iam_role.external_secrets_grafana` (this
   file) for IRSA. **Still assumed, not installed here:** ESO's own
   controller must already be running cluster-wide — see
   `modules/observability/README.md`.

3. **`endpoint_public_access = true`** on the EKS cluster (`eks.tf`) so
   `bootstrap.sh` run from a developer machine can reach the API server
   without a bastion/VPN. Fine for a Phase 0 pilot; production
   compositions should narrow this to a CIDR allowlist or private-only
   plus a bastion, per whatever the organization's own network policy
   requires — intentionally not decided here since it's an operational
   choice, not an infra-module-contract concern.

4. **Firecracker node-bootstrap script is now wired in** (was gap #3 in
   `infra/README.md`): `module.gpu_node_pool`'s `user_data` is
   `module.sandbox_runtime.bootstrap_script`. Doing this without a
   dependency cycle required computing the GPU pool's name as a plain
   local (`local.gpu_node_pool_name`, a pure function of `var.environment`)
   instead of threading it through `module.gpu_node_pool.node_pool_name`
   — see `main.tf`'s locals block comment for why that's safe. The
   Ollama tier's own pool does not get this userData (it runs no
   sandboxed tool-execution workload).

5. **KEDA and External Secrets Operator controllers are prerequisites,
   not installed by this composition** — same posture as the existing
   Karpenter-controller assumption. Install both cluster-wide (their own
   Helm charts) before setting `ollama_keda_enabled` or relying on the
   Grafana secret sync.

6. **Nothing in this repo has been run against a real AWS account.**
   Every module and this composition is written to be internally
   consistent, syntactically valid OpenTofu using real provider resource
   types — but `tofu apply` was not run here (no AWS credentials, no
   cluster, in this build environment); `tofu validate` passes and
   `tofu plan` was attempted where feasible (see each touched module's
   verification output) but resources wrapping cluster-installed CRDs
   (`kubernetes_manifest` for Karpenter/KEDA/ESO objects) need a
   reachable, schema-serving Kubernetes API to plan against, not just to
   apply — expect a connection error there, not a structural one,
   without a live, correctly-equipped cluster. Treat this composition as
   reviewed-by-construction, not apply-tested.
