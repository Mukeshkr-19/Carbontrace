provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      ManagedBy = "Terraform"
      Project   = var.project_tag
    }
  }
}

data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "instance" {
  name_prefix = "${var.project_name}-instance-"
  description = "Restricts administrative SSH access to the configured trusted IP."
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH from the trusted operator IP only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  egress {
    description = "Allow outbound traffic for package installation and AWS API calls"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-instance-sg"
  }
}
