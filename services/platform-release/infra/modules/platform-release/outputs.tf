output "platform_version" {
  value       = var.platform_version
  description = "The platform_version this apply just deployed (or rolled back to)."
}

output "manifest_path" {
  value       = local_file.deployed_version.filename
  description = "Path to the on-disk deployed-version manifest this apply wrote/updated -- what a test asserts against to prove a rollback actually took effect."
}

output "manifest_content" {
  value       = local_file.deployed_version.content
  description = "The manifest's own JSON content, echoed back so a caller (or a test) can assert on it without a separate file read if it already has the tofu output."
}

output "is_rollback" {
  value       = var.rolled_back_from != null
  description = "True iff this apply was a rollback (rolled_back_from was set)."
}
