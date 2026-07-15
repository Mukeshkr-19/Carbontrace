import argparse
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.drain_app import WorkloadSummary
from app.metrics_reporter import (
    ACTIVE_WORKLOAD_TAG_KEY,
    CARBON_METHODOLOGY,
    CODECARBON_VERSION,
    PUE,
    TRACKING_MODE,
    ActiveWorkLease,
    EstimationMethodology,
    ResourceSummary,
    RunSummary,
    main,
    measure_run,
    publish_metrics,
)


def make_summary(**overrides) -> RunSummary:
    summary = RunSummary(
        run_id="test-run-id",
        started_at="2026-07-10T00:00:00+00:00",
        revision="0123456789abcdef0123456789abcdef01234567",
        workload=WorkloadSummary(1.0, 1, 100, 10_000, 0),
        resources=ResourceSummary(10.0, 20.0, 1.0, 2.0, 1),
        methodology=EstimationMethodology(
            estimator="CodeCarbon",
            estimator_version=CODECARBON_VERSION,
            tracking_mode=TRACKING_MODE,
            cloud_provider="aws",
            cloud_region="us-east-1",
            carbon_country_iso_code="USA",
            carbon_region="virginia",
            carbon_intensity_source=CARBON_METHODOLOGY,
            carbon_intensity_g_co2e_per_kwh=250.0,
            pue=PUE,
        ),
        estimated_energy_wh=2.0,
        estimated_watts=7.2,
        estimated_co2_grams=0.5,
    )
    return replace(summary, **overrides)


def lease_response(value: str | None) -> dict:
    tags = [] if value is None else [{"Key": ACTIVE_WORKLOAD_TAG_KEY, "Value": value}]
    return {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-0123456789abcdef0",
                        "Tags": tags,
                    }
                ]
            }
        ]
    }


