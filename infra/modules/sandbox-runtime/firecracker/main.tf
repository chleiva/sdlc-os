# sandbox-runtime/firecracker — Firecracker microVM isolation tier (spec
# §10.1). Registers the RuntimeClass sandboxed agent-tool-execution pods
# select via runtimeClassName, and renders the node bootstrap script that
# installs the firecracker-containerd shim.
#
# Integration gap, stated honestly: gpu-node-pool/aws's EC2NodeClass does
# not yet accept a userData hook to run bootstrap_script.sh.tpl's output
# on node launch — wiring that through is a small follow-up (add a
# `user_data` variable to gpu-node-pool/aws and pass this module's
# rendered script into it) rather than a re-architecture. Until that's
# done, the RuntimeClass this module registers has no node actually
# running the matching shim, so pods requesting it will fail to schedule
# — this module is real, working HCL for the Kubernetes-facing half of
# the tier; the node-bootstrap half needs that one additional wire-up.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.31"
    }
  }
}

resource "kubernetes_manifest" "firecracker_runtime_class" {
  manifest = {
    apiVersion = "node.k8s.io/v1"
    kind       = "RuntimeClass"
    metadata = {
      name   = var.runtime_class_name
      labels = var.tags
    }
    # firecracker-containerd registers itself under this handler name on
    # nodes that ran bootstrap_script.sh.tpl.
    handler = "firecracker"
    scheduling = {
      nodeSelector = {
        "sdlc-auto.io/node-pool"        = var.node_pool_name
        "sdlc-auto.io/sandbox-runtime"  = "firecracker"
      }
    }
  }
}

# Rendered for the gpu-node-pool module (or an environment composition) to
# pass to Karpenter's EC2NodeClass.spec.userData once that wiring exists
# (see header comment). Kept as a local + output rather than applied
# directly here, since this module has no node/instance resource of its
# own to attach it to.
locals {
  bootstrap_script = templatefile("${path.module}/bootstrap_script.sh.tpl", {
    runtime_class_name = var.runtime_class_name
  })
}
