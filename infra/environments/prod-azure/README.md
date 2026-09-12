# prod-azure — not built out in this pass

Unlike `prod-gcp`, this directory intentionally has **no `.tf` files
yet**. The brief for this deliverable scoped a full build to
`pilot-aws-g7e` plus the `aws` variant of each cloud-specific module;
`prod-gcp` was written anyway as a concrete demonstration of this
deliverable's acceptance criterion 2 (module swap requires no
orchestrator/model-serving change). Writing a second, near-duplicate
stub composition for Azure without a specific requirement driving it
would be padding, not delivery — the pattern to follow when this
environment is actually built is `prod-gcp/main.tf` with
`network/azure`, `gpu-node-pool/azure`, and `secrets/azure-key-vault`
substituted in place of the `gcp` variants, and an
`azurerm_kubernetes_cluster` (AKS) resource in place of the
GKE-placeholder gap `prod-gcp/README.md` calls out.

This directory exists (per the required module-layout tree, spec §14.3)
so `infra/environments/` has the expected shape; it is empty of
implementation by design, not by oversight.
