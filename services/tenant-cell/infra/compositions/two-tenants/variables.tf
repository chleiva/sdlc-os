variable "base_environment" {
  type        = string
  description = "Base environment name, matching a real environments/* composition's own var.environment."
  default     = "pilot-aws-g7e"
}

variable "cluster_name" {
  type        = string
  description = "Kubernetes cluster name. In a real composition this is aws_eks_cluster.this.name (see environments/pilot-aws-g7e/eks.tf); stood in with a plain variable here so this composition validates standalone."
  default     = "pilot-aws-g7e-cluster"
}

variable "network_id" {
  type        = string
  description = "Network id. In a real composition this is module.network.network_id."
  default     = "vpc-stand-in-for-validation"
}

variable "gpu_subnet_ids" {
  type        = list(string)
  description = "GPU subnet ids. In a real composition this is module.network.gpu_subnet_ids."
  default     = ["subnet-stand-in-a", "subnet-stand-in-b"]
}

variable "egress_allowlist_group_id" {
  type        = string
  description = "Egress-allowlist security-group id. In a real composition this is module.network.egress_allowlist_group_id."
  default     = "sg-stand-in-for-validation"
}

variable "primary_model_weight_checksum" {
  type        = string
  description = "Checksum stand-in for the pinned primary model artifact (spec §13.5) -- a real value is a real sha256 of a real artifact, not invented here."
  default     = "sha256:0000000000000000000000000000000000000000000000000000000000aa"
}

variable "reviewer_model_weight_checksum" {
  type        = string
  description = "Checksum stand-in for the pinned reviewer model artifact."
  default     = "sha256:0000000000000000000000000000000000000000000000000000000000bb"
}

variable "tags" {
  type        = map(string)
  description = "Common resource tags."
  default = {
    "sdlc-auto:managed-by" = "opentofu"
    "sdlc-auto:purpose"    = "tenant-cell-validation"
  }
}
