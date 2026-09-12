# sandbox-runtime/kata — Phase 1.5-and-beyond stub. Kata Containers is an
# alternate isolation tier (spec §10.1) to Firecracker: a heavier but more
# broadly hardware-compatible VM-based sandbox. Real implementation would
# register a RuntimeClass with handler "kata" and a node bootstrap
# installing the kata-runtime + a compatible VMM (QEMU or Cloud Hypervisor).
# This file only satisfies the module contract; it registers nothing.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.31"
    }
  }
}

locals {
  not_implemented = "sandbox-runtime/kata is a stub beyond Phase 0 — see README.md"
}
