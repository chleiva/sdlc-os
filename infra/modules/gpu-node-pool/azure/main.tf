# gpu-node-pool/azure — Phase 1.5 stub.
#
# Real implementation would provision an AKS GPU-enabled node pool (NCads
# H100 v5 or the closest same-capability shape Azure offers), spot-first
# with on-demand fallback, tainted the same way as gpu-node-pool/aws,
# autoscaled via KEDA against GPU utilization + inference-queue depth
# (spec §14.4's cloud-native-equivalent-to-Karpenter path). This file only
# satisfies the module contract; it provisions nothing.

terraform {
  required_version = ">= 1.6.0"
  # Real implementation would add:
  #   azurerm = { source = "hashicorp/azurerm", version = ">= 3.90" }
}

locals {
  not_implemented = "gpu-node-pool/azure is a Phase 1.5 stub — see README.md"
}
