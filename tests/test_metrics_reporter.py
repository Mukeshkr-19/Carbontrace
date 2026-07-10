import unittest
from unittest.mock import patch

from app.drain_app import WorkloadSummary
from app.metrics_reporter import ResourceSummary, RunSummary, publish_metrics


class MetricsReporterTests(unittest.TestCase):
    @patch("app.metrics_reporter.boto3.client")
    def test_publish_uses_stable_dimensions_and_expected_namespace(self, mock_client) -> None:
        summary = RunSummary(
            run_id="test-run-id",
            started_at="2026-07-10T00:00:00+00:00",
            workload=WorkloadSummary(1.0, 1, 100, 10_000, 0),
            resources=ResourceSummary(10.0, 20.0, 1.0, 2.0, 1),
            estimated_energy_wh=2.0,
            estimated_watts=7.2,
            estimated_co2_grams=0.5,
        )

        publish_metrics(summary, "carbontrace", "t3.micro", "us-east-1")

        client = mock_client.return_value
        client.put_metric_data.assert_called_once()
        kwargs = client.put_metric_data.call_args.kwargs
        self.assertEqual(kwargs["Namespace"], "GreenOps/App")
        self.assertEqual(len(kwargs["MetricData"]), 5)
        dimensions = kwargs["MetricData"][0]["Dimensions"]
        self.assertNotIn("RunId", [dimension["Name"] for dimension in dimensions])
        self.assertEqual(kwargs["MetricData"][0]["Timestamp"].year, 2026)


if __name__ == "__main__":
    unittest.main()
