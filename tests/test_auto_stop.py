import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


def load_auto_stop_module():
    module_path = Path(__file__).parents[1] / "lambda" / "auto_stop.py"
    specification = importlib.util.spec_from_file_location("auto_stop", module_path)
    module = importlib.util.module_from_spec(specification)
    assert specification.loader is not None
    specification.loader.exec_module(module)
    return module


class AutoStopTests(unittest.TestCase):
    @patch.dict(os.environ, {"INSTANCE_ID": "i-0123456789abcdef0"}, clear=False)
    def test_handler_stops_only_configured_instance(self) -> None:
        module = load_auto_stop_module()
        with patch.object(module.boto3, "client") as mock_client:
            mock_client.return_value.stop_instances.return_value = {"StoppingInstances": [{"CurrentState": {"Name": "stopping"}}]}

            result = module.handler({}, None)

        mock_client.return_value.stop_instances.assert_called_once_with(InstanceIds=["i-0123456789abcdef0"])
        self.assertEqual(result["instance_id"], "i-0123456789abcdef0")
