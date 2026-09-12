# prod-gcp — Phase 1.5 portability-proof structure

This composition exists to make this deliverable's acceptance criterion
2 concrete in code, not just asserted in prose: **`main.tf` here differs
from `pilot-aws-g7e`/`prod-aws` in exactly two `source` lines** —
`network/gcp` in place of `network/aws`, `gpu-node-pool/gcp` in place of
`gpu-node-pool/aws` — and nothing else. `modules/model-serving`,
`modules/orchestrator`, `modules/observability`, and
`modules/sandbox-runtime/firecracker` are referenced **completely
unmodified**, with the same variables, wired the same way, as in the AWS
compositions. That is the module-contract rule (spec §14.3, §14.10)
demonstrated structurally.

## What this does NOT prove yet

`network/gcp` and `gpu-node-pool/gcp` are Phase 1.5 stubs (see their
READMEs under `infra/modules/`) — they register no real GCP resources.
So while this composition shows the *shape* of the portability proof
(same downstream modules, swapped cloud-specific ones, zero edits
required elsewhere), it does not itself constitute the Phase 1.5 proof
spec §14.10 and §21 require, which needs those modules built out for
real and a `tofu apply` that actually stands up a second-cloud pilot.
`secrets/gcp-secret-manager` is used for the same reason — also a stub.

Also unresolved: this composition has no `eks.tf`-equivalent GKE
cluster-provisioning file (GCP's cluster resource is `google_container_
cluster`, not `aws_eks_cluster`) — that file would need writing alongside
the real `network/gcp` and `gpu-node-pool/gcp` modules, not before them,
since its shape depends on what those modules actually output. Left
unwritten here rather than guessed at.

## What's real

`tofu validate` passes against this composition today (module contract
types line up even though the underlying gcp resources are stubs) —
see the parent deliverable's report for confirmation this was actually
run, not assumed.
