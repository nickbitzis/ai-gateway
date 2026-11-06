# Tests with a mocked AWS provider: no credentials, no account, no cost.
# They check the wiring and the cost switches, not that AWS accepts the resources.
#   terraform init -backend=false && terraform test

mock_provider "aws" {
  # Fixed, well-formed ARNs so the provider's ARN checks pass.
  mock_resource "aws_lb" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-1:000000000000:loadbalancer/app/mock/0123456789abcdef"
      arn_suffix = "app/mock/0123456789abcdef"
      dns_name   = "mock-alb.us-east-1.elb.amazonaws.com"
    }
  }

  mock_resource "aws_lb_target_group" {
    defaults = {
      arn        = "arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/mock/0123456789abcdef"
      arn_suffix = "targetgroup/mock/0123456789abcdef"
    }
  }

  mock_resource "aws_lb_listener" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/mock/0123456789abcdef/0123456789abcdef"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::000000000000:role/mock"
    }
  }

  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:us-east-1:000000000000:repository/mock"
      repository_url = "000000000000.dkr.ecr.us-east-1.amazonaws.com/mock"
    }
  }

  mock_resource "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws:secretsmanager:us-east-1:000000000000:secret:mock-AbCdEf"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:000000000000:log-group:/ecs/mock"
    }
  }

  mock_resource "aws_ecs_cluster" {
    defaults = {
      arn = "arn:aws:ecs:us-east-1:000000000000:cluster/mock"
    }
  }

  mock_resource "aws_ecs_task_definition" {
    defaults = {
      arn = "arn:aws:ecs:us-east-1:000000000000:task-definition/mock:1"
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-1a", "us-east-1b", "us-east-1c"]
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "000000000000"
    }
  }

  mock_data "aws_region" {
    defaults = {
      name = "us-east-1"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

run "staging_is_cheap" {
  command = apply # against the mock provider, so nothing is created

  variables {
    environment        = "staging"
    enable_nat_gateway = false
    certificate_arn    = ""
  }

  assert {
    condition     = length(module.network.private_subnet_ids) == 2 && length(module.network.public_subnet_ids) == 2
    error_message = "Expected two public and two private subnets."
  }

  assert {
    condition     = length(module.network.nat_gateway_ids) == 0
    error_message = "Staging should not create a NAT gateway."
  }

  assert {
    condition     = startswith(output.gateway_url, "http://")
    error_message = "Without a certificate the gateway URL should be plain HTTP."
  }
}

run "prod_uses_nat_and_https" {
  command = apply # against the mock provider

  variables {
    environment        = "prod"
    enable_nat_gateway = true
    alb_internal       = true
    certificate_arn    = "arn:aws:acm:us-east-1:000000000000:certificate/example"
    min_capacity       = 2
    max_capacity       = 8
  }

  assert {
    condition     = length(module.network.nat_gateway_ids) == 1
    error_message = "Prod should create one NAT gateway."
  }

  assert {
    condition     = startswith(output.gateway_url, "https://")
    error_message = "With a certificate the gateway URL should be HTTPS."
  }
}

run "rejects_latest_tag" {
  command = plan

  variables {
    environment = "staging"
    image_tag   = "latest"
  }

  expect_failures = [var.image_tag]
}

run "rejects_unknown_environment" {
  command = plan

  variables {
    environment = "dev"
  }

  expect_failures = [var.environment]
}
