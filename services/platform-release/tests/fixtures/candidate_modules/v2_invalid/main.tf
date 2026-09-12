# Deliberately broken candidate module version: references a variable
# that is never declared in this module (var.undeclared_variable), so a
# real `tofu validate` genuinely fails against it. Used by
# tests/test_module_versioning.py to prove the promotion gate refuses to
# mark a module version promotable when its own validate/plan fails --
# not a hand-wave, a real HCL error a real `tofu` catches.

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
  content  = "module version = ${var.undeclared_variable}"
}
