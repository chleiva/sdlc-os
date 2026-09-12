# spot-lifecycle — AWS interruption signal path (spec §14.8), wired for
# real. GCP/Azure signal sources are not implemented in this pass — see
# README.md. baremetal is a deliberate no-op (no spot/preemption concept).
#
# What this module owns vs. what it doesn't (documented honestly rather
# than silently claimed): it wires the *signal path* (EventBridge -> SQS)
# and deploys aws-node-termination-handler (NTH), which natively performs
# step 1 (cordon) and step 3 (drain, respecting each pod's own grace
# period). Steps 2 (stop-accepting-requests), 4 (flush observability), and
# 5 (force checkpoint) are implemented as Kubernetes `preStop` lifecycle
# hooks + a sized `terminationGracePeriodSeconds` on the model-serving and
# orchestrator pod specs themselves (owned by those modules) — this module
# provides the webhook notification those hooks key off of and the timing
# budget (`warning_window_seconds`) those modules size their grace period
# against. This split keeps "what happens on interruption" co-located with
# the pods that actually hold the state being protected, instead of a
# node-level watcher trying to reach into another module's containers.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.13"
    }
  }
}

locals {
  enabled = var.cloud_provider == "aws"

  tags = merge(var.tags, {
    "sdlc-auto:environment" = var.environment
    "sdlc-auto:module"      = "spot-lifecycle"
  })
}

# --- Signal path: EventBridge -> SQS (spec §14.8's "second, redundant
# channel" alongside each node's own IMDS poll of
# http://169.254.169.254/latest/meta-data/spot/instance-action) ----------

resource "aws_sqs_queue" "interruption_events" {
  count = local.enabled ? 1 : 0

  name                      = "${var.environment}-spot-interruption-events"
  message_retention_seconds = 300 # short-lived; a stale interruption event is not actionable
  tags                      = local.tags
}

data "aws_iam_policy_document" "sqs_from_eventbridge" {
  count = local.enabled ? 1 : 0

  statement {
    sid       = "AllowEventBridgeSend"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.interruption_events[0].arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_sqs_queue_policy" "interruption_events" {
  count     = local.enabled ? 1 : 0
  queue_url = aws_sqs_queue.interruption_events[0].id
  policy    = data.aws_iam_policy_document.sqs_from_eventbridge[0].json
}

resource "aws_cloudwatch_event_rule" "spot_interruption" {
  count       = local.enabled ? 1 : 0
  name        = "${var.environment}-spot-interruption-warning"
  description = "EC2 Spot Instance Interruption Warning (spec §14.8's ~2-minute warning)"
  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Spot Instance Interruption Warning"]
  })
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "rebalance_recommendation" {
  count       = local.enabled ? 1 : 0
  name        = "${var.environment}-spot-rebalance-recommendation"
  description = "EC2 Instance Rebalance Recommendation — the earlier, non-guaranteed second signal spec §14.8 treats as a redundant channel"
  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Instance Rebalance Recommendation"]
  })
  tags = local.tags
}

resource "aws_cloudwatch_event_target" "spot_interruption_to_sqs" {
  count = local.enabled ? 1 : 0
  rule  = aws_cloudwatch_event_rule.spot_interruption[0].name
  arn   = aws_sqs_queue.interruption_events[0].arn
}

resource "aws_cloudwatch_event_target" "rebalance_to_sqs" {
  count = local.enabled ? 1 : 0
  rule  = aws_cloudwatch_event_rule.rebalance_recommendation[0].name
  arn   = aws_sqs_queue.interruption_events[0].arn
}

# --- IAM for NTH's queue-processor deployment (IRSA) ------------------------

data "aws_iam_policy_document" "nth_permissions" {
  count = local.enabled ? 1 : 0

  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
    ]
    resources = [aws_sqs_queue.interruption_events[0].arn]
  }

  statement {
    actions = [
      "ec2:DescribeInstances",
      "autoscaling:DescribeAutoScalingInstances",
      "autoscaling:CompleteLifecycleAction",
    ]
    resources = ["*"] # NTH's own documented policy requires wildcard here (read/describe + lifecycle-action completion, no mutating instance access)
  }
}

resource "aws_iam_policy" "nth" {
  count       = local.enabled ? 1 : 0
  name        = "${var.environment}-node-termination-handler"
  description = "Permissions for aws-node-termination-handler's queue-processor deployment (spec §14.8)."
  policy      = data.aws_iam_policy_document.nth_permissions[0].json
  tags        = local.tags
}

# --- aws-node-termination-handler, queue-processor mode ---------------------
#
# NOTE: chart values below match the aws-node-termination-handler Helm
# chart's documented shape at the time of writing; pin an exact chart
# version at apply time and re-verify values against that pinned version's
# published values.yaml before first real apply.

resource "helm_release" "node_termination_handler" {
  count = local.enabled ? 1 : 0

  name       = "aws-node-termination-handler"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-node-termination-handler"
  namespace  = "sdlc-auto-system"

  # NOTE: helm provider >= 3.0 represents `set` as a list-of-objects
  # argument rather than repeated `set { ... }` blocks (a breaking schema
  # change from the 2.x provider line) — this is the current syntax,
  # verified against the installed provider version at the time of
  # writing.
  set = [
    {
      name  = "enableSqsTerminationDraining"
      value = "true"
    },
    {
      name  = "queueURL"
      value = aws_sqs_queue.interruption_events[0].id
    },
    {
      name  = "nodeSelector.sdlc-auto\\.io/node-pool"
      value = "gpu"
    },
    {
      name  = "webhookURL"
      value = var.checkpoint_webhook_url
    },
    {
      name  = "checkTagBasedNodeTermination"
      value = "false"
    },
  ]

  depends_on = [
    aws_sqs_queue_policy.interruption_events,
    aws_cloudwatch_event_target.spot_interruption_to_sqs,
    aws_cloudwatch_event_target.rebalance_to_sqs,
  ]
}
