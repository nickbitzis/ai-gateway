# Staging: smallest shape that still runs the full path. No NAT gateway (tasks get
# a public IP in public subnets), one task, HTTP unless a certificate is supplied.
#   terraform plan -var-file=environments/staging.tfvars

environment = "staging"
aws_region  = "us-east-1"

vpc_cidr           = "10.40.0.0/16"
enable_nat_gateway = false

alb_internal          = false
allowed_ingress_cidrs = ["203.0.113.0/24"] # placeholder, replace with your VPN range
certificate_arn       = ""

task_cpu         = 256
task_memory      = 512
gateway_provider = "mock"

min_capacity       = 1
max_capacity       = 2
cpu_target_percent = 70

log_retention_days          = 14
container_insights          = false
secret_recovery_window_days = 0
ecr_force_delete_on_destroy = true
alb_deletion_protection     = false
