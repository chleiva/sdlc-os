module "platform" {
  source = "../../modules/platform-release"

  environment        = var.environment
  platform_version   = var.platform_version
  component_versions = var.component_versions
  release_id         = var.release_id
  rolled_back_from   = var.rolled_back_from

  # Written next to this composition (path.root -- the root module's own
  # directory), never into the shared modules/platform-release source
  # tree itself, so multiple compositions/instances never collide on the
  # same on-disk manifest file.
  output_dir = path.root
}
