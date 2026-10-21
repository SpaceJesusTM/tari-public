import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("diagnostic_cli", ROOT / "scripts/tari_cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class DiagnosticTests(unittest.TestCase):
    def inspect(self, auth, code=0):
        payload = {"resolvedDefault": "openai/example", "auth": auth}
        completed = subprocess.CompletedProcess([], code, json.dumps(payload), "PRIVATE DIAGNOSTIC")
        with patch.object(cli, "compose_invocation", return_value=(["docker", "models-status"], {"OPENAI_API_KEY": ""})) as invocation, patch.object(cli.subprocess, "run", return_value=completed):
            result = cli.model_auth_status()
        arguments = invocation.call_args.args
        self.assertIn("--check", arguments)
        self.assertIn("--agent", arguments)
        self.assertIn("tari", arguments)
        self.assertNotIn("--probe", arguments)
        return result

    def test_managed_oauth_without_environment_key_is_configured_not_inference(self):
        state, message = self.inspect({"providersWithOAuth": ["openai"], "providers": [{"provider": "openai", "profiles": {"oauth": 1}}],
                                      "runtimeAuthRoutes": [{"provider": "openai", "status": "usable"}]})
        self.assertEqual(state, "configured")
        self.assertIn("live inference unverified", message)

    def test_local_or_other_supported_provider_does_not_require_named_env_keys(self):
        self.assertEqual(self.inspect({"providers": [], "missingProvidersInUse": []})[0], "configured")

    def test_missing_expired_unsupported_and_unavailable_are_distinguished(self):
        cases = [({"missingProvidersInUse": ["openai"]}, "missing"),
                 ({"runtimeAuthRoutes": [{"status": "expired"}]}, "expired"),
                 ({"modelRouteIssues": [{"code": "unsupported_provider"}]}, "unsupported"),
                 ({"runtimeAuthRoutes": [{"status": "unavailable", "authStatus": "usable"}]}, "unavailable")]
        for auth, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.inspect(auth, 1)[0], expected)

    def test_expiring_and_indeterminate_do_not_claim_valid_inference(self):
        self.assertEqual(self.inspect({}, 2)[0], "expiring")
        self.assertEqual(self.inspect({"runtimeAuthRoutes": [{"status": "indeterminate"}]}, 1)[0], "unverified")

    def test_failed_or_malformed_command_is_unverified_and_does_not_disclose_output(self):
        for stdout in ['{"error":"PRIVATE CONTENT"}', "PRIVATE INVALID JSON"]:
            with patch.object(cli, "compose_invocation", return_value=(["docker"], {})), patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout, "PRIVATE TOKEN")):
                result = cli.model_auth_status()
            self.assertEqual(result[0], "unverified")
            self.assertNotIn("PRIVATE", str(result))
        with patch.object(cli, "compose_invocation", return_value=(["docker"], {})), patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 45)):
            self.assertEqual(cli.model_auth_status()[0], "unverified")

    def test_doctor_accepts_managed_auth_with_healthy_gateway_and_no_api_key_env(self):
        base = ROOT / ".test-tmp"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            state = root / ".local/state"
            (state / "workspace/bin").mkdir(parents=True)
            (state / "workspace/AGENTS.md").write_text("Fictional instructions")
            (state / "workspace/bin/day-planner").write_text("Fictional helper")
            (state / "openclaw.json").write_text(json.dumps({"agents": {"defaults": {"userTimezone": "UTC"}}}))
            output = io.StringIO()
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.status = 200
            opener = unittest.mock.Mock()
            opener.open.return_value = response
            with patch.object(cli, "ROOT", root), patch.object(cli, "LOCAL", root / ".local"), patch.object(cli, "read_env", return_value={"TARI_PORT": "18899", "TARI_TIMEZONE": "UTC"}), patch.object(cli.shutil, "which", return_value="docker"), patch.object(cli.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "29.0", "")), patch.object(cli, "model_auth_status", return_value=("configured", "OpenClaw check passed; live inference unverified")), patch.object(cli.urllib.request, "build_opener", return_value=opener), redirect_stdout(output):
                self.assertEqual(cli.doctor(), 0)
            self.assertIn("live inference unverified", output.getvalue())
            self.assertNotIn("No model API key", output.getvalue())

    def test_start_does_not_warn_that_an_environment_key_is_required(self):
        with patch("sys.argv", ["tari", "start"]), patch.object(cli, "dailygrad_cli", return_value=None), patch.object(cli, "compose", return_value=0) as compose, patch.object(cli, "read_env") as read_env:
            self.assertEqual(cli.main(), 0)
        read_env.assert_not_called()
        compose.assert_called_once_with("up", "-d", "--build", "tari")


if __name__ == "__main__":
    unittest.main()
