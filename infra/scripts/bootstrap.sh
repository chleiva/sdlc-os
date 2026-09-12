#!/usr/bin/env bash
# infra/scripts/bootstrap.sh <environment>
#
# One command: init, apply, seed secrets, deploy, verify (spec §14.3,
# §14.7). Runs the fixed 5-step sequence spec §14.7 specifies, each step
# a precondition for the next — a partial or silently broken deployment
# cannot be mistaken for a working one. Uses `tofu`, never `terraform`
# (spec §14.2).
#
#   1. tofu init against the target environment's state backend.
#   2. tofu apply for network, compute, and GPU node pool (preceded by a
#      live spot price/interruption-frequency discovery step, spec §14.8
#      — never a hardcoded region).
#   3. Secret bootstrap — interactive locally, or injected from CI's own
#      secret store in an automated pipeline.
#   4. Helm/Kubernetes manifests applied for model serving and for the
#      agent-runtime orchestrator, MCP servers, observability stack.
#   5. An automated smoke run of a fixed subset of the evaluation suite
#      (spec §20.1) against the freshly deployed stack. The environment
#      is declared healthy only after this step passes, not after step
#      4's resources report "ready."
#
# Usage: scripts/bootstrap.sh <environment> [--auto] [--skip-smoke]
#   --auto        Non-interactive: step 3 reads secret values from the
#                 environment (CI's own secret store injects them there)
#                 instead of prompting.
#   --skip-smoke  Skip step 5. NEVER use this to declare an environment
#                 healthy — only for iterating on steps 1-4 during this
#                 module tree's own development. bootstrap.sh refuses to
#                 print "healthy" when this flag is set; it prints
#                 "steps 1-4 complete, health UNVERIFIED" instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ENVIRONMENT="${1:-}"
AUTO_MODE="false"
SKIP_SMOKE="false"
shift || true
for arg in "$@"; do
  case "$arg" in
    --auto) AUTO_MODE="true" ;;
    --skip-smoke) SKIP_SMOKE="true" ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z "$ENVIRONMENT" ]]; then
  echo "Usage: $0 <environment> [--auto] [--skip-smoke]" >&2
  echo "Known environments: $(ls -1 "$INFRA_ROOT/environments")" >&2
  exit 2
fi

ENV_DIR="$INFRA_ROOT/environments/$ENVIRONMENT"
if [[ ! -d "$ENV_DIR" ]]; then
  echo "No such environment: $ENVIRONMENT (looked in $ENV_DIR)" >&2
  exit 2
fi

if ! command -v tofu >/dev/null 2>&1; then
  echo "tofu (OpenTofu CLI) not found on PATH. This deployment layer uses" >&2
  echo "OpenTofu specifically, not the Terraform CLI (spec §14.2)." >&2
  exit 1
fi

log() { printf '\n\033[1m[bootstrap:%s] %s\033[0m\n' "$ENVIRONMENT" "$*"; }
fail() { printf '\n\033[1;31m[bootstrap:%s] FAILED at: %s\033[0m\n' "$ENVIRONMENT" "$*" >&2; exit 1; }

cd "$ENV_DIR"

# --- Step 1: tofu init against the target environment's state backend -----
log "step 1/5 — tofu init"

STATE_BUCKET="${SDLC_AUTO_STATE_BUCKET:-}"
if [[ -z "$STATE_BUCKET" ]]; then
  fail "step 1: set SDLC_AUTO_STATE_BUCKET (the S3 bucket backing this environment's state — backend.tf's backend block is deliberately left partial, see backend.tf's header comment)"
fi

tofu init \
  -input=false \
  -backend-config="bucket=${STATE_BUCKET}" \
  -backend-config="key=${ENVIRONMENT}/terraform.tfstate" \
  -backend-config="region=${SDLC_AUTO_STATE_REGION:-us-east-1}" \
  -backend-config="dynamodb_table=${SDLC_AUTO_STATE_LOCK_TABLE:-sdlc-auto-tfstate-lock}" \
  -backend-config="encrypt=true" \
  || fail "step 1: tofu init"

# --- Step 2: pre-flight spot discovery, then tofu apply for network,
#     compute, and GPU node pool (spec §14.7 step 2, §14.8's discovery
#     pre-flight) ---------------------------------------------------------
log "step 2/5 — live spot price/interruption-frequency discovery"

