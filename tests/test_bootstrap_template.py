import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
TEMPLATE_PATH = ROOT / "scripts" / "user_data.sh.tftpl"
COMPUTE_PATH = ROOT / "compute.tf"
VARIABLES_PATH = ROOT / "variables.tf"
TFVARS_EXAMPLE_PATH = ROOT / "terraform.tfvars.example"


def render_user_data() -> str:
    rendered = TEMPLATE_PATH.read_text()
    replacements = {
        "${jsonencode(app_repository_url)}": json.dumps(
            "https://github.com/example/Carbontrace.git"
        ),
        "${jsonencode(app_revision)}": json.dumps("a" * 40),
        "${jsonencode(project_name)}": json.dumps("Carbontrace"),
        "${jsonencode(log_group_name)}": json.dumps(
            "/aws/carbontrace/carbontrace"
        ),
        "${run_interval_hours}": "1",
    }
    for expression, value in replacements.items():
        rendered = rendered.replace(expression, value)
    if "${" in rendered:
        raise AssertionError("Rendered user-data contains an unresolved template expression.")
    return rendered


class BootstrapTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = TEMPLATE_PATH.read_text()
        cls.rendered = render_user_data()
        cls.compute = COMPUTE_PATH.read_text()

    def test_rendered_template_is_valid_bash(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".sh") as script:
            script.write(self.rendered)
            script.flush()
            result = subprocess.run(
                ["bash", "-n", script.name],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reporter_and_cloudwatch_agent_are_unprivileged(self) -> None:
        self.assertIn("User=carbontrace", self.rendered)
        self.assertIn('"run_as_user": "cwagent"', self.rendered)
        self.assertNotIn('"run_as_user": "root"', self.rendered)

    def test_timer_interval_jitter_and_enablement_are_preserved(self) -> None:
        self.assertIn("OnUnitActiveSec=1h", self.rendered)
        self.assertIn("RandomizedDelaySec=5min", self.rendered)
        self.assertIn("systemctl enable --now carbontrace-reporter.timer", self.rendered)

    def test_imdsv2_is_required(self) -> None:
        self.assertIn('http_tokens                 = "required"', self.compute)
        self.assertIn("X-aws-ec2-metadata-token", self.rendered)

    def test_revision_is_checked_out_and_verified_as_exact_sha(self) -> None:
        self.assertIn('git -C /opt/carbontrace/app checkout --detach "$APP_REVISION"', self.rendered)
        self.assertIn(
            'test "$(git -C /opt/carbontrace/app rev-parse HEAD)" = "$APP_REVISION"',
            self.rendered,
        )

    def test_log_permissions_allow_cwagent_without_world_readability(self) -> None:
        self.assertIn("usermod --append --groups carbontrace cwagent", self.rendered)
        self.assertIn("chmod 0750 /var/log/carbontrace", self.rendered)
        self.assertIn("UMask=0027", self.rendered)
        self.assertNotIn("chmod 0755 /var/log/carbontrace", self.rendered)

    def test_reporter_publishes_under_bounded_systemd_runtime(self) -> None:
        self.assertIn("python -m app.metrics_reporter --publish", self.rendered)
        self.assertIn("TimeoutStartSec=5min", self.rendered)
        self.assertIn("CARBONTRACE_ACTIVE_LEASE_SECONDS=300", self.rendered)
        self.assertIn("CARBONTRACE_INSTANCE_ID=", self.rendered)

    def test_ami_is_explicit_and_cannot_follow_most_recent(self) -> None:
        variables = VARIABLES_PATH.read_text()
        example = TFVARS_EXAMPLE_PATH.read_text()
        ami_variable = variables.split('variable "ubuntu_ami_id"', 1)[1].split(
            'variable "key_name"', 1
        )[0]
        self.assertIn("type        = string", ami_variable)
        self.assertNotIn("default", ami_variable)
        self.assertIn('data "aws_ami" "ubuntu"', self.compute)
        self.assertIn('owners = ["099720109477"]', self.compute)
        self.assertIn('name   = "image-id"', self.compute)
        self.assertIn("values = [var.ubuntu_ami_id]", self.compute)
        self.assertNotIn("most_recent", self.compute)
        self.assertNotIn('name   = "name"', self.compute)
        self.assertIn("ami                         = data.aws_ami.ubuntu.id", self.compute)
        self.assertIn('regex("^ami-[0-9a-f]{17}$", var.ubuntu_ami_id)', variables)
        self.assertIn('ubuntu_ami_id = "ami-0d28727121d5d4a3c"', example)


if __name__ == "__main__":
    unittest.main()
