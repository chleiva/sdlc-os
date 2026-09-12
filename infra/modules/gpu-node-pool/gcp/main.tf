# gpu-node-pool/gcp — Phase 1.5 stub.
#
# Real implementation would provision a GKE node pool of A2/G2-family (or
# whatever same-capability Blackwell/Hopper-class GPU shape GCP offers)
# nodes, spot/preemptible-first with on-demand fallback, tainted the same
# way as gpu-node-pool/aws, driven by a GCP-native autoscaler signal
# equivalent to Karpenter (spec §14.4 calls out "Karpenter, or the
# cloud-native equivalent where Karpenter itself is AWS-specific"). This
# file only satisfies the module contract; it provisions nothing.

terraform {
  required_version = ">= 1.6.0"
  # Real implementation would add:
  #   google = { source = "hashicorp/google", version = ">= 5.0" }
}

locals {
  not_implemented = "gpu-node-pool/gcp is a Phase 1.5 stub — see README.md"
}