DISCOVERY_OUT="$("$SCRIPT_DIR/lib/spot-discovery.sh" "$ENV_DIR")" \
  || fail "step 2: spot-discovery.sh (no candidate region/pool cleared the configured interruption-frequency threshold)"

# spot-discovery.sh prints a single line: "<region>\t<instance_type>"
DISCOVERED_REGION="$(cut -f1 <<<"$DISCOVERY_OUT")"
DISCOVERED_INSTANCE_TYPE="$(cut -f2 <<<"$DISCOVERY_OUT")"
log "discovery selected region=${DISCOVERED_REGION} instance_type=${DISCOVERED_INSTANCE_TYPE}"

log "step 2/5 — tofu apply (network, compute, GPU node pool)"

# Scoped to the substrate this step owns; model-serving/orchestrator/
# observability are step 4's job, not step 2's, even though they live in
# the same OpenTofu configuration (spec §14.7 treats them as two
# sequential steps with the second gated on the first's success).
#
# NOTE: this target list is specific to pilot-aws-g7e/prod-aws's file
# layout (an EKS cluster + system node group defined directly in the
# environment's own eks.tf, plus network/gpu_node_pool/secrets modules).
# An environment composed differently would need its own equivalent list
# here — not generalized further in this pass since only these two
# environments are apply-tested (see environments/*/README.md).
STEP2_TARGETS=(
  -target=aws_iam_role.eks_cluster
  -target=aws_iam_role_policy_attachment.eks_cluster
  -target=aws_eks_cluster.this
  -target=aws_iam_role.eks_system_nodes
  -target=aws_iam_role_policy_attachment.eks_system_nodes
  -target=aws_eks_node_group.system
  -target=aws_iam_role.karpenter_controller
  -target=aws_iam_role_policy_attachment.karpenter_controller
  -target=helm_release.karpenter
  -target=module.network
  -target=module.gpu_node_pool
  -target=module.secrets
  -target=module.sandbox_runtime
  -target=module.spot_lifecycle
)

tofu apply -input=false -auto-approve \
  -var="region=${DISCOVERED_REGION}" \
  -var="instance_types=[\"${DISCOVERED_INSTANCE_TYPE}\"]" \
  "${STEP2_TARGETS[@]}" \
  || fail "step 2: tofu apply (network/compute/GPU node pool)"

# --- Step 3: secret bootstrap ----------------------------------------------
log "step 3/5 — secret bootstrap"

"$SCRIPT_DIR/lib/seed-secrets.sh" "$ENV_DIR" "$ENVIRONMENT" "$AUTO_MODE" \
  || fail "step 3: secret bootstrap"

# --- Step 4: Helm/Kubernetes manifests for model serving, orchestrator,
#     MCP servers, observability (spec §14.7 step 4) ------------------------
log "step 4/5 — tofu apply (model serving, orchestrator, observability)"

tofu apply -input=false -auto-approve \
  -var="region=${DISCOVERED_REGION}" \
  -var="instance_types=[\"${DISCOVERED_INSTANCE_TYPE}\"]" \
  || fail "step 4: tofu apply (full — model-serving/orchestrator/observability)"

CLUSTER_NAME="$(tofu output -raw cluster_name)"
log "updating local kubeconfig for cluster ${CLUSTER_NAME}"
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$DISCOVERED_REGION" \
  || fail "step 4: aws eks update-kubeconfig"

# --- Step 5: automated smoke run — the ONLY thing allowed to declare
#     "healthy" (spec §14.7 step 5) -----------------------------------------
if [[ "$SKIP_SMOKE" == "true" ]]; then
  log "step 5/5 — SKIPPED (--skip-smoke). Steps 1-4 complete; health UNVERIFIED."
  echo "sdlc-auto:${ENVIRONMENT}:steps-1-4-complete:health-unverified"
  exit 0
fi

log "step 5/5 — smoke run (fixed subset of spec §20.1's evaluation suite)"

"$SCRIPT_DIR/smoke-test.sh" "$ENVIRONMENT" \
  || fail "step 5: smoke run did not pass — environment is NOT healthy regardless of step 4's resource status"

log "healthy — all 5 steps passed, smoke run included"
echo "sdlc-auto:${ENVIRONMENT}:healthy"
