output "default_vpc_id" {
  description = "ID of the default VPC selected for this proof of concept."
  value       = data.aws_vpc.default.id
}

output "instance_security_group_id" {
  description = "ID of the security group that will be attached to the EC2 instance in the next phase."
  value       = aws_security_group.instance.id
}

output "application_log_group_name" {
  description = "CloudWatch Logs group reserved for the GreenOps application."
  value       = aws_cloudwatch_log_group.application.name
}

output "instance_id" {
  description = "ID of the EC2 profiler instance."
  value       = aws_instance.profiler.id
}

output "dashboard_name" {
  description = "Name of the Terraform-managed CloudWatch dashboard."
  value       = aws_cloudwatch_dashboard.greenops.dashboard_name
}

output "auto_stop_rule_name" {
  description = "Name of the EventBridge auto-stop rule, or null when disabled."
  value       = var.auto_stop_enabled ? aws_cloudwatch_event_rule.auto_stop[0].name : null
}
