from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError


def load_auto_stop_module():
    module_path = Path(__file__).parents[1] / "lambda" / "auto_stop.py"
    specification = importlib.util.spec_from_file_location("auto_stop", module_path)
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
INSTANCE_ID = "i-0123456789abcdef0"
BASE_ENVIRONMENT = {
    "INSTANCE_ID": INSTANCE_ID,
    "MIN_RUNTIME_SECONDS": "900",
    "MAX_ACTIVE_LEASE_SECONDS": "600",
    "ACTIVE_WORKLOAD_TAG_KEY": "CarbontraceActiveUntil",
}


def instance_response(
    state: str = "running",
    age_seconds: int = 1_800,
    tags: list[dict] | None = None,
) -> dict:
    return {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": INSTANCE_ID,
                        "State": {"Name": state},
                        "LaunchTime": NOW - timedelta(seconds=age_seconds),
                        "Tags": tags or [],
                    }
                ]
            }
        ]
    }


class AutoStopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_auto_stop_module()
        self.stdout = StringIO()
        self.client_patcher = patch.object(self.module.boto3, "client")
        self.mock_client_factory = self.client_patcher.start()
        self.ec2 = self.mock_client_factory.return_value
        self.now_patcher = patch.object(self.module, "_utc_now", return_value=NOW)
        self.now_patcher.start()
        self.environment_patcher = patch.dict(os.environ, BASE_ENVIRONMENT, clear=False)
        self.environment_patcher.start()

    def tearDown(self) -> None:
        self.environment_patcher.stop()
        self.now_patcher.stop()
        self.client_patcher.stop()

    def invoke(self) -> dict:
        with redirect_stdout(self.stdout):
            result = self.module.handler({}, None)
        self.records = [
            json.loads(line) for line in self.stdout.getvalue().splitlines() if line
        ]
        self.assertEqual(self.records[-1], result)
        return result

    def test_handler_rejects_invalid_instance_id(self) -> None:
        with patch.dict(os.environ, {"INSTANCE_ID": "all-instances"}, clear=False):
            with self.assertRaisesRegex(ValueError, "valid EC2 instance ID"):
                self.module.handler({}, None)
        self.mock_client_factory.assert_not_called()

    def test_instance_not_found_is_safe_no_op(self) -> None:
        self.ec2.describe_instances.side_effect = ClientError(
            {"Error": {"Code": "InvalidInstanceID.NotFound", "Message": "not found"}},
            "DescribeInstances",
        )
        self.assertEqual(self.invoke()["decision"], "skipped_not_found")
        self.ec2.stop_instances.assert_not_called()

    def test_stopped_instance_is_safe_no_op(self) -> None:
        self.ec2.describe_instances.return_value = instance_response(state="stopped")
        result = self.invoke()
        self.assertEqual(
            result,
            {
                "decision": "skipped_not_running",
                "instance_id": INSTANCE_ID,
                "state": "stopped",
            },
        )
        self.ec2.stop_instances.assert_not_called()

    def test_pending_instance_is_safe_no_op(self) -> None:
        self.ec2.describe_instances.return_value = instance_response(state="pending")
        result = self.invoke()
        self.assertEqual(result["decision"], "skipped_not_running")
        self.assertEqual(result["state"], "pending")
        self.ec2.stop_instances.assert_not_called()

    def test_running_instance_younger_than_grace_period_is_not_stopped(self) -> None:
        self.ec2.describe_instances.return_value = instance_response(age_seconds=899)
        result = self.invoke()
        self.assertEqual(result["decision"], "skipped_within_grace_period")
        self.assertEqual(result["age_seconds"], 899)
        self.ec2.stop_instances.assert_not_called()

    def test_running_instance_older_than_grace_period_is_evaluated(self) -> None:
        self.ec2.describe_instances.return_value = instance_response(age_seconds=901)
        self.assertEqual(self.invoke()["decision"], "stop_requested")

    def test_active_workload_protects_old_running_instance(self) -> None:
        active_until = str(int(NOW.timestamp()) + 120)
        self.ec2.describe_instances.return_value = instance_response(
            tags=[{"Key": "CarbontraceActiveUntil", "Value": active_until}]
        )
        result = self.invoke()
        self.assertEqual(result["decision"], "skipped_active_workload")
        self.assertEqual(result["active_until"], active_until)
        self.ec2.stop_instances.assert_not_called()

    def test_lease_at_maximum_permitted_horizon_is_active(self) -> None:
        active_until = str(int(NOW.timestamp()) + 600)
        self.ec2.describe_instances.return_value = instance_response(
            tags=[{"Key": "CarbontraceActiveUntil", "Value": active_until}]
        )
        result = self.invoke()
        self.assertEqual(result["decision"], "skipped_active_workload")
        self.assertEqual(result["active_until"], active_until)
        self.ec2.stop_instances.assert_not_called()

    def test_malformed_text_lease_is_emitted_and_ignored(self) -> None:
        self.ec2.describe_instances.return_value = instance_response(
            tags=[{"Key": "CarbontraceActiveUntil", "Value": "not-an-epoch"}]
        )
        result = self.invoke()
        self.assertEqual(result["decision"], "stop_requested")
        self.assertEqual(
            [record["decision"] for record in self.records],
            ["ignored_invalid_lease", "ignored_invalid_lease", "stop_requested"],
        )
        self.assertTrue(
            all(
                record.get("reason") == "malformed"
                for record in self.records[:-1]
            )
        )

    def test_empty_lease_is_emitted_and_ignored(self) -> None:
        self.ec2.describe_instances.return_value = instance_response(
            tags=[{"Key": "CarbontraceActiveUntil", "Value": ""}]
        )
        result = self.invoke()
        self.assertEqual(result["decision"], "stop_requested")
        self.assertEqual(self.records[0]["decision"], "ignored_invalid_lease")
        self.assertEqual(self.records[0]["reason"], "malformed")

    def test_negative_epoch_is_expired_and_does_not_suppress_stop(self) -> None:
        self.ec2.describe_instances.return_value = instance_response(
            tags=[{"Key": "CarbontraceActiveUntil", "Value": "-1"}]
        )
        result = self.invoke()
        self.assertEqual(result["decision"], "stop_requested")
        self.assertNotIn("ignored_invalid_lease", self.stdout.getvalue())

    def test_extremely_future_dated_lease_is_emitted_and_ignored(self) -> None:
        active_until = str(int(NOW.timestamp()) + 86_400)
        self.ec2.describe_instances.return_value = instance_response(
            tags=[{"Key": "CarbontraceActiveUntil", "Value": active_until}]
        )
        result = self.invoke()
        self.assertEqual(result["decision"], "stop_requested")
        self.assertEqual(self.records[0]["decision"], "ignored_invalid_lease")
        self.assertEqual(self.records[0]["reason"], "beyond_maximum_horizon")

    def test_lease_acquired_between_describe_calls_prevents_stop(self) -> None:
        active_until = str(int(NOW.timestamp()) + 120)
        self.ec2.describe_instances.side_effect = [
            instance_response(),
            instance_response(
                tags=[{"Key": "CarbontraceActiveUntil", "Value": active_until}]
            ),
        ]
        result = self.invoke()
        self.assertEqual(result["decision"], "skipped_active_workload")
        self.assertEqual(result["phase"], "pre_stop")
        self.ec2.stop_instances.assert_not_called()

    def test_safe_stop_after_grace_period_and_expired_lease(self) -> None:
        self.ec2.describe_instances.return_value = instance_response(
            tags=[
                {
                    "Key": "CarbontraceActiveUntil",
                    "Value": str(int(NOW.timestamp()) - 1),
                }
            ]
        )
        result = self.invoke()
        self.assertEqual(result["decision"], "stop_requested")
        self.assertEqual(self.ec2.describe_instances.call_count, 2)
        self.ec2.describe_instances.assert_called_with(InstanceIds=[INSTANCE_ID])
        self.ec2.stop_instances.assert_called_once_with(InstanceIds=[INSTANCE_ID])
        config = self.mock_client_factory.call_args.kwargs["config"]
        self.assertEqual(config.connect_timeout, 3)
        self.assertEqual(config.read_timeout, 5)
        self.assertEqual(config.retries["total_max_attempts"], 3)

    def test_invalid_or_missing_max_active_lease_seconds_fails_before_client(self) -> None:
        for value in (None, "", "0", "-1", "not-a-number", "3601"):
            with self.subTest(value=value):
                environment = dict(BASE_ENVIRONMENT)
                if value is None:
                    environment.pop("MAX_ACTIVE_LEASE_SECONDS")
                else:
                    environment["MAX_ACTIVE_LEASE_SECONDS"] = value
                self.mock_client_factory.reset_mock()
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(ValueError, "positive bounded"):
                        self.module.handler({}, None)
                self.mock_client_factory.assert_not_called()

    def test_invalid_or_missing_min_runtime_seconds_fails_before_client(self) -> None:
        for value in (None, "", "0", "-1", "not-a-number", "86401"):
            with self.subTest(value=value):
                environment = dict(BASE_ENVIRONMENT)
                if value is None:
                    environment.pop("MIN_RUNTIME_SECONDS")
                else:
                    environment["MIN_RUNTIME_SECONDS"] = value
                self.mock_client_factory.reset_mock()
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaisesRegex(ValueError, "positive bounded"):
                        self.module.handler({}, None)
                self.mock_client_factory.assert_not_called()

    def test_describe_instances_failure_is_logged_and_raised(self) -> None:
        self.ec2.describe_instances.side_effect = ClientError(
            {"Error": {"Code": "UnauthorizedOperation", "Message": "sensitive"}},
            "DescribeInstances",
        )
        with self.assertRaises(ClientError):
            with redirect_stdout(self.stdout):
                self.module.handler({}, None)
        record = json.loads(self.stdout.getvalue())
        self.assertEqual(record["decision"], "describe_failure")
        self.assertEqual(record["error_code"], "UnauthorizedOperation")
        self.assertNotIn("sensitive", self.stdout.getvalue())

    def test_stop_instances_failure_is_logged_and_raised(self) -> None:
        self.ec2.describe_instances.return_value = instance_response()
        self.ec2.stop_instances.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "sensitive"}},
            "StopInstances",
        )
        with self.assertRaises(ClientError):
            with redirect_stdout(self.stdout):
                self.module.handler({}, None)
        record = json.loads(self.stdout.getvalue())
        self.assertEqual(record["decision"], "stop_failure")
        self.assertEqual(record["error_code"], "AccessDenied")
        self.assertNotIn("sensitive", self.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
