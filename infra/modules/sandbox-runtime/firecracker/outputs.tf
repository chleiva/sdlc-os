output "runtime_class_name" {
  value       = var.runtime_class_name
  description = "RuntimeClass name; sandboxed pods set runtimeClassName to this value."
}

output "bootstrap_script" {
  value       = local.bootstrap_script
  sensitive   = false
  description = "Rendered node-bootstrap script installing the firecracker-containerd shim. Not yet wired into gpu-node-pool/aws's node launch — see main.tf header comment."
}
