variable "project" {
  description = "Short project name, used as a prefix for every resource name."
  type        = string
  default     = "ai-gateway"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "project must be 2 to 21 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment. Drives names, tags and the state key."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be staging or prod."
  }
}

variable "aws_region" {
  description = "AWS region to deploy into, for example us-east-1."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like us-east-1 or eu-west-2."
  }
}

variable "repository" {
  description = "Source repository, recorded in the Repository tag."
  type        = string
  default     = "github.com/OWNER/ai-gateway"
}

variable "extra_tags" {
  description = "Additional tags merged into default_tags, for example CostCenter or Owner."
  type        = map(string)
  default     = {}
}

# Network

variable "vpc_cidr" {
  description = "CIDR block for the VPC. Split into two public and two private /20 subnets (for a /16)."
  type        = string
  default     = "10.40.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && tonumber(split("/", var.vpc_cidr)[1]) <= 20
    error_message = "vpc_cidr must be a valid IPv4 CIDR of size /20 or larger."
  }
}

variable "enable_nat_gateway" {
  description = "Create one NAT gateway so tasks in private subnets can reach the internet. About $33 a month plus data. When false, tasks run in public subnets with a public IP."
  type        = bool
  default     = true
}

# Load balancer

variable "alb_internal" {
  description = "Make the ALB internal (reachable only inside the VPC and peered networks). A gateway for internal apps usually is."
  type        = bool
  default     = false
}

variable "allowed_ingress_cidrs" {
  description = "CIDR blocks allowed to reach the ALB on ports 80 and 443."
  type        = list(string)
  default     = ["10.0.0.0/8"]

  validation {
    condition     = length(var.allowed_ingress_cidrs) > 0 && alltrue([for c in var.allowed_ingress_cidrs : can(cidrhost(c, 0))])
    error_message = "allowed_ingress_cidrs must be a non-empty list of valid CIDR blocks."
  }
}

variable "certificate_arn" {
  description = "ACM certificate ARN for the HTTPS listener. Leave empty to serve plain HTTP on port 80 (staging only)."
  type        = string
  default     = ""

  validation {
    condition     = var.certificate_arn == "" || can(regex("^arn:aws[a-z-]*:acm:", var.certificate_arn))
    error_message = "certificate_arn must be empty or an ACM certificate ARN."
  }
}

variable "alb_deletion_protection" {
  description = "Block deletion of the ALB. Turn on for prod."
  type        = bool
  default     = false
}

# Container and task

variable "image_tag" {
  description = "Image tag in the ECR repository to deploy, normally the git SHA. Tags are immutable, so latest is not allowed."
  type        = string
  default     = "0.1.0"

  validation {
    condition     = var.image_tag != "latest" && can(regex("^[A-Za-z0-9_.-]{1,128}$", var.image_tag))
    error_message = "image_tag must be a valid tag and must not be latest."
  }
}

variable "container_image" {
  description = "Full image reference that overrides the ECR repository plus image_tag. Leave empty to use ECR."
  type        = string
  default     = ""
}

variable "task_cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256

  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.task_cpu)
    error_message = "task_cpu must be one of 256, 512, 1024, 2048, 4096."
  }
}

variable "task_memory" {
  description = "Fargate task memory in MiB. Must be a valid pairing with task_cpu."
  type        = number
  default     = 512

  validation {
    condition     = var.task_memory >= 512 && var.task_memory <= 30720
    error_message = "task_memory must be between 512 and 30720 MiB."
  }
}

variable "gateway_provider" {
  description = "Upstream provider for the gateway: mock (no API calls) or anthropic."
  type        = string
  default     = "mock"

  validation {
    condition     = contains(["mock", "anthropic"], var.gateway_provider)
    error_message = "gateway_provider must be mock or anthropic."
  }
}

# Scaling

variable "min_capacity" {
  description = "Minimum number of running tasks."
  type        = number
  default     = 1

  validation {
    condition     = var.min_capacity >= 1
    error_message = "min_capacity must be at least 1."
  }
}

variable "max_capacity" {
  description = "Maximum number of running tasks."
  type        = number
  default     = 4

  validation {
    condition     = var.max_capacity >= 1 && var.max_capacity <= 50
    error_message = "max_capacity must be between 1 and 50."
  }
}

variable "cpu_target_percent" {
  description = "Target average CPU utilization for target tracking scaling."
  type        = number
  default     = 60

  validation {
    condition     = var.cpu_target_percent >= 10 && var.cpu_target_percent <= 90
    error_message = "cpu_target_percent must be between 10 and 90."
  }
}

variable "requests_per_target" {
  description = "Target ALB requests per task per minute for request count scaling."
  type        = number
  default     = 600

  validation {
    condition     = var.requests_per_target > 0
    error_message = "requests_per_target must be positive."
  }
}

# Operations

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
  default     = 30

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653], var.log_retention_days)
    error_message = "log_retention_days must be a value CloudWatch accepts, for example 7, 30 or 365."
  }
}

variable "container_insights" {
  description = "Enable ECS Container Insights (extra CloudWatch cost)."
  type        = bool
  default     = false
}

variable "ecr_keep_last_images" {
  description = "Number of tagged images the ECR lifecycle policy keeps."
  type        = number
  default     = 20

  validation {
    condition     = var.ecr_keep_last_images >= 1
    error_message = "ecr_keep_last_images must be at least 1."
  }
}

variable "ecr_force_delete_on_destroy" {
  description = "Allow terraform destroy to delete the ECR repository even if it holds images. Keep false for prod."
  type        = bool
  default     = false
}

variable "secret_recovery_window_days" {
  description = "Days Secrets Manager keeps a deleted secret before it is gone. 0 deletes at once (staging only)."
  type        = number
  default     = 7

  validation {
    condition     = var.secret_recovery_window_days == 0 || (var.secret_recovery_window_days >= 7 && var.secret_recovery_window_days <= 30)
    error_message = "secret_recovery_window_days must be 0 or between 7 and 30."
  }
}

variable "secrets_kms_key_arn" {
  description = "Customer managed KMS key ARN for the secrets. Leave empty to use the AWS managed key."
  type        = string
  default     = ""

  validation {
    condition     = var.secrets_kms_key_arn == "" || can(regex("^arn:aws[a-z-]*:kms:", var.secrets_kms_key_arn))
    error_message = "secrets_kms_key_arn must be empty or a KMS key ARN."
  }
}
