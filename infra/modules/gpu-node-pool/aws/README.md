# gpu-node-pool/aws

Dedicated, tainted GPU node group for the G7e family (spec §14.4),
provisioned via Karpenter's `EC2NodeClass`/`NodePool` CRDs rather than a
fixed EKS-managed node group. Real, `tofu validate`-clean HCL — see
`infra/README.md` for this tree's overall real-vs-stub split.

## Prerequisite: Karpenter's controller

This module only declares CRD instances (`EC2NodeClass`, `NodePool`).
It assumes Karpenter's *controller* is already running on the cluster —
`environments/pilot-aws-g7e/eks.tf` installs it via `helm_release
"karpenter"` and grants it the least-privilege IAM policy documented in
that file (replacing the earlier `PowerUserAccess` placeholder — see
`infra/README.md` known-gap #5, now closed).

## Composing this module more than once: `pool_name_suffix`

Every resource this module creates is named from `"${var.environment}
-gpu-node-pool"`. An environment that wants a second, differently-
configured pool alongside the main one — e.g. a spot-only,
scale-to-zero pool sized for a single-GPU Ollama serving tier, next to
the main pool vLLM/orchestrator schedule onto — sets `pool_name_suffix`
on the second instance (e.g. `"ollama"`) so the two don't collide on
IAM role/instance-profile/EC2NodeClass/NodePool names. Default `""`
reproduces the original single-pool naming exactly; every existing
caller is unaffected.

## Spot-only, scale-to-zero pools

`capacity_type` and `on_demand_fallback` already compose to a spot-only
pool with no code changes: `capacity_type = "spot"` +
`on_demand_fallback = false` collapses the NodePool's
`karpenter.sh/capacity-type` requirement to `["spot"]` alone (see
`main.tf`'s `capacity_type_values` local). Combined with `min_size = 0`
(already this module's default) and a short `consolidate_after` (new
variable, default `"1m"`; the Ollama tier in
`environments/pilot-aws-g7e` passes `"60s"`), this is what actually lets
Karpenter's `disruption.consolidationPolicy = WhenEmptyOrUnderutilized`
terminate the EC2 instance once nothing is scheduled on it — the
mechanism a KEDA `ScaledObject` scaling a Deployment to zero replicas
(see `modules/model-serving-ollama/README.md`) ultimately depends on to
actually stop paying for the GPU, not just idle it.

**Karpenter's own consolidation is not the spot-interruption path.**
`modules/spot-lifecycle` and `aws-node-termination-handler` (deployed by
that module) handle a *live* spot reclaim notice — cordon/drain/
checkpoint within the ~2-minute warning window. `consolidate_after` here
is the *opposite* direction: Karpenter deciding, on its own initiative,
that an already-idle node should be given back. Both apply to any node
this module creates; they are complementary, not alternatives.

## Firecracker/kata/gvisor bootstrap wiring

`user_data` (new variable, default `null`) is set on the EC2NodeClass's
`userData` field when provided. Pass
`sandbox-runtime/firecracker`'s (or kata/gvisor's, once real) rendered
`bootstrap_script` output here to actually install the isolation-tier
containerd shim on node launch — this closes `infra/README.md`
known-gap #3 ("registers a RuntimeClass ... but that script isn't yet
wired into `gpu-node-pool/aws`'s `EC2NodeClass.userData`"). See
`environments/pilot-aws-g7e/main.tf` for the wiring (and its comment on
the small dependency-cycle fix that was needed to do it without
`sandbox-runtime/firecracker` depending on this module's own output).

A pool with no sandboxed tool-execution workload (e.g. the Ollama
model-serving tier, which only ever runs the `ollama/ollama` server
image directly, no agent tool-execution sandbox) has no reason to pass
`user_data` — omit it and the EC2NodeClass comes up with no custom
bootstrap, same as this module's original behavior.

## KEDA (used by the Ollama tier, not by this module directly)

This module does not declare a KEDA `ScaledObject` itself — that
resource's `scaleTargetRef` names a Deployment, and the Deployment
belongs to whichever model-serving module actually deploys it (see
`modules/model-serving-ollama/main.tf`, next to the Deployment it
targets). This module only provides the spot-only, scale-to-zero
*NodePool* half of that story. As with Karpenter above: **KEDA's
controller is assumed already installed on the cluster** (its own
cluster-wide Helm chart), not installed by any module in this tree.

## What is real here vs. still a gap

- Real: IAM node role + instance profile, `EC2NodeClass`/`NodePool` CRD
  instances, the spot-only/scale-to-zero parameterization, the
  `userData` wiring.
- Gap, unchanged by this pass: GCP/Azure/baremetal siblings are still
  explicit stubs (see their own READMEs) — this module (`aws`) is the
  only one with real provisioning logic.
- Nothing here has been run against a real AWS account or a live
  cluster — see `infra/README.md`'s top-level caveat. `kubernetes_manifest`
  resources in particular (`EC2NodeClass`/`NodePool` here) require a
  reachable, schema-serving Kubernetes API during `tofu plan`, not just
  `tofu validate` — expect `tofu plan` against no live cluster to fail
  on that connection, not on any structural error in this HCL.
