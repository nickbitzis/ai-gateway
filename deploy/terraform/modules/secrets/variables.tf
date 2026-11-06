variable "name" {
  description = "Name prefix for the secrets, used as a path: <name>/anthropic-api-key."
  type        = string
}

variable "recovery_window_in_days" {
  description = "Days a deleted secret can be restored. 0 deletes at once."
  type        = number
}

variable "kms_key_arn" {
  description = "Customer managed KMS key ARN, or empty for the AWS managed key."
  type        = string
  default     = ""
}
