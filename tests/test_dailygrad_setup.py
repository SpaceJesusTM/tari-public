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
        self.running = patch.object(cli, "running_service", return_value=False)
        self.running_mock = self.running.start()
        self.addCleanup(self.running.stop)

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
        self.assertEqual(updated["plugins"]["load"]["paths"],
                         ["/opt/tari/plugins/tari-workflows", "/opt/fixture-other"])
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

    def simulate_running_runner(self):
        """Track effective process policy separately from files, as a real runner does."""
        saved = setup.settings(cli)
        state = {"running": True, "capabilities": dict(saved["capabilities"]), "events": []}
        self.running_mock.side_effect = lambda service: state["running"] if service == "dailygrad-runner" else False

        def lifecycle(*args):
            overlay = json.loads((self.local / "dailygrad/compose.json").read_text())
            saved = setup.settings(cli)
            event = {"args": args, "capabilities": dict(saved["capabilities"]),
                     "overlay": overlay, "model_config": (self.local / "dailygrad/dailygrad.toml").read_text()}
            state["events"].append(event)
            if "stop" in args:
                state["running"] = False
            elif "up" in args:
                state.update(running=True, capabilities=dict(saved["capabilities"]),
                             overlay=overlay, model_config=event["model_config"])
            else:
                self.fail("Unexpected service operation during configure")
            return 0

        self.compose_mock.side_effect = lifecycle
        return state

    def test_running_runner_permission_expansion_recreates_only_that_service(self):
        self.configure()
        state = self.simulate_running_runner()
        self.args.allow_run = True
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.configure(self.args, cli), 0)
        self.assertTrue(state["running"])
        self.assertTrue(state["capabilities"]["allowRun"])
        self.assertFalse(state["events"][0]["capabilities"]["allowRun"])
        self.assertIn("stop", state["events"][0]["args"])
        self.assertTrue(state["events"][1]["capabilities"]["allowRun"])
        self.assertIn("--force-recreate", state["events"][1]["args"])
        self.assertIn("--no-build", state["events"][1]["args"])
        self.assertTrue(all(event["args"][-1] == "dailygrad-runner" for event in state["events"]))
        self.assertFalse(any("run" in event["args"] for event in state["events"]))

    def test_running_runner_revocation_quiesces_old_policy_before_replacing_files(self):
        for option in setup.CAPABILITIES:
            setattr(self.args, option, True)
        self.configure()
        state = self.simulate_running_runner()
        for option in setup.CAPABILITIES:
            setattr(self.args, option, False)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.configure(self.args, cli), 0)
        self.assertTrue(all(state["events"][0]["capabilities"].values()))
        self.assertFalse(any(state["events"][1]["capabilities"].values()))
        self.assertFalse(any(state["capabilities"].values()))
        self.assertTrue(state["running"])

    def test_settings_data_model_and_timezone_use_new_mounts_after_quiescence(self):
        self.configure()
        state = self.simulate_running_runner()
        replacement = self.base / "replacement-output"
        replacement.mkdir()
        self.args.data_dir = str(replacement)
        self.args.ollama_url = "http://fixture-model-server:11434"
        self.args.ollama_model = "replacement-model"
        env = self.local / "runtime.env"
        env.write_text(env.read_text().replace("Europe/Paris", "Asia/Tokyo"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.configure(self.args, cli), 0)
        old_runner = state["events"][0]["overlay"]["services"]["dailygrad-runner"]
        new_runner = state["overlay"]["services"]["dailygrad-runner"]
        self.assertEqual(next(m["source"] for m in old_runner["volumes"] if m["target"] == "/data"), str(self.external_data))
        self.assertEqual(next(m["source"] for m in new_runner["volumes"] if m["target"] == "/data"), str(replacement))
        self.assertEqual(new_runner["command"][new_runner["command"].index("--timezone") + 1], "Asia/Tokyo")
        self.assertIn('model = "replacement-model"', state["model_config"])
        self.assertIn('url = "http://fixture-model-server:11434"', state["model_config"])
        self.assertIn("--force-recreate", state["events"][1]["args"])
        self.assertEqual(self.runtime()["plugins"]["entries"][setup.PLUGIN_ID]["config"]["timeZone"], "Asia/Tokyo")

    def test_stopped_runner_stays_stopped_during_reconfiguration(self):
        self.configure()
        self.args.allow_run = True
        self.args.ollama_model = "changed-while-stopped"
        output = self.configure()
        self.compose_mock.assert_not_called()
        self.assertIn("No optional runner was started", output)
        self.assertTrue(setup.settings(cli)["capabilities"]["allowRun"])

    def test_first_time_offline_configuration_saves_without_starting_any_runner(self):
        self.running_mock.return_value = None
        output = self.configure()
        self.assertTrue(setup.settings(cli)["enabled"])
        self.compose_mock.assert_not_called()
        self.assertIn("No optional runner was started", output)

    def test_unknown_previous_runner_state_requires_confirmed_stop_before_applying(self):
        self.args.allow_run = True
        self.configure()
        before = self.snapshot()
        self.args.allow_run = False
        self.running_mock.return_value = None
        self.recreate_mock.reset_mock()
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(setup.configure(self.args, cli), 1)
        self.assertEqual(self.snapshot(), before)
        self.assertIn("could not be confirmed stopped", error.getvalue())
        self.assertIn("permissions may still be active", error.getvalue())
        self.recreate_mock.assert_not_called()
        self.assertNotIn("up", self.compose_mock.call_args.args)

    def test_unknown_runner_that_can_be_stopped_is_not_restarted_automatically(self):
        self.configure()
        self.running_mock.side_effect = [None, False]
        self.args.allow_run = True
        output = self.configure()
        self.assertEqual(self.compose_mock.call_count, 1)
        self.assertIn("stop", self.compose_mock.call_args.args)
        self.assertIn("No optional runner was started", output)

    def test_successful_stop_command_without_stopped_state_cannot_revoke_permissions(self):
        self.args.allow_run = True
        self.configure()
        before = self.snapshot()
        self.args.allow_run = False
        self.running_mock.return_value = True
        self.recreate_mock.reset_mock()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.configure(self.args, cli), 1)
        self.assertEqual(self.snapshot(), before)
        self.recreate_mock.assert_not_called()

    def test_failed_recreation_stops_partially_started_runner_and_reports_failure(self):
        self.args.allow_run = True
        self.configure()
        state = self.simulate_running_runner()
        successful = self.compose_mock.side_effect
        self.args.allow_run = False

        def fail_recreation(*args):
            result = successful(*args)
            return 8 if "up" in args else result

        self.compose_mock.side_effect = fail_recreation
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(setup.configure(self.args, cli), 8)
        self.assertEqual(["up" if "up" in e["args"] else "stop" for e in state["events"]], ["stop", "up", "stop"])
        self.assertFalse(state["running"])
        self.assertFalse(setup.settings(cli)["capabilities"]["allowRun"])
        self.assertIn("confirmed stopped", error.getvalue())

    def test_unverifiable_recreated_runner_is_stopped_instead_of_claiming_success(self):
        self.configure()
        state = self.simulate_running_runner()
        successful = self.compose_mock.side_effect

        def unknown_after_start(*args):
            result = successful(*args)
            if "up" in args:
                state["running"] = None
            return result

        self.compose_mock.side_effect = unknown_after_start
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(setup.configure(self.args, cli), 1)
        self.assertFalse(state["running"])
        self.assertIn("confirmed stopped", error.getvalue())

    def test_failed_recreation_with_unverifiable_shutdown_never_claims_revocation(self):
        self.args.allow_run = True
        self.configure()
        state = self.simulate_running_runner()
        successful = self.compose_mock.side_effect
        self.args.allow_run = False

        def unavailable_during_recreation(*args):
            result = successful(*args)
            if "up" in args or len(state["events"]) > 2:
                state["running"] = None
                return 8
            return result

        self.compose_mock.side_effect = unavailable_during_recreation
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(setup.configure(self.args, cli), 8)
        self.assertIsNone(state["running"])
        self.assertIn("shutdown could not be verified", error.getvalue())
        self.assertNotIn("The optional runner is confirmed stopped", error.getvalue())
        self.assertIn("do not rely on permission revocation", error.getvalue())
        self.assertNotIn("DailyGrad configured", output.getvalue())
        self.assertFalse(setup.settings(cli)["capabilities"]["allowRun"])

    def test_failed_core_apply_leaves_previous_runner_stopped(self):
        self.configure()
        state = self.simulate_running_runner()
        self.recreate_mock.return_value = 7
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(setup.configure(self.args, cli), 7)
        self.assertFalse(state["running"])
        self.assertEqual(len(state["events"]), 1)
        self.assertIn("not restarted", error.getvalue())

    def test_failed_save_leaves_previous_runner_stopped_and_does_not_recreate_core(self):
        self.configure()
        state = self.simulate_running_runner()
        self.recreate_mock.reset_mock()
        with patch.object(setup, "write_json", side_effect=OSError("fixture disk failure")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.configure(self.args, cli), 1)
        self.assertFalse(state["running"])
        self.assertEqual(len(state["events"]), 1)
        self.recreate_mock.assert_not_called()

    def test_concurrent_configuration_is_rejected_before_service_changes(self):
        with setup.configuration_lock(cli):
            with self.assertRaisesRegex(ValueError, "already being changed"):
                self.configure()
        self.compose_mock.assert_not_called()
        self.recreate_mock.assert_not_called()

    def test_optional_lock_unavailable_refuses_configure_without_changing_core(self):
        before = self.snapshot()
        with patch.object(setup, "fcntl", None):
            with self.assertRaisesRegex(ValueError, "Unix file locking"):
                self.configure()
        self.assertEqual(self.snapshot(), before)
        self.compose_mock.assert_not_called()
        self.recreate_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
