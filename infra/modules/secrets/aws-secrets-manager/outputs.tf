output "secret_ids" {
  value       = { for name, s in aws_secretsmanager_secret.this : name => s.arn }
  description = "Map of logical secret name -> provider-native secret id (ARN on AWS). Contains no secret values."
}

output "access_policy_arn" {
  value       = aws_iam_policy.read_only.arn
  description = "IAM policy ARN granting read-only access to this environment's secret containers."
}
