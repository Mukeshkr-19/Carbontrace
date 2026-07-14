data "aws_subnets" "default_vpc" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
}

resource "aws_instance" "profiler" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = sort(data.aws_subnets.default_vpc.ids)[0]
  vpc_security_group_ids      = [aws_security_group.instance.id]
  iam_instance_profile        = data.aws_iam_instance_profile.instance.name
  key_name                    = var.key_name
  associate_public_ip_address = true
  monitoring                  = false
  user_data = templatefile("${path.module}/scripts/user_data.sh.tftpl", {
    app_repository_url = var.app_repository_url
    app_revision       = var.app_revision
    log_group_name     = aws_cloudwatch_log_group.application.name
    project_name       = var.project_tag
    run_interval_hours = var.run_interval_hours
  })
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    encrypted             = true
    delete_on_termination = true
    volume_type           = "gp3"
    volume_size           = 8
  }

  volume_tags = {
    ManagedBy = "Terraform"
    Name      = "${var.project_name}-profiler-root"
    Project   = var.project_tag
  }

  tags = {
    Name = "${var.project_name}-profiler"
  }
}
