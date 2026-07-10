resource "aws_cloudwatch_dashboard" "greenops" {
  dashboard_name = "${var.project_name}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "GreenOps workload CPU utilization"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 300
          metrics = [[
            "GreenOps/App", "CPUUtilizationCustom", "Project", var.project_name,
            "InstanceType", var.instance_type, "WorkloadVersion", "v1",
          ]]
          yAxis = { left = { label = "Percent", min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "GreenOps workload memory utilization"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 300
          metrics = [[
            "GreenOps/App", "MemoryUtilizationPercent", "Project", var.project_name,
            "InstanceType", var.instance_type, "WorkloadVersion", "v1",
          ]]
          yAxis = { left = { label = "Percent", min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Modeled average power"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 300
          metrics = [[
            "GreenOps/App", "EstimatedWatts", "Project", var.project_name,
            "InstanceType", var.instance_type, "WorkloadVersion", "v1",
          ]]
          yAxis = { left = { label = "Watts", min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Modeled CO2 per workload run"
          view   = "timeSeries"
          region = var.aws_region
          stat   = "Average"
          period = 300
          metrics = [[
            "GreenOps/App", "EstimatedCO2Grams", "Project", var.project_name,
            "InstanceType", var.instance_type, "WorkloadVersion", "v1",
          ]]
          yAxis = { left = { label = "Grams CO2e", min = 0 } }
        }
      },
    ]
  })
}
