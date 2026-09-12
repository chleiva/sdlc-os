# gpu-node-pool/aws — dedicated, tainted GPU node group for the G7e family
# (spec §14.4), provisioned via Karpenter rather than a fixed EKS-managed
# node group so it can drive live capacity-optimized allocation across
# instance types/AZs (spec §14.8) instead of a single pinned instance type.
#
# This module assumes Karpenter's controller is already installed on the
# cluster (the orchestrator module's Helm composition installs it — see
# modules/orchestrator). It only declares the EC2NodeClass + NodePool CRDs
# that tell an already-running Karpenter controller what to provision.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.31"
    }
  }
}

locals {
  name = "${var.environment}-gpu-node-pool"

  tags = merge(var.tags, {
    "sdlc-auto:environment" = var.environment
    "sdlc-auto:module"      = "gpu-node-pool/aws"
    "sdlc-auto:tenant-id"   = coalesce(var.tenant_id, "shared")
  })

  # Karpenter's NodePool "requirements" block, generated from instance_types
  # rather than hardcoded, so the sizing ladder (spec §13.3) and the
  # same-capability-fallback family (spec §14.8) both flow through the one
  # variable instead of needing a second edit here.
  capacity_type_values = var.capacity_type == "spot" && var.on_demand_fallback ? ["spot", "on-demand"] : [var.capacity_type]
}

# --- IAM: node role Karpenter-launched instances assume --------------------

data "aws_iam_policy_document" "node_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${local.name}-node-role"
  assume_role_policy = data.aws_iam_policy_document.node_assume_role.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "node_worker" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

resource "aws_iam_instance_profile" "node" {
  name = "${local.name}-node-profile"
  role = aws_iam_role.node.name
  tags = local.tags
}

# --- Karpenter EC2NodeClass: how a node is launched -------------------------

resource "kubernetes_manifest" "ec2_node_class" {
  manifest = {
    apiVersion = "karpenter.k8s.aws/v1"
    kind       = "EC2NodeClass"
    metadata = {
      name = local.name
    }
    spec = {
      role = aws_iam_role.node.name
      subnetSelectorTerms = [
        { tags = { "karpenter.sh/discovery" = var.environment } }
      ]
      securityGroupSelectorTerms = [
        { id = var.egress_allowlist_group_id }
      ]
      # GPU nodes boot the AL2023 NVIDIA-enabled AMI family; sandbox-runtime
      # module's firecracker/kata bootstrap layers on top via userData.
      amiFamily = "AL2023"
      tags      = local.tags
    }
  }
}

# --- Karpenter NodePool: what/how much to provision -------------------------

resource "kubernetes_manifest" "node_pool" {
  manifest = {
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata = {
      name = local.name
    }
    spec = {
      template = {
        metadata = {
          labels = var.labels
        }
        spec = {
          taints = [for t in var.taints : {
            key    = t.key
            value  = t.value
            effect = t.effect
          }]
          requirements = [
            {
              key      = "karpenter.k8s.aws/instance-family"
              operator = "In"
              # Derived from instance_types (e.g. "g7e.2xlarge" -> "g7e")
              # rather than hardcoded, so a same-capability fallback family
              # (spec §14.8) just gets added to var.instance_types.
              values = distinct([for t in var.instance_types : split(".", t)[0]])
            },
            {
              key      = "karpenter.k8s.aws/instance-size"
              operator = "In"
              values   = distinct([for t in var.instance_types : split(".", t)[1]])
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = local.capacity_type_values
            },
            {
              # Multi-AZ spread is a requirement, not a default we hope
              # Karpenter picks: this is the "diversification to reduce
              # interruption frequency, not just cost" behavior (§14.8).
              key      = "topology.kubernetes.io/zone"
              operator = "Exists"
            },
          ]
          nodeClassRef = {
            group = "karpenter.k8s.aws"
            kind  = "EC2NodeClass"
            name  = local.name
          }
        }
      }
      limits = {
        cpu = "1000" # generous ceiling; real bound is instance-type driven
      }
      disruption = {
        # capacity-optimized-prioritized: Karpenter's own consolidation
        # policy, complementing (not replacing) the spot-lifecycle
        # module's interruption watcher for the ~2-minute warning window.
        consolidationPolicy = "WhenEmptyOrUnderutilized"
        consolidateAfter    = "1m"
      }
      weight = 10
    }
  }

  depends_on = [kubernetes_manifest.ec2_node_class]
}
