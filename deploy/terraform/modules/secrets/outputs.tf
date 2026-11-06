output "anthropic_api_key_arn" {
  description = "ARN of the Anthropic API key secret."
  value       = aws_secretsmanager_secret.anthropic_api_key.arn
}

output "gateway_config_arn" {
  description = "ARN of the gateway config secret."
  value       = aws_secretsmanager_secret.gateway_config.arn
}

output "secret_names" {
  description = "Secret names, for aws secretsmanager put-secret-value."
  value = {
    anthropic_api_key = aws_secretsmanager_secret.anthropic_api_key.name
    gateway_config    = aws_secretsmanager_secret.gateway_config.name
  }
}
