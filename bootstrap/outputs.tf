output "state_bucket_name" {
  description = "Use this value in the root module's backend.hcl file."
  value       = aws_s3_bucket.state.bucket
}

output "lock_table_name" {
  description = "Use this value in the root module's backend.hcl file."
  value       = aws_dynamodb_table.lock.name
}
