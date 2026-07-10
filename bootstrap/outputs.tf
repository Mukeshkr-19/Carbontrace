output "state_bucket_name" {
  description = "Use this value in the root module's private backend.hcl file."
  value       = aws_s3_bucket.state.bucket
}
