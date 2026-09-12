variable "environment" {
  type    = string
  default = "platform-demo"
}

variable "platform_version" {
  type        = string
  description = "The version to deploy/roll back to. Every real `tofu apply` this deliverable's rollback test runs passes this in via -var, exactly the way a real release/rollback pipeline would."
}

variable "component_versions" {
  type    = map(string)
  default = {}
}

variable "release_id" {
  type = string
}

variable "rolled_back_from" {
  type    = string
  default = null
}
