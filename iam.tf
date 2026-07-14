locals {
  ec2_role_name             = "carbontrace-ec2-role"
  ec2_instance_profile_name = "carbontrace-ec2-profile"
  auto_stop_role_name       = "carbontrace-auto-stop-role"
}

data "aws_iam_role" "instance" {
  # Existence guard: fail planning before EC2 creation if the administrator-created role is missing.
  name = local.ec2_role_name
}

data "aws_iam_instance_profile" "instance" {
  name = local.ec2_instance_profile_name
}

resource "aws_cloudwatch_log_group" "application" {
  name              = "/aws/carbontrace/${var.project_name}"
  retention_in_days = 14
}
