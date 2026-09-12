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

# Karpenter's own documented least-privilege controller policy (closes
# infra/README.md known-gap #5 — this used to be a flagged
# `PowerUserAccess` placeholder). Reproduces the statement structure
# Karpenter's own docs publish (https://karpenter.sh/docs/reference/cloudformation/
# — the "KarpenterControllerPolicy" from the getting-started
# CloudFormation template, the same policy Karpenter's own install docs
# hand out), scoped to this cluster/environment/region rather than left
# as a copy-pasted wildcard-everything policy with a new name. This
# module's EC2NodeClass sets `spec.role` (not a pre-created
# `instanceProfile`, see gpu-node-pool/aws/main.tf) — that means
# Karpenter itself creates and manages the per-NodePool instance
# profile, which is why the iam:*InstanceProfile* statements below are
# present at all rather than only iam:PassRole.
data "aws_iam_policy_document" "karpenter_controller" {
  # --- Regional, read-only discovery. Must be Resource = "*": these are
  # plain Describe/Get calls with no ARN to scope down to, and Karpenter
  # has to be able to see all candidate instance types/AMIs/subnets to
  # make a placement decision, not only ones already tagged for this
  # cluster. Least-privilege here means "read-only", not "resource-
  # scoped" (AWS's own IAM model has no ARN to scope a Describe* call to
  # in the first place). ---
  statement {
    sid    = "AllowRegionalReadActions"
    effect = "Allow"
    actions = [
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribeImages",
      "ec2:DescribeInstances",
      "ec2:DescribeInstanceTypeOfferings",
      "ec2:DescribeInstanceTypes",
      "ec2:DescribeLaunchTemplates",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeSpotPriceHistory",
      "ec2:DescribeSubnets",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.region]
    }
  }

  # --- Creating instances/fleets/launch templates, scoped by the
  # request carrying this cluster's own discovery tag plus a
  # karpenter.sh/nodepool tag -- i.e. Karpenter can only launch capacity
  # it is itself tagging as belonging to this cluster/nodepool, not
  # arbitrary EC2 resources. ---
  statement {
    sid    = "AllowScopedEC2InstanceActionsWithTags"
    effect = "Allow"
    actions = [
      "ec2:RunInstances",
      "ec2:CreateFleet",
      "ec2:CreateLaunchTemplate",
    ]
    resources = [
      "arn:aws:ec2:${var.region}:*:fleet/*",
      "arn:aws:ec2:${var.region}:*:instance/*",
      "arn:aws:ec2:${var.region}:*:volume/*",
      "arn:aws:ec2:${var.region}:*:network-interface/*",
      "arn:aws:ec2:${var.region}:*:launch-template/*",
      "arn:aws:ec2:${var.region}:*:spot-instances-request/*",
    ]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes.io/cluster/${aws_eks_cluster.this.name}"
      values   = ["owned"]
    }
    condition {
      test     = "StringLike"
      variable = "aws:RequestTag/karpenter.sh/nodepool"
      values   = ["*"]
    }
  }

  # --- Referencing (not creating) AMIs/snapshots/security-groups/
  # subnets when launching -- these are read/attach-only references, so
  # no RequestTag condition applies (Karpenter isn't tagging these, it's
  # pointing at ones the EC2NodeClass/network module already tagged). ---
  statement {
    sid    = "AllowScopedEC2InstanceAccessActions"
    effect = "Allow"
    actions = [
      "ec2:RunInstances",
      "ec2:CreateFleet",
    ]
    resources = [
      "arn:aws:ec2:${var.region}::image/*",
      "arn:aws:ec2:${var.region}::snapshot/*",
      "arn:aws:ec2:${var.region}:*:security-group/*",
      "arn:aws:ec2:${var.region}:*:subnet/*",
    ]
  }

  # --- Tagging resources Karpenter itself just created, on the CreateFleet/
  # RunInstances/CreateLaunchTemplate action's own tagging step. ---
  statement {
    sid     = "AllowScopedResourceCreationTagging"
    effect  = "Allow"
    actions = ["ec2:CreateTags"]
    resources = [
      "arn:aws:ec2:${var.region}:*:fleet/*",
      "arn:aws:ec2:${var.region}:*:instance/*",
      "arn:aws:ec2:${var.region}:*:volume/*",
      "arn:aws:ec2:${var.region}:*:network-interface/*",
      "arn:aws:ec2:${var.region}:*:launch-template/*",
      "arn:aws:ec2:${var.region}:*:spot-instances-request/*",
    ]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes.io/cluster/${aws_eks_cluster.this.name}"
      values   = ["owned"]
    }
    condition {
      test     = "StringLike"
      variable = "ec2:CreateAction"
      values   = ["RunInstances", "CreateFleet", "CreateLaunchTemplate"]
    }
  }

  # --- Re-tagging an instance Karpenter already owns (e.g. drift
  # correction) -- scoped to instances already carrying this cluster's
  # own ownership tag, not any instance in the account. ---
  statement {
    sid       = "AllowScopedResourceTagging"
    effect    = "Allow"
    actions   = ["ec2:CreateTags"]
    resources = ["arn:aws:ec2:${var.region}:*:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/kubernetes.io/cluster/${aws_eks_cluster.this.name}"
      values   = ["owned"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["karpenter.sh/nodeclaim", "Name"]
    }
  }

  # --- Terminating instances / deleting launch templates -- only ones
  # this cluster owns, per its ownership tag. This is Karpenter's own
  # consolidation/expiration/drift-replacement path (see gpu-node-pool/aws's
  # `disruption` block), separate from spot-lifecycle's NTH-driven
  # interruption drain, which uses its own scoped IAM (see
  # modules/spot-lifecycle/main.tf). ---
  statement {
    sid    = "AllowScopedDeletion"
    effect = "Allow"
    actions = [
      "ec2:TerminateInstances",
      "ec2:DeleteLaunchTemplate",
    ]
    resources = [
      "arn:aws:ec2:${var.region}:*:instance/*",
      "arn:aws:ec2:${var.region}:*:launch-template/*",
    ]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/kubernetes.io/cluster/${aws_eks_cluster.this.name}"
      values   = ["owned"]
    }
  }

  # --- AMI resolution via SSM parameters (EC2NodeClass's amiSelectorTerms
  # default alias resolution) and Karpenter's own pricing lookups for
  # cost-based instance-type ranking -- both read-only, no per-resource
  # ARN to scope beyond the SSM public-parameter path/pricing's own
  # lack of resource-level permissions. ---
  statement {
    sid       = "AllowSSMReadActions"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}::parameter/aws/service/*"]
  }

  statement {
    sid       = "AllowPricingReadActions"
    effect    = "Allow"
    actions   = ["pricing:GetProducts"]
    resources = ["*"]
  }

  # --- iam:PassRole, scoped to exactly the node role(s) Karpenter is
  # allowed to launch instances with (this environment's gpu-node-pool
  # instances' own node roles — see locals.karpenter_node_role_arns in
  # main.tf), never a wildcard over every role in the account. ---
  statement {
    sid       = "AllowPassingInstanceRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = local.karpenter_node_role_arns
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }

  # --- Dynamic instance-profile management. Karpenter's EC2NodeClass
  # here uses `spec.role` (see gpu-node-pool/aws/main.tf), which means
  # Karpenter itself creates/tags/deletes a per-NodePool instance
  # profile rather than one being pre-created and referenced by name --
  # these are the permissions that specific mode requires, each scoped
  # by this cluster's own ownership tag on the create/tag path. ---
  statement {
    sid    = "AllowScopedInstanceProfileCreationActions"
    effect = "Allow"
    actions = [
      "iam:CreateInstanceProfile",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes.io/cluster/${aws_eks_cluster.this.name}"
      values   = ["owned"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/topology.kubernetes.io/region"
      values   = [var.region]
    }
  }

  statement {
    sid    = "AllowScopedInstanceProfileTagActions"
    effect = "Allow"
    actions = [
      "iam:TagInstanceProfile",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/kubernetes.io/cluster/${aws_eks_cluster.this.name}"
      values   = ["owned"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/kubernetes.io/cluster/${aws_eks_cluster.this.name}"
      values   = ["owned"]
    }
  }

  statement {
    sid    = "AllowScopedInstanceProfileActions"
    effect = "Allow"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:DeleteInstanceProfile",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/kubernetes.io/cluster/${aws_eks_cluster.this.name}"
      values   = ["owned"]
    }
  }

  statement {
    sid       = "AllowInstanceProfileReadActions"
    effect    = "Allow"
    actions   = ["iam:GetInstanceProfile"]
    resources = ["*"]
  }

  # --- EKS cluster endpoint/CA discovery Karpenter's controller does on
  # its own startup, scoped to this one cluster's ARN. ---
  statement {
    sid       = "AllowAPIServerEndpointDiscovery"
    effect    = "Allow"
    actions   = ["eks:DescribeCluster"]
    resources = [aws_eks_cluster.this.arn]
  }
}

resource "aws_iam_policy" "karpenter_controller" {
  name        = "${var.environment}-karpenter-controller"
  description = "Karpenter controller's own documented least-privilege policy (see this file's data.aws_iam_policy_document.karpenter_controller comment), replacing the PowerUserAccess placeholder."
  policy      = data.aws_iam_policy_document.karpenter_controller.json
  tags        = var.tags
}

resource "aws_iam_role_policy_attachment" "karpenter_controller" {
  role       = aws_iam_role.karpenter_controller.name
  policy_arn = aws_iam_policy.karpenter_controller.arn
}

# --- External Secrets Operator: IRSA role for the Grafana admin-password
# sync (closes infra/README.md known-gap #6 — see
# modules/observability/main.tf and its README for what this syncs and
# how). Attaches the read-only policy modules/secrets/aws-secrets-manager
# already creates, rather than a new bespoke policy -- exactly the
# access ESO's ServiceAccount needs and no more.
resource "aws_iam_role" "external_secrets_grafana" {
  name = "${var.environment}-eso-grafana"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" } # IRSA trust policy narrowed via the cluster's OIDC provider in a real apply, same caveat as aws_iam_role.karpenter_controller above
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "external_secrets_grafana" {
  role       = aws_iam_role.external_secrets_grafana.name
  policy_arn = module.secrets.access_policy_arn
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
