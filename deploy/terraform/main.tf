provider "aws" {
  region = var.aws_region

  # Every taggable resource gets these, so cost reports can group by project and environment.
  default_tags {
    tags = merge(
      {
        Project     = var.project
        Environment = var.environment
        ManagedBy   = "terraform"
        Repository  = var.repository
      },
      var.extra_tags,
    )
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "${var.project}-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
}

module "network" {
  source = "./modules/network"

  name               = local.name
  cidr_block         = var.vpc_cidr
  azs                = local.azs
  enable_nat_gateway = var.enable_nat_gateway
}

module "secrets" {
  source = "./modules/secrets"

  name                    = local.name
  recovery_window_in_days = var.secret_recovery_window_days
  kms_key_arn             = var.secrets_kms_key_arn
}

module "service" {
  source = "./modules/service"

  name   = local.name
  vpc_id = module.network.vpc_id

  # The ALB lives in public subnets unless it is internal. Tasks live in private
  # subnets behind the NAT gateway. With NAT turned off (cheap staging), tasks run
  # in public subnets with a public IP so they can still reach ECR and the
  # Anthropic API; their security group only accepts traffic from the ALB.
  alb_subnet_ids   = var.alb_internal ? module.network.private_subnet_ids : module.network.public_subnet_ids
  task_subnet_ids  = var.enable_nat_gateway ? module.network.private_subnet_ids : module.network.public_subnet_ids
  assign_public_ip = !var.enable_nat_gateway

  alb_internal          = var.alb_internal
  allowed_ingress_cidrs = var.allowed_ingress_cidrs
  certificate_arn       = var.certificate_arn

  image_tag       = var.image_tag
  container_image = var.container_image
  task_cpu        = var.task_cpu
  task_memory     = var.task_memory
  container_port  = 8080

  gateway_provider = var.gateway_provider
  secret_arns = {
    ANTHROPIC_API_KEY   = module.secrets.anthropic_api_key_arn
    GATEWAY_CONFIG_YAML = module.secrets.gateway_config_arn
  }
  secrets_kms_key_arn = var.secrets_kms_key_arn

  log_retention_days          = var.log_retention_days
  container_insights          = var.container_insights
  min_capacity                = var.min_capacity
  max_capacity                = var.max_capacity
  cpu_target_percent          = var.cpu_target_percent
  requests_per_target         = var.requests_per_target
  ecr_keep_last_images        = var.ecr_keep_last_images
  alb_deletion_protection     = var.alb_deletion_protection
  ecr_force_delete_on_destroy = var.ecr_force_delete_on_destroy
}

# Warns on plan, does not block: prod should always terminate TLS at the ALB.
check "prod_has_https" {
  assert {
    condition     = var.environment != "prod" || var.certificate_arn != ""
    error_message = "prod is planned without certificate_arn, so the ALB will serve plain HTTP."
  }
}
