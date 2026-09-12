# The EKS control plane itself and a small, non-GPU "system" node group.
#
# This lives at the environment composition layer rather than inside any
# module in infra/modules/ because a Kubernetes cluster is a per-
# environment singleton that every module in this composition depends on
# — it is the thing modules attach to, not a swappable component behind
# its own cloud-contract (unlike network/gpu-node-pool/secrets, which
# genuinely differ in shape per cloud and need the contract). The system
# node group hosts small, always-on, non-GPU control-plane workloads
# (spec §14.11: job dispatcher, Run Registry, MCP servers, Karpenter's own
# controller, NTH, observability ingest) so those never compete with — or
# get scheduled onto — the tainted GPU pool.

resource "aws_iam_role" "eks_cluster" {
  name = "${var.environment}-eks-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  role       = aws_iam_role.eks_cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_eks_cluster" "this" {
  name     = "${var.environment}-cluster"
  role_arn = aws_iam_role.eks_cluster.arn
  version  = "1.30"

  vpc_config {
    subnet_ids              = module.network.private_subnet_ids
    endpoint_private_access = true
    # Public endpoint stays enabled for the pilot so bootstrap.sh (run
    # from a developer's machine, not from inside the VPC) can reach the
    # API server; production compositions narrow this to a CIDR allowlist
    # or private-only + a bastion/VPN path.
    endpoint_public_access = true
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy_attachment.eks_cluster]
}

resource "aws_iam_role" "eks_system_nodes" {
  name = "${var.environment}-eks-system-node-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "eks_system_nodes" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
  ])
  role       = aws_iam_role.eks_system_nodes.name
  policy_arn = each.value
}

resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${var.environment}-system"
  node_role_arn   = aws_iam_role.eks_system_nodes.arn
  subnet_ids      = module.network.private_subnet_ids

  # Small and always-on (spec §14.11) — cheap enough to run continuously,
  # never GPU, never running agent-orchestrator logic (that runs inside
  # the tenant compute cell per §14.13, on the GPU-adjacent pool).
  instance_types = ["t3.medium"]
  capacity_type  = "ON_DEMAND"

  scaling_config {
    min_size     = 1
    max_size     = 3
    desired_size = 2
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy_attachment.eks_system_nodes]
}

# Karpenter controller itself runs on the system node group and manages
# the GPU pool declared by module.gpu_node_pool's EC2NodeClass/NodePool.
resource "aws_iam_role" "karpenter_controller" {
  name = "${var.environment}-karpenter-controller"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" } # IRSA trust policy narrowed in a real apply via the cluster's OIDC provider
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "karpenter_controller" {
  role       = aws_iam_role.karpenter_controller.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess" # NOTE: replace with Karpenter's documented least-privilege policy before real apply — see README.md
}

resource "helm_release" "karpenter" {
  name       = "karpenter"
  repository = "oci://public.ecr.aws/karpenter"
  chart      = "karpenter"
  namespace  = "kube-system"

  # helm provider >= 3.0 represents `set` as a list-of-objects argument
  # rather than repeated `set { ... }` blocks — see spot-lifecycle's
  # module for the same note.
  set = [
    {
      name  = "settings.clusterName"
      value = aws_eks_cluster.this.name
    },
    {
      name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
      value = aws_iam_role.karpenter_controller.arn
    },
  ]

  depends_on = [aws_eks_node_group.system]
}
