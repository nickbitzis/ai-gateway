# Prod: tasks in private subnets behind a NAT gateway, internal ALB with HTTPS,
# two tasks minimum (one per AZ), longer log retention, deletion protection on.
#   terraform plan -var-file=environments/prod.tfvars -var image_tag=<git sha>

environment = "prod"
aws_region  = "us-east-1"

vpc_cidr           = "10.41.0.0/16"
enable_nat_gateway = true

alb_internal          = true
allowed_ingress_cidrs = ["10.0.0.0/8"] # internal callers only
certificate_arn       = ""             # pass at plan time: -var certificate_arn=arn:aws:acm:REGION:ACCOUNT_ID:certificate/ID

task_cpu         = 512
task_memory      = 1024
gateway_provider = "anthropic"

min_capacity        = 2
max_capacity        = 8
cpu_target_percent  = 60
requests_per_target = 600

log_retention_days          = 90
container_insights          = true
secret_recovery_window_days = 30
ecr_force_delete_on_destroy = false
alb_deletion_protection     = true
