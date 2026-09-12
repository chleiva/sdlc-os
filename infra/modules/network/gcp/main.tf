# network/gcp — Phase 1.5 stub.
#
# Real implementation (not yet built — see README.md in this directory)
# would declare a google_compute_network + per-region google_compute_
# subnetwork resources (public/private/gpu tiers, matching network/aws's
# three-tier layout), a Cloud NAT + Cloud Router for private egress, and
# google_compute_firewall rules implementing the same egress-allowlist
# semantics as network/aws's security group. This file only satisfies the
# module contract (variables.tf/outputs.tf) so a composition under
# environments/ can reference gcp variants without erroring; it provisions
# nothing.

terraform {
  required_version = ">= 1.6.0"
  # required_providers intentionally omitted from this stub — no provider
  # is invoked. The real implementation would add:
  #   google = { source = "hashicorp/google", version = ">= 5.0" }
}

locals {
  not_implemented = "network/gcp is a Phase 1.5 stub — see README.md"
}
