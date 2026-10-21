import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tari_cli", ROOT / "scripts/tari_cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".test-tmp"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "config/openclaw.json").write_text((ROOT / "config/openclaw.json").read_text())
        (self.root / "workspace/bin").mkdir(parents=True)
        (self.root / "workspace/IDENTITY.md").write_text("{{TARI_NAME}}")
        (self.root / "workspace/USER.md").write_text("{{TARI_TIMEZONE}}")
        (self.root / "workspace/bin/tool").write_text("#!/bin/sh\nexit 0\n")
        self.patches = [patch.object(cli, "ROOT", self.root), patch.object(cli, "LOCAL", self.root / ".local")]
        for item in self.patches:
            item.start()
        self.args = argparse.Namespace(timezone="UTC", port=18899, name="Example", model="openai/example")

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def initialize(self):
        with patch("builtins.print"):
            cli.initialize(self.args)

    def test_first_run_private_and_idempotent(self):
        self.initialize()
        local = self.root / ".local"
        token = cli.read_env()["OPENCLAW_GATEWAY_TOKEN"]
        identity = local / "state/workspace/IDENTITY.md"
        self.assertEqual(identity.read_text(), "Example")
        identity.write_text("Edited by owner")
        self.initialize()
        self.assertEqual(token, cli.read_env()["OPENCLAW_GATEWAY_TOKEN"])
        self.assertEqual(identity.read_text(), "Edited by owner")
        self.assertEqual((local / "runtime.env").stat().st_mode & 0o777, 0o600)
        self.assertEqual((local / "state/workspace/bin/tool").stat().st_mode & 0o777, 0o700)
        config = json.loads((local / "state/openclaw.json").read_text())
        self.assertEqual(config["agents"]["defaults"]["model"]["primary"], "openai/example")

    def test_reject_symlink_local_before_initialization(self):
        target = self.root / "other"
        target.mkdir()
        (self.root / ".local").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.initialize()
        self.assertFalse(any(target.iterdir()))

    def test_reject_symlink_in_workspace_templates(self):
        (self.root / "workspace/evil").symlink_to(self.root / "config/openclaw.json")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.initialize()

    def test_compose_cannot_inherit_production_overrides(self):
        self.initialize()
        with patch.dict(os.environ, {"COMPOSE_FILE": "/fictional/production.yml", "OPENCLAW_STATE_DIR": "/fictional/private", "TARI_PORT": "18789"}), patch.object(cli.subprocess, "call", return_value=0) as run:
            cli.compose("ps")
        command = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertEqual(command[command.index("-f") + 1], str(self.root / "compose.yaml"))
        self.assertNotIn("COMPOSE_FILE", env)
        self.assertNotIn("OPENCLAW_STATE_DIR", env)
        self.assertEqual(env["TARI_PORT"], "18899")

    def test_chat_bearer_and_stable_conversation(self):
        self.initialize()
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"choices":[{"message":{"content":"Hello"}}]}'
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.object(cli.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(cli.chat_request("Hello", "conversation-one"), "Hello")
            self.assertEqual(cli.chat_request("Continue", "conversation-one"), "Hello")
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual([json.loads(req.data)["user"] for req in requests], ["conversation-one"] * 2)
        self.assertEqual(json.loads(requests[0].data)["model"], "openclaw/tari")
        self.assertTrue(requests[0].headers["Authorization"].startswith("Bearer "))

    def test_invalid_timezone_or_port_creates_no_state(self):
        self.args.port = 80
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertFalse((self.root / ".local").exists())


if __name__ == "__main__":
    unittest.main()
