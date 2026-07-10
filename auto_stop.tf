locals {
  auto_stop_schedule = var.auto_stop_interval_hours == 1 ? "rate(1 hour)" : "rate(${var.auto_stop_interval_hours} hours)"
}

data "archive_file" "auto_stop" {
  type        = "zip"
  source_file = "${path.module}/lambda/auto_stop.py"
  output_path = "${path.module}/.terraform/auto_stop.zip"
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "auto_stop" {
  count              = var.auto_stop_enabled ? 1 : 0
  name_prefix        = "${var.project_name}-auto-stop-"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  description        = "Allows the GreenOps auto-stop function to stop only its profiler instance."
}

data "aws_iam_policy_document" "auto_stop" {
  statement {
    sid       = "StopOnlyProfilerInstance"
    effect    = "Allow"
    actions   = ["ec2:StopInstances"]
    resources = [aws_instance.profiler.arn]
  }

  statement {
    sid    = "WriteOwnExecutionLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.auto_stop[0].arn}:*"]
  }
}

resource "aws_iam_role_policy" "auto_stop" {
  count       = var.auto_stop_enabled ? 1 : 0
  name_prefix = "${var.project_name}-auto-stop-"
  role        = aws_iam_role.auto_stop[0].id
  policy      = data.aws_iam_policy_document.auto_stop.json
}

resource "aws_cloudwatch_log_group" "auto_stop" {
  count             = var.auto_stop_enabled ? 1 : 0
  name              = "/aws/lambda/${var.project_name}-auto-stop"
  retention_in_days = 14
}

resource "aws_lambda_function" "auto_stop" {
  count            = var.auto_stop_enabled ? 1 : 0
  function_name    = "${var.project_name}-auto-stop"
  description      = "Periodic circuit breaker for the GreenOps profiler instance."
  filename         = data.archive_file.auto_stop.output_path
  source_code_hash = data.archive_file.auto_stop.output_base64sha256
  handler          = "auto_stop.handler"
  runtime          = "python3.12"
  architectures    = ["arm64"]
  role             = aws_iam_role.auto_stop[0].arn
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
  description         = "Runs the GreenOps EC2 auto-stop circuit breaker on a fixed schedule."
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
