"""Owner setup tests use only repository-local fixtures and mocked Docker calls."""

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = load_module("dailygrad_setup_test_cli", REPO / "scripts/tari_cli.py")
setup = load_module("dailygrad_setup_under_test", REPO / "scripts/dailygrad_setup.py")


class DailyGradSetupTests(unittest.TestCase):
    def setUp(self):
        base = REPO / ".test-tmp"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "installation"
        (self.root / "config").mkdir(parents=True)
        (self.root / "config/openclaw.json").write_bytes((REPO / "config/openclaw.json").read_bytes())
        (self.root / "workspace").mkdir()
        (self.root / "workspace/USER.md").write_text("Fixture user notes\n")
        (self.root / "plugins/tari-dailygrad").mkdir(parents=True)
        self.local = self.root / ".local"
        self.settings = patch.multiple(cli, ROOT=self.root, LOCAL=self.local)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.initialize(argparse.Namespace(timezone="Europe/Paris", port=18899, name="Fixture", model="openai/example"))
        env_path = self.local / "runtime.env"
        env_path.write_text(env_path.read_text().replace("OPENAI_API_KEY=", "OPENAI_API_KEY=fixture-provider-key"))
        self.external_data = self.base / "selected-output"
        self.external_data.mkdir()
        self.args = argparse.Namespace(data_dir=str(self.external_data), ollama_url="http://host.docker.internal:11434",
                                       ollama_model="fixture-model", **{name: False for name in setup.CAPABILITIES})
        self.compose = patch.object(cli, "compose", return_value=0)
        self.compose_mock = self.compose.start()
        self.addCleanup(self.compose.stop)
        self.recreate = patch.object(cli, "recreate_running", return_value=None)
        self.recreate_mock = self.recreate.start()
        self.addCleanup(self.recreate.stop)

    def configure(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            setup.configure(self.args, cli)
        return output.getvalue()

    def runtime(self):
        return json.loads((self.local / "state/openclaw.json").read_text())

    def overlay(self):
        return json.loads(Path(setup.compose_files(cli)[0]).read_text())

    def snapshot(self):
        return {str(path.relative_to(self.local)): path.read_bytes()
                for path in self.local.rglob("*") if path.is_file()}

    def test_disabled_optional_setup_adds_no_compose_overlay(self):
        self.assertEqual(setup.compose_files(cli), [])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = setup.dispatch(argparse.Namespace(dailygrad_command="status"), cli)
        self.assertEqual(result, 0)
        self.assertIn("disabled (optional)", output.getvalue())
        self.assertFalse((self.local / "dailygrad").exists())

    def test_configure_is_read_only_by_default_and_preserves_other_plugins(self):
        config_path = self.local / "state/openclaw.json"
        original = self.runtime()
        original["plugins"]["entries"]["fixture-other"] = {"enabled": True, "config": {"note": "keep"}}
        original["plugins"]["load"] = {"paths": ["/opt/fixture-other"]}
        original["plugins"]["allow"] = ["fixture-other", "memory-core"]
        config_path.write_text(json.dumps(original))
        output = self.configure()
        runtime = self.runtime()
        integration = runtime["plugins"]["entries"][setup.PLUGIN_ID]
        self.assertTrue(integration["enabled"])
        self.assertEqual(integration["config"]["timeZone"], "Europe/Paris")
        for capability in setup.CAPABILITIES.values():
            self.assertFalse(integration["config"][capability])
        self.assertEqual(runtime["plugins"]["entries"]["fixture-other"], original["plugins"]["entries"]["fixture-other"])
        self.assertEqual(runtime["agents"], original["agents"])
        self.assertEqual(runtime["gateway"], original["gateway"])
        self.assertEqual(runtime["plugins"]["load"]["paths"], ["/opt/fixture-other", setup.PLUGIN_PATH])
        self.assertIn("fixture-other", runtime["plugins"]["allow"])
        self.assertNotIn("fixture-provider-key", output)
        self.configure()
        self.assertEqual(self.runtime()["plugins"]["load"]["paths"].count(setup.PLUGIN_PATH), 1)
        self.assertEqual((self.local / "dailygrad/settings.json").stat().st_mode & 0o777, 0o600)
        self.compose_mock.assert_not_called()

    def test_external_directory_must_preexist_and_is_never_created(self):
        self.args.data_dir = str(self.base / "missing-external-output")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "must already exist"):
            self.configure()
        self.assertFalse(Path(self.args.data_dir).exists())
        self.assertEqual(self.snapshot(), before)

    def test_local_output_directory_may_be_created_explicitly(self):
        self.args.data_dir = str(self.local / "selected-output")
        self.configure()
        self.assertTrue(Path(self.args.data_dir).is_dir())
        self.assertEqual(setup.settings(cli)["dataDir"], self.args.data_dir)

    def test_symlink_selected_directory_is_refused_without_changes(self):
        link = self.base / "linked-output"
        link.symlink_to(self.external_data, target_is_directory=True)
        self.args.data_dir = str(link)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.configure()
        self.assertEqual(self.snapshot(), before)

    def test_data_path_interpolation_or_control_characters_are_refused(self):
        for suffix in ["$OPENAI_API_KEY", "bad\npath", "bad\x7fpath"]:
            with self.subTest(suffix=suffix):
                self.args.data_dir = str(self.base / suffix)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "interpolation or control"):
                    self.configure()
                self.assertEqual(self.snapshot(), before)

    def test_installation_roots_or_their_fixture_ancestors_cannot_be_mounted(self):
        for selected in [".", str(self.root), str(self.local), str(self.base)]:
            with self.subTest(selected=selected):
                self.args.data_dir = selected
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "[Ss]elect only.*output directory"):
                    self.configure()
                self.assertEqual(self.snapshot(), before)
                self.recreate_mock.assert_not_called()

    def test_checkout_or_config_markers_reject_broad_directory_selection(self):
        for marker in [".git", "pyproject.toml", "package.json", "openclaw.json", "runtime.env", ".env", "AGENTS.md"]:
            with self.subTest(marker=marker):
                marker_path = self.external_data / marker
                if marker == ".git":
                    marker_path.mkdir()
                else:
                    marker_path.write_text("Fixture marker; do not read contents")
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "[Ss]elect only.*output directory"):
                    self.configure()
                self.assertEqual(self.snapshot(), before)
                if marker_path.is_dir():
                    marker_path.rmdir()
                else:
                    marker_path.unlink()

    def test_each_runner_capability_requires_its_own_opt_in(self):
        for selected, selected_key in setup.CAPABILITIES.items():
            with self.subTest(capability=selected):
                for option in setup.CAPABILITIES:
                    setattr(self.args, option, option == selected)
                self.configure()
                overlay = self.overlay()
                command = overlay["services"]["dailygrad-runner"]["command"]
                config = self.runtime()["plugins"]["entries"][setup.PLUGIN_ID]["config"]
                for option, key in setup.CAPABILITIES.items():
                    self.assertEqual(config[key], key == selected_key)
                    self.assertEqual("--" + option.replace("_", "-") in command, option == selected)

    def test_overlay_separates_read_only_agent_and_runner_write_access_without_secret_env(self):
        self.configure()
        overlay = self.overlay()
        agent = overlay["services"]["tari"]
        runner = overlay["services"]["dailygrad-runner"]
        agent_data = next(mount for mount in agent["volumes"] if mount["target"] == "/mnt/dailygrad")
        runner_data = next(mount for mount in runner["volumes"] if mount["target"] == "/data")
        self.assertEqual(agent_data["source"], str(self.external_data))
        self.assertTrue(agent_data["read_only"])
        self.assertFalse(agent_data["bind"]["create_host_path"])
        self.assertEqual(runner_data["source"], str(self.external_data))
        self.assertFalse(runner_data.get("read_only", False))
        self.assertTrue(all(mount.get("read_only") for mount in agent["volumes"]))
        self.assertEqual(runner["profiles"], ["dailygrad-runner"])
        self.assertTrue(runner["read_only"])
        self.assertEqual(runner["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", runner["security_opt"])
        self.assertFalse(runner.get("privileged", False))
        self.assertEqual(runner["environment"], {"TZ": "Europe/Paris"})
        self.assertNotIn("env_file", runner)
        serialized = json.dumps(overlay)
        self.assertNotIn("fixture-provider-key", serialized)
        self.assertNotIn("OPENAI_API_KEY", serialized)
        self.assertNotIn("docker.sock", serialized)
        self.assertNotIn("/var/run", serialized)
        self.assertIsInstance(runner["command"], list)
        self.assertNotIn("shell", runner)
        self.assertEqual(runner["command"][:2], ["--repo", "/opt/dailygrad"])

    def test_disappeared_output_directory_does_not_break_core_overlay(self):
        self.configure()
        self.external_data.rmdir()
        overlay = self.overlay()
        self.assertFalse(any(mount["target"] == "/mnt/dailygrad" for mount in overlay["services"]["tari"]["volumes"]))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            setup.dispatch(argparse.Namespace(dailygrad_command="runner-start"), cli)
        self.compose_mock.assert_not_called()

    def test_broken_optional_settings_leave_core_code_only_overlay(self):
        self.configure()
        path = self.local / "dailygrad/settings.json"
        for content in ["{broken json", '{"enabled": "true"}', '{"enabled": true}']:
            with self.subTest(content=content):
                path.write_text(content)
                with contextlib.redirect_stderr(io.StringIO()) as error:
                    overlay = self.overlay()
                self.assertIn("core Tari", error.getvalue())
                self.assertEqual(set(overlay["services"]), {"tari"})
                self.assertEqual(len(overlay["services"]["tari"]["volumes"]), 1)
                self.assertEqual(overlay["services"]["tari"]["volumes"][0]["target"], setup.PLUGIN_PATH)

    def test_missing_settings_with_configured_plugin_leave_code_only_overlay(self):
        self.configure()
        (self.local / "dailygrad/settings.json").unlink()
        with contextlib.redirect_stderr(io.StringIO()):
            overlay = self.overlay()
        self.assertEqual(set(overlay["services"]), {"tari"})
        self.assertEqual(overlay["services"]["tari"]["volumes"][0]["target"], setup.PLUGIN_PATH)

    def test_symlinked_optional_settings_are_not_followed_and_core_can_start(self):
        self.configure()
        settings_path = self.local / "dailygrad/settings.json"
        settings_path.unlink()
        unrelated_fixture = self.base / "other-fixture.json"
        unrelated_fixture.write_text("Do not read through this symlink")
        settings_path.symlink_to(unrelated_fixture)
        with contextlib.redirect_stderr(io.StringIO()):
            overlay = self.overlay()
        self.assertEqual(set(overlay["services"]), {"tari"})
        self.assertEqual(unrelated_fixture.read_text(), "Do not read through this symlink")

    def test_status_reports_missing_data_honestly_without_secrets(self):
        self.configure()
        self.external_data.rmdir()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(setup.dispatch(argparse.Namespace(dailygrad_command="status"), cli), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["enabled"])
        self.assertFalse(result["data_available"])
        self.assertFalse(result["latest_file_present"])
        self.assertNotIn("fixture-provider-key", output.getvalue())

    def test_status_does_not_follow_latest_file_symlink(self):
        self.configure()
        target = self.base / "unrelated-fixture.json"
        target.write_text("unrelated content")
        (self.external_data / "latest.json").symlink_to(target)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            setup.dispatch(argparse.Namespace(dailygrad_command="status"), cli)
        self.assertFalse(json.loads(output.getvalue())["latest_file_present"])
        self.assertNotIn("unrelated content", output.getvalue())

    def test_runner_commands_target_only_the_optional_service(self):
        self.configure()
        for action, suffix in [("runner-start", ("up", "-d", "--build", "dailygrad-runner")),
                               ("runner-stop", ("stop", "dailygrad-runner")),
                               ("runner-logs", ("logs", "--tail", "100", "dailygrad-runner"))]:
            with self.subTest(action=action):
                self.assertEqual(setup.dispatch(argparse.Namespace(dailygrad_command=action), cli), 0)
                self.compose_mock.assert_called_with("--profile", "dailygrad-runner", *suffix)

    def test_disable_preserves_data_other_plugins_and_configuration(self):
        self.configure()
        data_file = self.external_data / "latest.json"
        data_file.write_text('{"fixture": true}')
        config_path = self.local / "state/openclaw.json"
        runtime = self.runtime()
        runtime["plugins"]["entries"]["fixture-other"] = {"enabled": True}
        runtime["plugins"]["load"]["paths"].append("/opt/fixture-other")
        runtime["plugins"]["allow"] = [setup.PLUGIN_ID, "fixture-other"]
        config_path.write_text(json.dumps(runtime))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.disable(cli), 0)
        updated = self.runtime()
        self.assertNotIn(setup.PLUGIN_ID, updated["plugins"]["entries"])
        self.assertEqual(updated["plugins"]["entries"]["fixture-other"], {"enabled": True})
        self.assertEqual(updated["plugins"]["load"]["paths"], ["/opt/fixture-other"])
        self.assertEqual(updated["plugins"]["allow"], ["fixture-other"])
        self.assertEqual(updated["agents"], runtime["agents"])
        self.assertEqual(data_file.read_text(), '{"fixture": true}')
        self.assertFalse(setup.settings(cli)["enabled"])
        self.assertEqual(setup.compose_files(cli), [])
        self.compose_mock.assert_called_once_with("--profile", "dailygrad-runner", "stop", "dailygrad-runner")

    def test_disable_can_disconnect_plugin_when_settings_are_missing_or_broken(self):
        for broken in [None, "{broken json", '{"enabled": false}']:
            with self.subTest(settings=broken):
                self.configure()
                path = self.local / "dailygrad/settings.json"
                if broken is None:
                    path.unlink()
                else:
                    path.write_text(broken)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(setup.disable(cli), 0)
                self.assertNotIn(setup.PLUGIN_ID, self.runtime()["plugins"]["entries"])
                self.assertNotIn(setup.PLUGIN_PATH, self.runtime()["plugins"]["load"]["paths"])
                retained = str(self.local / "dailygrad/compose.json")
                self.compose_mock.assert_called_with("-f", retained, "--profile", "dailygrad-runner", "stop", "dailygrad-runner")

    def test_disable_disconnects_saved_config_even_when_docker_is_unavailable(self):
        self.configure()
        self.compose_mock.side_effect = OSError("fixture Docker unavailable")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertNotEqual(setup.disable(cli), 0)
        self.assertNotIn(setup.PLUGIN_ID, self.runtime()["plugins"]["entries"])
        self.assertFalse(setup.settings(cli)["enabled"])
        self.assertIn("could not be verified", output.getvalue())

    def test_configure_and_disable_apply_only_to_running_tari_and_propagate_errors(self):
        self.recreate_mock.return_value = 0
        self.configure()
        self.recreate_mock.assert_called_once_with()
        self.recreate_mock.reset_mock()
        self.recreate_mock.return_value = 7
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(setup.dispatch(argparse.Namespace(dailygrad_command="configure", **vars(self.args)), cli), 7)
        self.assertIn("recreation failed", error.getvalue())
        self.assertTrue(setup.settings(cli)["enabled"])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(setup.disable(cli), 7)
        self.assertIn("recreation failed", error.getvalue())
        self.assertNotIn(setup.PLUGIN_ID, self.runtime()["plugins"]["entries"])

    def test_invalid_runtime_config_is_rejected_before_optional_setup_writes(self):
        (self.local / "state/openclaw.json").write_text('{"plugins": {"load": []}}')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "invalid types"):
            self.configure()
        self.assertEqual(self.snapshot(), before)

    def test_invalid_ollama_url_credentials_or_model_do_not_write(self):
        cases = [("ollama_url", "http://user:password@example.net"),
                 ("ollama_url", "file:///fixture/model"), ("ollama_url", "http://fixture\n:11434"),
                 ("ollama_model", "bad\nmodel")]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                original = getattr(self.args, field)
                setattr(self.args, field, value)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.configure()
                self.assertEqual(self.snapshot(), before)
                setattr(self.args, field, original)


if __name__ == "__main__":
    unittest.main()
