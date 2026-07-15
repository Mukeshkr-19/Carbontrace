variable "aws_region" {
  description = "AWS Region in which the Carbontrace infrastructure is provisioned."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "Carbontrace must run only in us-east-1."
  }
}

variable "aws_account_id" {
  description = "AWS account ID allowed for this deployment."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be exactly 12 digits."
  }
}

variable "project_name" {
  description = "Lowercase project identifier used in AWS resource names."
  type        = string
  default     = "carbontrace"

  validation {
    condition     = var.project_name == "carbontrace"
    error_message = "project_name must remain carbontrace; the reviewed IAM policies and runtime-role conditions are scoped to this exact name."
  }
}

variable "project_tag" {
  description = "Canonical Project tag and metric-dimension value."
  type        = string
  default     = "Carbontrace"

  validation {
    condition     = var.project_tag == "Carbontrace"
    error_message = "project_tag must be exactly Carbontrace."
  }
}

variable "my_ip" {
  description = "Globally routable public IPv4 address allowed to use SSH, expressed as exactly one /32 CIDR."
  type        = string
  sensitive   = true

  validation {
    condition = try(
      length(regexall("^(?:[0-9]{1,3}\\.){3}[0-9]{1,3}/32$", var.my_ip)) == 1 &&
      alltrue([
        for octet in split(".", split("/", var.my_ip)[0]) :
        tonumber(octet) >= 0 && tonumber(octet) <= 255
      ]) &&
      !(
        tonumber(split(".", split("/", var.my_ip)[0])[0]) == 0 ||
        tonumber(split(".", split("/", var.my_ip)[0])[0]) == 10 ||
        tonumber(split(".", split("/", var.my_ip)[0])[0]) == 127 ||
        tonumber(split(".", split("/", var.my_ip)[0])[0]) >= 224 ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 100 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) >= 64 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) <= 127
        ) ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 169 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) == 254
        ) ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 172 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) >= 16 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) <= 31
        ) ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 192 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) == 168
        ) ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 192 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) == 0 &&
          tonumber(split(".", split("/", var.my_ip)[0])[2]) == 0
        ) ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 192 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) == 0 &&
          tonumber(split(".", split("/", var.my_ip)[0])[2]) == 2
        ) ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 192 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) == 88 &&
          tonumber(split(".", split("/", var.my_ip)[0])[2]) == 99
        ) ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 198 &&
          contains([18, 19], tonumber(split(".", split("/", var.my_ip)[0])[1]))
        ) ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 198 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) == 51 &&
          tonumber(split(".", split("/", var.my_ip)[0])[2]) == 100
        ) ||
        (
          tonumber(split(".", split("/", var.my_ip)[0])[0]) == 203 &&
          tonumber(split(".", split("/", var.my_ip)[0])[1]) == 0 &&
          tonumber(split(".", split("/", var.my_ip)[0])[2]) == 113
        )
      ),
      false,
    )
    error_message = "my_ip must be a globally routable public IPv4 /32; private, shared, loopback, link-local, documentation, benchmark, multicast, reserved, IPv6, 0.0.0.0/32, and wider CIDRs are rejected."
  }
}

variable "instance_type" {
  description = "EC2 instance type for the bounded Carbontrace proof of concept."
  type        = string
  default     = "t3.micro"

  validation {
    condition     = var.instance_type == "t3.micro"
    error_message = "instance_type must remain t3.micro for this cost-bounded proof of concept."
  }
}

variable "ubuntu_ami_id" {
  description = "Explicitly reviewed Canonical Ubuntu 22.04 amd64 AMI ID for us-east-1. Update only through reviewed source control."
  type        = string

  validation {
    condition     = can(regex("^ami-[0-9a-f]{17}$", var.ubuntu_ami_id))
    error_message = "ubuntu_ami_id must be a 17-hex-character EC2 AMI ID."
  }
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

variable "auto_stop_grace_period_seconds" {
  description = "Minimum instance runtime after launch or restart before auto-stop may request a stop."
  type        = number
  default     = 900

  validation {
    condition = (
      var.auto_stop_grace_period_seconds >= 300 &&
      var.auto_stop_grace_period_seconds <= 3600 &&
      floor(var.auto_stop_grace_period_seconds) == var.auto_stop_grace_period_seconds
    )
    error_message = "auto_stop_grace_period_seconds must be a whole number from 300 through 3600."
  }
}
