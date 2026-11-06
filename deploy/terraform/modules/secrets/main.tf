# Secret containers only. Terraform never sees the values, so they never land in
# state. Fill them once with the AWS CLI (see the README), then rotate the same way.
#
#   anthropic-api-key  plain string, injected as ANTHROPIC_API_KEY
#   gateway-config     the full gateway.yaml (teams, keys, budgets), injected as
#                      GATEWAY_CONFIG_YAML and written to /tmp at container start

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name                    = "${var.name}/anthropic-api-key"
  description             = "Anthropic API key used by the AI gateway."
  recovery_window_in_days = var.recovery_window_in_days
  kms_key_id              = var.kms_key_arn == "" ? null : var.kms_key_arn
}

resource "aws_secretsmanager_secret" "gateway_config" {
  name                    = "${var.name}/gateway-config"
  description             = "AI gateway config YAML with per-team gateway keys, budgets and limits."
  recovery_window_in_days = var.recovery_window_in_days
  kms_key_id              = var.kms_key_arn == "" ? null : var.kms_key_arn
}
