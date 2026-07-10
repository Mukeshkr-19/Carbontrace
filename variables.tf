variable "aws_region" {
  description = "AWS Region in which the GreenOps infrastructure is provisioned."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short project name used in resource names and tags."
  type        = string
  default     = "greenops-profiler"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.project_name))
    error_message = "project_name may contain only lowercase letters, digits, and hyphens."
  }
}

variable "my_ip" {
  description = "Trusted public IPv4 address allowed to use SSH, in CIDR notation (for example, 203.0.113.10/32)."
  type        = string
  sensitive   = true

  validation {
    condition     = can(cidrhost(var.my_ip, 0)) && endswith(var.my_ip, "/32")
    error_message = "my_ip must be a single IPv4 address in /32 CIDR notation."
  }
}

variable "instance_type" {
  description = "EC2 instance type for the later compute phase."
  type        = string
  default     = "t3.micro"
}
