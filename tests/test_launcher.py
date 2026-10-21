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
        (self.root / "workspace/skills/example").mkdir(parents=True)
        (self.root / "workspace/skills/example/SKILL.md").write_text("Example skill v1\n")
        (self.root / "workspace/AGENTS.md").write_text("Distributed agent instructions\n")
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

    def test_workspace_update_refreshes_code_skills_and_preserves_personal_files(self):
        self.initialize()
        runtime = self.root / ".local/state/workspace"
        protected = {
            "USER.md": "User's own notes\n", "IDENTITY.md": "Custom identity\n",
            "MEMORY.md": "Remembered facts\n", "AGENTS.md": "Custom instructions\n",
            "domains/work.md": "Private work state\n", "planner/checklist.md": "Private tasks\n",
            "bin/user-tool": "User's own executable\n", "skills/custom/SKILL.md": "Custom skill\n",
        }
        for name, data in protected.items():
            path = runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        (self.root / "workspace/bin/tool").write_text("#!/bin/sh\nexit 1\n")
        (self.root / "workspace/skills/example/SKILL.md").write_text("Example skill v2\n")
        env_before = (self.root / ".local/runtime.env").read_bytes()
        with patch("builtins.print"):
            self.assertEqual(cli.workspace_update(), 0)
        self.assertEqual((runtime / "bin/tool").read_text(), "#!/bin/sh\nexit 1\n")
        self.assertEqual((runtime / "skills/example/SKILL.md").read_text(), "Example skill v2\n")
        self.assertEqual((runtime / "bin/tool").stat().st_mode & 0o777, 0o700)
        for name, data in protected.items():
            self.assertEqual((runtime / name).read_text(), data)
        self.assertEqual((self.root / ".local/runtime.env").read_bytes(), env_before)
        with patch("builtins.print") as output:
            cli.workspace_update()
        self.assertIn("Updated 0", output.call_args_list[0].args[0])

    def test_workspace_update_instructions_require_explicit_flag(self):
        self.initialize()
        runtime = self.root / ".local/state/workspace"
        (runtime / "AGENTS.md").write_text("Custom instructions")
        with patch("builtins.print"):
            cli.workspace_update(instructions=True)
        self.assertEqual((runtime / "AGENTS.md").read_text(), "Distributed agent instructions\n")

    def test_workspace_update_rejects_symlink_before_any_write(self):
        self.initialize()
        runtime = self.root / ".local/state/workspace"
        before = (runtime / "bin/tool").read_bytes()
        (self.root / "workspace/bin/tool").write_text("New source")
        (runtime / "skills/example/SKILL.md").unlink()
        (runtime / "skills/example/SKILL.md").symlink_to(runtime / "USER.md")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            cli.workspace_update()
        self.assertEqual((runtime / "bin/tool").read_bytes(), before)

    def test_dailygrad_parser_registration_and_dispatch(self):
        self.initialize()
        (self.root / "scripts").mkdir()
        (self.root / "scripts/dailygrad_setup.py").write_text(
            "def register_parser(sub):\n    sub.add_parser('dailygrad')\n"
            "def dispatch(args, cli):\n    assert cli.LOCAL.name == '.local'\n    return 7\n")
        with patch("sys.argv", ["tari", "dailygrad"]):
            self.assertEqual(cli.main(), 7)

    def test_compose_adds_only_checkout_local_dailygrad_overlay(self):
        self.initialize()
        (self.root / "scripts").mkdir()
        (self.root / "scripts/dailygrad_setup.py").write_text(
            "def compose_files(cli):\n    return [cli.LOCAL / 'dailygrad/compose.json']\n")
        with patch.object(cli.subprocess, "call", return_value=0) as run:
            cli.compose("ps")
        command = run.call_args.args[0]
        overlays = [command[index + 1] for index, value in enumerate(command) if value == "-f"]
        self.assertEqual(overlays, [str(self.root / "compose.yaml"), str(self.root / ".local/dailygrad/compose.json")])
        (self.root / "scripts/dailygrad_setup.py").write_text(
            "def compose_files(cli):\n    return [cli.ROOT.parent / 'outside.json']\n")
        with self.assertRaisesRegex(ValueError, "inside this checkout"):
            cli.compose_invocation("ps")

    def test_recreate_running_does_not_start_stopped_installation(self):
        with patch.object(cli, "running_service", return_value=False), patch.object(cli, "compose") as compose:
            self.assertIsNone(cli.recreate_running())
        compose.assert_not_called()
        with patch.object(cli, "running_service", return_value=True), patch.object(cli, "compose", return_value=0) as compose:
            self.assertEqual(cli.recreate_running(), 0)
        compose.assert_called_once_with("up", "-d", "--no-build", "--force-recreate", "tari")

    def test_stop_removes_only_this_compose_projects_orphan_services(self):
        with patch("sys.argv", ["tari", "stop"]), patch.object(cli, "compose", return_value=0) as compose:
            self.assertEqual(cli.main(), 0)
        compose.assert_called_once_with("down", "--remove-orphans", "--timeout", "360")


if __name__ == "__main__":
    unittest.main()
