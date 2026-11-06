locals {
  image = var.container_image != "" ? var.container_image : "${aws_ecr_repository.this.repository_url}:${var.image_tag}"

  # The gateway reads its team config from a file. The config (with team keys)
  # arrives as a secret env var, is written to /tmp, then removed from the
  # environment before the app starts.
  start_command = "printf '%s' \"$GATEWAY_CONFIG_YAML\" > /tmp/gateway.yaml && unset GATEWAY_CONFIG_YAML && exec aigateway"
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "this" {
  name = var.name

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }
}

resource "aws_security_group" "tasks" {
  name        = "${var.name}-tasks"
  description = "Gateway tasks: ingress from the ALB only"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Gateway port from the ALB"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
}

# HTTPS out only: Anthropic API, ECR, Secrets Manager, CloudWatch Logs.
resource "aws_vpc_security_group_egress_rule" "tasks_https_out" {
  security_group_id = aws_security_group.tasks.id
  description       = "HTTPS to AWS APIs and the Anthropic API"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_ecs_task_definition" "this" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  # Task-local scratch space, needed because the root filesystem is read only.
  # Both are lost when the task stops, including the SQLite audit log in /app/data.
  volume {
    name = "tmp"
  }
  volume {
    name = "data"
  }

  container_definitions = jsonencode([
    {
      name                   = "gateway"
      image                  = local.image
      essential              = true
      user                   = "10001"
      readonlyRootFilesystem = true
      entryPoint             = ["sh", "-c"]
      command                = [local.start_command]

      portMappings = [{ containerPort = var.container_port, protocol = "tcp" }]

      environment = [
        { name = "HOST", value = "0.0.0.0" },
        { name = "PORT", value = tostring(var.container_port) },
        { name = "GATEWAY_PROVIDER", value = var.gateway_provider },
        { name = "GATEWAY_CONFIG", value = "/tmp/gateway.yaml" },
        { name = "GATEWAY_DB", value = "/app/data/audit.db" },
        { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
      ]

      # Injected by the ECS agent from Secrets Manager. Never plain env in the task definition.
      secrets = [for k, arn in var.secret_arns : { name = k, valueFrom = arn }]

      mountPoints = [
        { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false },
        { sourceVolume = "data", containerPath = "/app/data", readOnly = false },
      ]

      # Same check as the Dockerfile HEALTHCHECK.
      healthCheck = {
        command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${var.container_port}/healthz', timeout=2)"]
        interval    = 15
        timeout     = 3
        retries     = 3
        startPeriod = 10
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.this.name
          awslogs-region        = data.aws_region.current.name
          awslogs-stream-prefix = "gateway"
        }
      }
    }
  ])
}

resource "aws_ecs_service" "this" {
  name            = var.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  launch_type     = "FARGATE"
  desired_count   = var.min_capacity

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 30
  enable_execute_command             = false
  propagate_tags                     = "SERVICE"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = var.task_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = var.assign_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = "gateway"
    container_port   = var.container_port
  }

  # Autoscaling owns the task count after the first apply.
  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.http, aws_lb_listener.https]
}
