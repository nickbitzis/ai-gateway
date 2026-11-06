variable "name" {
  description = "Name prefix for every resource in the service."
  type        = string
}

variable "vpc_id" {
  description = "VPC to deploy into."
  type        = string
}

variable "alb_subnet_ids" {
  description = "Subnets for the ALB (public, or private when internal)."
  type        = list(string)
}

variable "task_subnet_ids" {
  description = "Subnets for the Fargate tasks."
  type        = list(string)
}

variable "assign_public_ip" {
  description = "Give tasks a public IP. Only needed when there is no NAT gateway."
  type        = bool
}

variable "alb_internal" {
  description = "Make the ALB internal."
  type        = bool
}

variable "allowed_ingress_cidrs" {
  description = "CIDR blocks allowed to reach the ALB."
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate ARN. Empty means HTTP only."
  type        = string
}

variable "alb_deletion_protection" {
  description = "Block ALB deletion."
  type        = bool
}

variable "image_tag" {
  description = "Image tag in the ECR repository."
  type        = string
}

variable "container_image" {
  description = "Full image reference that overrides ECR plus image_tag. Empty uses ECR."
  type        = string
}

variable "task_cpu" {
  description = "Fargate CPU units."
  type        = number
}

variable "task_memory" {
  description = "Fargate memory in MiB."
  type        = number
}

variable "container_port" {
  description = "Port the gateway listens on."
  type        = number
}

variable "gateway_provider" {
  description = "mock or anthropic."
  type        = string
}

variable "secret_arns" {
  description = "Map of environment variable name to Secrets Manager ARN, injected as ECS secrets."
  type        = map(string)
}

variable "secrets_kms_key_arn" {
  description = "Customer managed KMS key for the secrets, or empty."
  type        = string
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
}

variable "container_insights" {
  description = "Enable Container Insights on the cluster."
  type        = bool
}

variable "min_capacity" {
  description = "Minimum task count."
  type        = number
}

variable "max_capacity" {
  description = "Maximum task count."
  type        = number
}

variable "cpu_target_percent" {
  description = "Target average CPU percent."
  type        = number
}

variable "requests_per_target" {
  description = "Target requests per task per minute."
  type        = number
}

variable "ecr_keep_last_images" {
  description = "Tagged images kept by the ECR lifecycle policy."
  type        = number
}

variable "ecr_force_delete_on_destroy" {
  description = "Let destroy remove a non-empty ECR repository."
  type        = bool
}
