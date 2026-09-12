# A minimal stand-in "infra module" (spec §14.3's module concept) used
# only by tests/test_module_versioning.py to prove the promotion gate
# against a real `tofu validate`/`plan`, without depending on any of F1's
# real, larger modules (which need AWS/GCP/network access this
# environment doesn't have). Its shape -- a resource driven by an input
# variable -- is representative of any real module's own contract.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

resource "local_file" "marker" {
  filename = "${path.module}/candidate-marker.txt"
  content  = "module version = ${var.module_version}"
}
