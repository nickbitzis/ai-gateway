output "gateway_url" {
  description = "Base URL of the gateway behind the ALB."
  value       = module.service.gateway_url
}

output "alb_dns_name" {
  description = "ALB DNS name. Point a Route 53 alias record at it."
  value       = module.service.alb_dns_name
}

output "ecr_repository_url" {
  description = "Push images here: docker push <url>:<tag>."
  value       = module.service.ecr_repository_url
}

output "cluster_name" {
  description = "ECS cluster name."
  value       = module.service.cluster_name
}

output "service_name" {
  description = "ECS service name."
  value       = module.service.service_name
}

output "log_group_name" {
  description = "CloudWatch log group for the gateway's JSON logs."
  value       = module.service.log_group_name
}

output "secret_names" {
  description = "Secrets Manager secret names to fill with put-secret-value before the first deploy."
  value       = module.secrets.secret_names
}

output "vpc_id" {
  description = "VPC ID."
  value       = module.network.vpc_id
}
