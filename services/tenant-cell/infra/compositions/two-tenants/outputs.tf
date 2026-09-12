output "acme_node_pool_name" {
  value = module.tenant_acme.node_pool_name
}

output "globex_node_pool_name" {
  value = module.tenant_globex.node_pool_name
}

output "acme_namespace" {
  value = module.tenant_acme.namespace
}

output "globex_namespace" {
  value = module.tenant_globex.namespace
}

output "pool_names_are_distinct" {
  # A plain, directly-inspectable assertion surfaced as a real plan
  # output (not just a comment): true iff the two tenants' node pool
  # names never collide.
  value = module.tenant_acme.node_pool_name != module.tenant_globex.node_pool_name
}
