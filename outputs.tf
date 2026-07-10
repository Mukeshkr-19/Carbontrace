output "default_vpc_id" {
  description = "ID of the default VPC selected for this proof of concept."
  value       = data.aws_vpc.default.id
}

output "instance_security_group_id" {
  description = "ID of the security group that will be attached to the EC2 instance in the next phase."
  value       = aws_security_group.instance.id
}
