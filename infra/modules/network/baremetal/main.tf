# network/baremetal — Phase 1.5 stub.
#
# Real implementation (not yet built — see README.md in this directory)
# targets a self-managed cluster (e.g. via the Kubernetes/kubeadm or
# Talos providers) and would declare VLAN/subnet segmentation for the
# public/private/gpu tiers using whichever L2/L3 fabric the bare-metal
# environment provides (e.g. a MetalLB address pool for ingress, a
# dedicated VLAN + nftables/iptables rules for the GPU/sandbox egress
# allowlist in place of a cloud security group). This file only satisfies
# the module contract so a composition under environments/ can reference
# the baremetal variant without erroring; it provisions nothing.

terraform {
  required_version = ">= 1.6.0"
  # No provider block: bare-metal networking in the real implementation is
  # provider-specific to whichever fabric/OS the target rack runs (e.g.
  # the `restapi` or a vendor-specific provider talking to a top-of-rack
  # switch controller, or a null_resource driving Ansible/nftables).
}

locals {
  not_implemented = "network/baremetal is a Phase 1.5 stub — see README.md"
}
