"""Native CLI capture is mocked; no installation or authentication state is read."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_cli_fixture", ROOT / "scripts/tari_cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class NativeCLITests(unittest.TestCase):
    def test_structured_argv_and_banner_json(self):
        result = subprocess.CompletedProcess([], 0, 'Native notice\n{"jobs": []}\n', "private diagnostic")
        with patch.object(cli, "compose_invocation", return_value=(["docker", "fixture"], {})) as invocation, \
                patch.object(cli.subprocess, "run", return_value=result) as run:
            self.assertEqual(cli.openclaw_json("automations", "list", "--all", "--json"), {"jobs": []})
        self.assertEqual(invocation.call_args.args, ("exec", "-T", "tari", "openclaw", "automations", "list", "--all", "--json"))
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_failures_do_not_expose_diagnostics(self):
        cases = [(1, '{"token":"private-value"}'), (0, "private invalid response"),
                 (0, json.dumps("private scalar")), (0, "x" * 1048577)]
        for code, stdout in cases:
            with self.subTest(code=code, size=len(stdout)), \
                    patch.object(cli, "compose_invocation", return_value=(["fixture"], {})), \
                    patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], code, stdout, "private")):
                with self.assertRaises(ValueError) as error:
                    cli.openclaw_json("channels", "status", "--json")
                self.assertNotIn("private", str(error.exception))
                self.assertIn("unknown outcome", str(error.exception))

    def test_timeout_preserves_uncertainty(self):
        with patch.object(cli, "compose_invocation", return_value=(["fixture"], {})), \
                patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("fixture", 45)):
            with self.assertRaisesRegex(ValueError, "must not be blindly retried"):
                cli.openclaw_json("automations", "enable", "fixture-id")

    def test_unknown_integration_name_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            cli.optional_cli("../../unrelated")


if __name__ == "__main__":
    unittest.main()
