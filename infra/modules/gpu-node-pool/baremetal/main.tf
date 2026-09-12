# gpu-node-pool/baremetal — Phase 1.5 stub.
#
# Real implementation targets pre-racked GPU hardware the organization
# already owns: no "provisioning" step in the cloud sense, just joining
# existing GPU hosts to the cluster (e.g. via a Kubernetes/Talos provider
# reading a static inventory) and applying the same taints/labels as the
# cloud variants. Spot/preemption and Karpenter-style autoscaling do not
# apply here (§14.8's "automatic on-demand fallback" collapses to
# "there's only the hardware you have"); this variant's capacity model is
# necessarily different from the cloud variants even though its
# variable/output contract must not be. This file only satisfies the
# module contract; it provisions nothing.

terraform {
  required_version = ">= 1.6.0"
  # No cloud provider: real implementation likely uses the kubernetes
  # provider directly against an existing cluster, or a null_resource
  # driving an Ansible inventory of already-racked GPU hosts.
}

locals {
  not_implemented = "gpu-node-pool/baremetal is a Phase 1.5 stub — see README.md"
}
