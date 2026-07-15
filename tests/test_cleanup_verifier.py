import importlib.util
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import unittest
from unittest.mock import patch


def load_verifier():
    module_path = Path(__file__).parents[1] / "scripts" / "verify_post_destroy.py"
    specification = importlib.util.spec_from_file_location("verify_post_destroy", module_path)
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


class CleanupVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_verifier()

    def test_mutating_aws_operation_is_rejected_before_subprocess(self) -> None:
        with patch.object(self.module.subprocess, "run") as mock_run:
            with self.assertRaisesRegex(ValueError, "Disallowed AWS operation"):
                self.module._aws(
                    "carbontrace",
                    "us-east-1",
                    "ec2",
                    "terminate-instances",
                )
        mock_run.assert_not_called()

    def test_missing_profile_or_region_fails_closed(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with redirect_stderr(StringIO()):
                self.assertEqual(self.module.main(), 2)

    def test_clear_main_stack_returns_zero(self) -> None:
        empty_responses = {
            ("ec2", "describe-instances"): {"Reservations": []},
            ("ec2", "describe-volumes"): {"Volumes": []},
            ("ec2", "describe-security-groups"): {"SecurityGroups": []},
            ("ec2", "describe-network-interfaces"): {"NetworkInterfaces": []},
            ("logs", "describe-log-groups"): {"logGroups": []},
            ("lambda", "get-function"): None,
            ("events", "describe-rule"): None,
            ("cloudwatch", "describe-alarms"): {"MetricAlarms": []},
            ("cloudwatch", "get-dashboard"): None,
        }

        def fake_aws(profile, region, service, operation, *arguments, **kwargs):
            return empty_responses[(service, operation)]

        with patch.object(self.module, "_aws", side_effect=fake_aws):
            with redirect_stdout(StringIO()):
                self.assertEqual(self.module.verify("carbontrace", "us-east-1"), 0)

    def test_remaining_instance_returns_nonzero(self) -> None:
        def fake_aws(profile, region, service, operation, *arguments, **kwargs):
            if (service, operation) == ("ec2", "describe-instances"):
                return {
                    "Reservations": [
                        {"Instances": [{"InstanceId": "i-0123456789abcdef0"}]}
                    ]
                }
            defaults = {
                ("ec2", "describe-volumes"): {"Volumes": []},
                ("ec2", "describe-security-groups"): {"SecurityGroups": []},
                ("ec2", "describe-network-interfaces"): {"NetworkInterfaces": []},
                ("logs", "describe-log-groups"): {"logGroups": []},
                ("lambda", "get-function"): None,
                ("events", "describe-rule"): None,
                ("cloudwatch", "describe-alarms"): {"MetricAlarms": []},
                ("cloudwatch", "get-dashboard"): None,
            }
            return defaults[(service, operation)]

        with patch.object(self.module, "_aws", side_effect=fake_aws):
            with redirect_stdout(StringIO()):
                self.assertEqual(self.module.verify("carbontrace", "us-east-1"), 1)


if __name__ == "__main__":
    unittest.main()
