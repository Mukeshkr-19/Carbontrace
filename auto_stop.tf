locals {
  auto_stop_schedule = var.auto_stop_interval_hours == 1 ? "rate(1 hour)" : "rate(${var.auto_stop_interval_hours} hours)"
}

data "archive_file" "auto_stop" {
  type        = "zip"
  source_file = "${path.module}/lambda/auto_stop.py"
  output_path = "${path.module}/.terraform/auto_stop.zip"
}

data "aws_iam_role" "auto_stop" {
  count = var.auto_stop_enabled ? 1 : 0
  name  = local.auto_stop_role_name
}

resource "aws_cloudwatch_log_group" "auto_stop" {
  count             = var.auto_stop_enabled ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-auto-stop"
  retention_in_days = 14
}

resource "aws_lambda_function" "auto_stop" {
  count            = var.auto_stop_enabled ? 1 : 0
  function_name    = "${var.project_name}-auto-stop"
  description      = "Periodic circuit breaker for the Carbontrace profiler instance."
  filename         = data.archive_file.auto_stop.output_path
  source_code_hash = data.archive_file.auto_stop.output_base64sha256
  handler          = "auto_stop.handler"
  runtime          = "python3.12"
  architectures    = ["arm64"]
  role             = data.aws_iam_role.auto_stop[0].arn
  timeout          = 15

  environment {
    variables = {
      INSTANCE_ID = aws_instance.profiler.id
    }
  }
}

resource "aws_cloudwatch_event_rule" "auto_stop" {
  count               = var.auto_stop_enabled ? 1 : 0
  name                = "${var.project_name}-auto-stop"
  description         = "Runs the Carbontrace EC2 auto-stop circuit breaker on a fixed schedule."
  schedule_expression = local.auto_stop_schedule
}

resource "aws_cloudwatch_event_target" "auto_stop" {
  count = var.auto_stop_enabled ? 1 : 0
  rule  = aws_cloudwatch_event_rule.auto_stop[0].name
  arn   = aws_lambda_function.auto_stop[0].arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  count         = var.auto_stop_enabled ? 1 : 0
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auto_stop[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.auto_stop[0].arn
}

resource "aws_cloudwatch_metric_alarm" "auto_stop_errors" {
  count               = var.auto_stop_enabled ? 1 : 0
  alarm_name          = "${var.project_name}-auto-stop-errors"
  alarm_description   = "Signals when the Carbontrace auto-stop circuit breaker fails."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.auto_stop[0].function_name
  }
}
