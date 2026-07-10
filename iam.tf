data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name_prefix        = "${var.project_name}-instance-"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
  description        = "Allows the GreenOps EC2 instance to publish only its approved metrics and logs."
}

resource "aws_cloudwatch_log_group" "application" {
  name              = "/aws/greenops/${var.project_name}"
  retention_in_days = 14
}

data "aws_iam_policy_document" "instance" {
  statement {
    sid       = "PublishGreenOpsMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["GreenOps/App"]
    }
  }

  statement {
    sid    = "CreateApplicationLogGroup"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
    ]
    resources = [aws_cloudwatch_log_group.application.arn]
  }

  statement {
    sid    = "WriteApplicationLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.application.arn}:*"]
  }
}

resource "aws_iam_role_policy" "instance" {
  name_prefix = "${var.project_name}-metrics-"
  role        = aws_iam_role.instance.id
  policy      = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name_prefix = "${var.project_name}-instance-"
  role        = aws_iam_role.instance.name
}
