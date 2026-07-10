variable "aws_region" {
  description = "AWS Region for the Terraform state backend."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "AWS account ID allowed to own the Terraform state backend."
  type        = string
  default     = "562325340670"

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be exactly 12 digits."
  }
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for this project's Terraform state."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be a valid, globally unique S3 bucket name."
  }
}
