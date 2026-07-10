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

variable "key_name" {
  description = "Name of an existing EC2 key pair in the selected AWS Region."
  type        = string

  validation {
    condition     = length(trimspace(var.key_name)) > 0
    error_message = "key_name must name an existing EC2 key pair."
  }
}

variable "app_repository_url" {
  description = "HTTPS URL of the public repository that the instance clones during bootstrap."
  type        = string
  default     = "https://github.com/Mukeshkr-19/Carbontrace.git"

  validation {
    condition     = startswith(var.app_repository_url, "https://")
    error_message = "app_repository_url must use HTTPS."
  }
}

variable "app_revision" {
  description = "Immutable Git commit SHA that the instance checks out during bootstrap."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.app_revision))
    error_message = "app_revision must be a 40-character lowercase Git commit SHA."
  }
}

variable "run_interval_hours" {
  description = "Minimum number of hours between automated workload runs."
  type        = number
  default     = 1

  validation {
    condition     = var.run_interval_hours >= 1 && floor(var.run_interval_hours) == var.run_interval_hours
    error_message = "run_interval_hours must be a whole number of at least 1."
  }
}

variable "auto_stop_enabled" {
  description = "Whether the EventBridge circuit breaker may stop the profiler instance periodically."
  type        = bool
  default     = true
}

variable "auto_stop_interval_hours" {
  description = "Maximum number of hours an instance may run before the periodic auto-stop guardrail acts."
  type        = number
  default     = 1

  validation {
    condition     = var.auto_stop_interval_hours >= 1 && floor(var.auto_stop_interval_hours) == var.auto_stop_interval_hours
    error_message = "auto_stop_interval_hours must be a whole number of at least 1."
  }
}
