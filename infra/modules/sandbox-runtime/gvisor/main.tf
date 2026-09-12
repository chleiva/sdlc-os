# sandbox-runtime/gvisor — Phase 1.5-and-beyond stub. gVisor is a
# userspace-kernel isolation tier (spec §10.1): lighter weight than a full
# microVM, sandboxing via a syscall-intercepting runtime (runsc) rather
# than hardware virtualization. Real implementation would register a
# RuntimeClass with handler "runsc" and a node bootstrap installing the
# runsc binary + containerd shim. This file only satisfies the module
# contract; it registers nothing.

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
  not_implemented = "sandbox-runtime/gvisor is a stub beyond Phase 0 — see README.md"
}
