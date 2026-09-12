output "interruption_queue_arn" {
  value       = local.enabled ? aws_sqs_queue.interruption_events[0].arn : null
  description = "SQS queue ARN carrying interruption/rebalance events. Null when cloud_provider != \"aws\"."
}

output "node_termination_handler_release" {
  value       = local.enabled ? helm_release.node_termination_handler[0].name : null
  description = "Helm release name of the deployed aws-node-termination-handler. Null when cloud_provider != \"aws\"."
}

output "warning_window_seconds" {
  value       = var.warning_window_seconds
  description = "Pass-through of the configured warning window, for model-serving/orchestrator to size terminationGracePeriodSeconds against."
}