class MetricsReporterTests(unittest.TestCase):
    def test_active_work_lease_visible_immediately_permits_execution(self) -> None:
        with (
            patch("app.metrics_reporter.time.time", return_value=1_700_000_000),
            patch("app.metrics_reporter.time.monotonic", return_value=100.0),
            patch("app.metrics_reporter.time.sleep") as mock_sleep,
            patch("app.metrics_reporter.boto3.client") as mock_client,
        ):
            mock_client.return_value.describe_instances.return_value = lease_response(
                "1700000300"
            )
            with ActiveWorkLease("i-0123456789abcdef0", "us-east-1", 300):
                mock_client.return_value.create_tags.assert_called_once_with(
                    Resources=["i-0123456789abcdef0"],
                    Tags=[
                        {
                            "Key": ACTIVE_WORKLOAD_TAG_KEY,
                            "Value": "1700000300",
                        }
                    ],
                )

            mock_client.return_value.describe_instances.assert_called_once_with(
                InstanceIds=["i-0123456789abcdef0"]
            )
            mock_sleep.assert_not_called()

        mock_client.return_value.delete_tags.assert_called_once_with(
            Resources=["i-0123456789abcdef0"],
            Tags=[{"Key": ACTIVE_WORKLOAD_TAG_KEY}],
        )
        _, client_kwargs = mock_client.call_args
        self.assertEqual(client_kwargs["region_name"], "us-east-1")
        config = client_kwargs["config"]
        self.assertEqual(config.connect_timeout, 3)
        self.assertEqual(config.read_timeout, 5)
        self.assertEqual(config.retries["mode"], "standard")
        self.assertEqual(config.retries["total_max_attempts"], 3)

    def test_active_work_lease_eventual_visibility_succeeds(self) -> None:
        with (
            patch("app.metrics_reporter.time.time", return_value=1_700_000_000),
            patch("app.metrics_reporter.time.monotonic", return_value=100.0),
            patch("app.metrics_reporter.time.sleep") as mock_sleep,
            patch("app.metrics_reporter.boto3.client") as mock_client,
        ):
            mock_client.return_value.describe_instances.side_effect = [
                lease_response(None),
                lease_response("stale"),
                lease_response("1700000300"),
            ]
            with ActiveWorkLease("i-0123456789abcdef0", "us-east-1", 300):
                pass

        self.assertEqual(mock_client.return_value.describe_instances.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_active_work_lease_never_visible_raises_and_attempts_cleanup(self) -> None:
        with (
            patch("app.metrics_reporter.time.time", return_value=1_700_000_000),
            patch("app.metrics_reporter.time.monotonic", return_value=100.0),
            patch("app.metrics_reporter.time.sleep"),
            patch("app.metrics_reporter.boto3.client") as mock_client,
        ):
            mock_client.return_value.describe_instances.return_value = lease_response(None)
            with self.assertRaisesRegex(RuntimeError, "lease was not visible"):
                with ActiveWorkLease("i-0123456789abcdef0", "us-east-1", 300):
                    self.fail("Execution entered an unconfirmed lease context.")

        self.assertEqual(mock_client.return_value.describe_instances.call_count, 3)
        mock_client.return_value.delete_tags.assert_called_once_with(
            Resources=["i-0123456789abcdef0"],
            Tags=[{"Key": ACTIVE_WORKLOAD_TAG_KEY}],
        )

    @patch("app.metrics_reporter.publish_metrics")
    @patch("app.metrics_reporter.measure_run")
    @patch("app.metrics_reporter.parse_args")
    def test_visibility_failure_prevents_measurement_workload_and_publication(
        self, mock_parse_args, mock_measure_run, mock_publish_metrics
    ) -> None:
        mock_parse_args.return_value = argparse.Namespace(
            duration_seconds=30,
            work_size=100,
            project_name="Carbontrace",
            instance_type="t3.micro",
            region="us-east-1",
            publish=True,
        )
        environment = {
            "CARBONTRACE_INSTANCE_ID": "i-0123456789abcdef0",
            "CARBONTRACE_ACTIVE_LEASE_SECONDS": "300",
        }
        with (
            patch.dict("app.metrics_reporter.os.environ", environment, clear=False),
            patch("app.metrics_reporter.time.time", return_value=1_700_000_000),
            patch("app.metrics_reporter.time.monotonic", return_value=100.0),
            patch("app.metrics_reporter.time.sleep"),
            patch("app.metrics_reporter.boto3.client") as mock_client,
        ):
            mock_client.return_value.describe_instances.return_value = lease_response(None)
            with self.assertRaisesRegex(RuntimeError, "lease was not visible"):
                main()

        mock_measure_run.assert_not_called()
        mock_publish_metrics.assert_not_called()
        mock_client.return_value.delete_tags.assert_called_once_with(
            Resources=["i-0123456789abcdef0"],
            Tags=[{"Key": ACTIVE_WORKLOAD_TAG_KEY}],
        )

    @patch("app.metrics_reporter.boto3.client")
    def test_publish_uses_exact_metric_contract_and_bounded_client(self, mock_client) -> None:
        summary = make_summary()

        publish_metrics(summary, "Carbontrace", "t3.micro", "us-east-1")

        mock_client.assert_called_once()
        _, client_kwargs = mock_client.call_args
        self.assertEqual(client_kwargs["region_name"], "us-east-1")
        config = client_kwargs["config"]
        self.assertEqual(config.connect_timeout, 3)
        self.assertEqual(config.read_timeout, 5)
        self.assertEqual(config.retries["mode"], "standard")
        self.assertEqual(config.retries["total_max_attempts"], 3)

        client = mock_client.return_value
        client.put_metric_data.assert_called_once()
        kwargs = client.put_metric_data.call_args.kwargs
        self.assertEqual(kwargs["Namespace"], "Carbontrace/App")
        metrics = kwargs["MetricData"]
        self.assertEqual(
            {metric["MetricName"]: metric["Unit"] for metric in metrics},
            {
                "CPUUtilizationCustom": "Percent",
                "MemoryUtilizationPercent": "Percent",
                "EstimatedWatts": "None",
                "EstimatedCO2Grams": "None",
                "EstimatedEnergyWh": "None",
            },
        )
        expected_dimensions = [
            {"Name": "Project", "Value": "Carbontrace"},
            {"Name": "InstanceType", "Value": "t3.micro"},
            {"Name": "WorkloadVersion", "Value": "v1"},
        ]
        for metric in metrics:
            self.assertEqual(metric["Dimensions"], expected_dimensions)
            self.assertEqual(metric["Timestamp"].year, 2026)
            self.assertNotIn("RunId", [item["Name"] for item in metric["Dimensions"]])

    @patch("app.metrics_reporter.boto3.client")
    def test_publish_rejects_invalid_values_before_client_creation(self, mock_client) -> None:
        invalid_values = [
            True,
            False,
            "1.25",
            "nan",
            "not-a-number",
            b"1.25",
            None,
            float("nan"),
            float("inf"),
            float("-inf"),
            -0.1,
        ]

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "finite, non-negative"):
                    publish_metrics(
                        replace(make_summary(), estimated_watts=value),
                        "Carbontrace",
                        "t3.micro",
                        "us-east-1",
                    )

        mock_client.assert_not_called()

    @patch("app.metrics_reporter.run_workload")
    @patch("app.metrics_reporter.ProcessSampler")
    @patch("app.metrics_reporter.OfflineEmissionsTracker")
    def test_measure_run_records_explicit_scientific_methodology(
        self, mock_tracker_class, mock_sampler_class, mock_run_workload
    ) -> None:
        mock_tracker = mock_tracker_class.return_value
        mock_tracker.final_emissions_data = SimpleNamespace(
            energy_consumed=0.002,
            emissions=0.0005,
        )
        mock_sampler_class.return_value.stop.return_value = ResourceSummary(
            10.0, 20.0, 1.0, 2.0, 2
        )
        mock_run_workload.return_value = WorkloadSummary(30.0, 1, 100, 10_000, 0)

        summary = measure_run(30, 100, "us-east-1")

        mock_tracker_class.assert_called_once_with(
            project_name="carbontrace",
            measure_power_secs=1,
            save_to_file=False,
            log_level="error",
            tracking_mode="process",
            pue=1.0,
            country_iso_code="USA",
            region="virginia",
        )
        mock_tracker.start.assert_called_once_with()
        mock_tracker.stop.assert_called_once_with()
        self.assertEqual(summary.methodology.estimator, "CodeCarbon")
        self.assertEqual(summary.methodology.estimator_version, CODECARBON_VERSION)
        self.assertEqual(summary.methodology.cloud_provider, "aws")
        self.assertEqual(summary.methodology.cloud_region, "us-east-1")
        self.assertEqual(summary.methodology.tracking_mode, "process")
        self.assertEqual(summary.methodology.pue, 1.0)
        self.assertEqual(summary.methodology.carbon_region, "virginia")
        self.assertEqual(summary.methodology.carbon_intensity_g_co2e_per_kwh, 250.0)

    def test_measure_run_rejects_region_without_reviewed_methodology(self) -> None:
        with self.assertRaisesRegex(ValueError, "No reviewed carbon methodology"):
            measure_run(30, 100, "eu-west-1")

    @patch("app.metrics_reporter.boto3.client")
    def test_publish_propagates_cloudwatch_failure(self, mock_client) -> None:
        mock_client.return_value.put_metric_data.side_effect = RuntimeError(
            "CloudWatch unavailable"
        )

        with self.assertRaisesRegex(RuntimeError, "CloudWatch unavailable"):
            publish_metrics(make_summary(), "Carbontrace", "t3.micro", "us-east-1")

    @patch("app.metrics_reporter.publish_metrics")
    @patch("app.metrics_reporter.measure_run")
    @patch("app.metrics_reporter.parse_args")
    @patch("app.metrics_reporter.active_work_lease")
    def test_main_logs_publish_success_only_after_publish_returns(
        self, mock_lease, mock_parse_args, mock_measure_run, mock_publish_metrics
    ) -> None:
        mock_parse_args.return_value = argparse.Namespace(
            duration_seconds=30,
            work_size=100,
            project_name="Carbontrace",
            instance_type="t3.micro",
            region="us-east-1",
            publish=True,
        )
        mock_measure_run.return_value = make_summary()
        output = StringIO()

        with redirect_stdout(output):
            main()

        mock_publish_metrics.assert_called_once()
        mock_lease.assert_called_once_with("us-east-1")
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(
            [record["event"] for record in records],
            ["measurement_complete", "publish_success"],
        )

    @patch("app.metrics_reporter.publish_metrics")
    @patch("app.metrics_reporter.measure_run")
    @patch("app.metrics_reporter.parse_args")
    @patch("app.metrics_reporter.active_work_lease")
    def test_main_logs_publish_failure_and_reraises(
        self, mock_lease, mock_parse_args, mock_measure_run, mock_publish_metrics
    ) -> None:
        mock_parse_args.return_value = argparse.Namespace(
            duration_seconds=30,
            work_size=100,
            project_name="Carbontrace",
            instance_type="t3.micro",
            region="us-east-1",
            publish=True,
        )
        mock_measure_run.return_value = make_summary()
        mock_publish_metrics.side_effect = RuntimeError("CloudWatch unavailable")
        output = StringIO()

        with self.assertRaisesRegex(RuntimeError, "CloudWatch unavailable"):
            with redirect_stdout(output):
                main()

        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(
            [record["event"] for record in records],
            ["measurement_complete", "publish_failure"],
        )
        self.assertEqual(records[-1]["error_type"], "RuntimeError")
        self.assertNotIn("publish_success", output.getvalue())


if __name__ == "__main__":
    unittest.main()
