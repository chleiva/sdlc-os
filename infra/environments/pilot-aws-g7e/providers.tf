provider "aws" {
  region = var.region
}

# Kubernetes/Helm providers authenticate against the EKS cluster this
# environment's own eks.tf creates, using a short-lived exec-plugin token
# (aws eks get-token) rather than a static kubeconfig or a long-lived
# credential — no cluster credential is ever a plain variable or stored in
# state (spec §14.6 applied one layer up, to the infra tooling itself).

data "aws_eks_cluster_auth" "this" {
  name = aws_eks_cluster.this.name
}

provider "kubernetes" {
  host                   = aws_eks_cluster.this.endpoint
  cluster_ca_certificate = base64decode(aws_eks_cluster.this.certificate_authority[0].data)
  token                  = data.aws_eks_cluster_auth.this.token
}

provider "helm" {
  # NOTE: helm provider >= 3.0 represents `kubernetes` as a single object
  # attribute rather than a nested block (a breaking schema change from
  # the 2.x provider line) — this is the current syntax, verified against
  # the installed provider version at the time of writing.
  kubernetes = {
    host                   = aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(aws_eks_cluster.this.certificate_authority[0].data)
    token                  = data.aws_eks_cluster_auth.this.token
  }
}
