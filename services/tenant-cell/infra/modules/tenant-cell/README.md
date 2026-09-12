# tenant-cell

D6's OpenTofu composition layer (spec §14.13, §13.7): calls F1's
`gpu-node-pool/aws` and `model-serving` modules, parameterized per
tenant, so each tenant's GPU node pool and pinned-model serving
deployment are structurally distinct from every other tenant's.

## What this module does and does not own

- **Owns**: deriving a per-tenant environment name from `base_environment`
  + `tenant_id`, and wiring that name through to F1's two modules so
    their own naming (`gpu-node-pool`'s NodePool/EC2NodeClass, model-
    serving's Helm release/namespace) inherits it. Optionally wires in a
    second, architecturally-distinct self-hosted reviewer-model
    deployment on the same dedicated pool (spec §13.4/§13.7) when a
    caller supplies `reviewer_model_artifact_uri`.
- **Does not own**: the tenant's network, KMS key, or secrets bootstrap
  (spec §14.13 names these as instantiated by the tenant *onboarding*
  workflow from F1's existing tenant-parameterized `network`/`secrets`
  modules -- out of this deliverable's scope per its own brief). This
  module takes an already-existing cluster/network as plain input
  variables (`cluster_name`, `network_id`, `gpu_subnet_ids`,
  `egress_allowlist_group_id`).
- **Does not own**: deciding *whether* a tenant's model-diversity posture
  (second self-hosted model vs. frontier-API escalation) is acceptable --
  that enforcement lives in the Python control-plane
  (`services/tenant-cell/src/tenant_cell/model_diversity.py`), which is
  what actually refuses to mark a cell "ready." This module will happily
  create a cell with only a primary model and no reviewer path if told
  to; the readiness gate is what stops that from being used.
- **Does not rewrite** `infra/modules/gpu-node-pool/` or
  `infra/modules/model-serving/` -- both are called exactly as F1
  published them, via their existing variable contracts.

## The no-shared-node guarantee, mechanically

Two tenant-cell module calls with different `tenant_id` values produce:

- Different `local.tenant_environment` (`"<base>-tenant-<tenant_id>"`),
  hence a different NodePool/EC2NodeClass name inside `gpu-node-pool`
  (`"${environment}-gpu-node-pool"`).
- Different `labels` passed to `gpu-node-pool` (`"sdlc-auto.io/node-pool"`
  set to *this tenant's own* pool name, not the module's generic
  default) -- see `main.tf`'s `local.node_pool_labels` comment for why
  this override matters: without it, every tenant's nodes would carry
  the same generic label value, and only the pool's *name* (not the
  label a node actually carries) would differ.
- A `model-serving` deployment whose `nodeSelector` targets that same
  tenant-specific label value, so a tenant's inference pods only ever
  schedule onto that tenant's own nodes.

Karpenter provisions nodes strictly from the NodePool that requested
them -- two distinct NodePool objects never share an underlying node, so
distinct pool identifiers is the actual scheduling-time "no GPU sharing"
guarantee (spec §14.13: "'No GPU sharing' is therefore a scheduling-time
guarantee, not a runtime one").

## Validating this composition (no live cloud/cluster available)

See `../../compositions/two-tenants/README.md` for the exact commands run
and their actual output, including the honest boundary: `tofu plan`
computes and displays both tenants' distinct `node_pool_name`/IAM-role
plans for real, then errors on the `kubernetes_manifest` resources
because reaching a live Kubernetes API server is unavoidable for that
resource type at plan time (the same hard constraint F1 itself was built
under) -- not a shortcut this module takes silently.
