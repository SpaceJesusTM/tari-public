"""Installation timezone changes and local-day boundaries; no real state or Docker."""

import argparse
import contextlib
from datetime import datetime
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cli = load_module("timezone_tari_cli", ROOT / "scripts/tari_cli.py")
planner = load_module("timezone_day_planner", ROOT / "workspace/bin/day-planner")


class InstallationTimezoneTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".test-tmp"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "config/openclaw.json").write_bytes((ROOT / "config/openclaw.json").read_bytes())
        (self.root / "workspace").mkdir()
        (self.root / "workspace/USER.md").write_bytes((ROOT / "workspace/USER.md").read_bytes())
        self.local = self.root / ".local"
        self.settings = patch.multiple(cli, ROOT=self.root, LOCAL=self.local)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.running = patch.object(cli, "running_service", return_value=False)
        self.running_mock = self.running.start()
        self.addCleanup(self.running.stop)
        self.compose = patch.object(cli, "compose", return_value=0)
        self.compose_mock = self.compose.start()
        self.addCleanup(self.compose.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.initialize(argparse.Namespace(timezone="UTC", port=18899, name="Fixture", model="openai/example"))

    def set_zone(self, zone):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli.configure_timezone(zone)
        return result, output.getvalue()

    def snapshot(self):
        return {str(path.relative_to(self.local)): path.read_bytes()
                for path in self.local.rglob("*") if path.is_file()}

    def test_show_is_read_only_and_missing_setting_defaults_to_utc(self):
        env_path = self.local / "runtime.env"
        env_path.write_text("\n".join(line for line in env_path.read_text().splitlines()
                                      if not line.startswith("TARI_TIMEZONE=")) + "\n")
        before = self.snapshot()
        self.assertEqual(self.set_zone(None), (0, "UTC\n"))
        self.assertEqual(self.snapshot(), before)
        self.running_mock.assert_not_called()

    def test_set_syncs_config_managed_user_stanza_and_dailygrad_only(self):
        env_path = self.local / "runtime.env"
        fixture_secret = "OPENAI_API_KEY=fixture-secret-never-real"
        env_path.write_text(env_path.read_text().replace("OPENAI_API_KEY=", fixture_secret))
        before_env = env_path.read_text()
        config_path = self.local / "state/openclaw.json"
        config = json.loads(config_path.read_text())
        config["plugins"]["entries"]["tari-dailygrad"] = {
            "enabled": True, "config": {"timeZone": "UTC", "dataDir": "/mnt/dailygrad", "runWaitSeconds": 570}}
        config["gateway"]["fixtureNote"] = "Keep this custom config field"
        config_path.write_text(json.dumps(config))
        user_path = self.local / "state/workspace/USER.md"
        user_path.write_text(user_path.read_text().replace("<!-- tari:timezone:end -->", "User note inside marker.\n<!-- tari:timezone:end -->")
                             + "\nUser's own profile notes.\n")
        result, output = self.set_zone("Europe/Paris")
        self.assertEqual(result, 0)
        self.assertNotIn("fixture-secret", output)
        self.assertEqual(env_path.read_text(), before_env.replace("TARI_TIMEZONE=UTC", "TARI_TIMEZONE=Europe/Paris"))
        updated = json.loads(config_path.read_text())
        config["agents"]["defaults"]["userTimezone"] = "Europe/Paris"
        config["plugins"]["entries"]["tari-dailygrad"]["config"]["timeZone"] = "Europe/Paris"
        self.assertEqual(updated, config)
        self.assertIn("Prefer Europe/Paris", user_path.read_text())
        self.assertIn("User note inside marker.", user_path.read_text())
        self.assertIn("User's own profile notes.", user_path.read_text())
        self.compose_mock.assert_not_called()
        self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)

    def test_running_container_recreates_without_build_or_other_services(self):
        self.running_mock.return_value = True
        self.assertEqual(self.set_zone("Asia/Tokyo")[0], 0)
        self.compose_mock.assert_called_once_with("up", "-d", "--no-build", "--force-recreate", "tari")

    def test_unavailable_docker_saves_settings_without_starting_a_container(self):
        self.running_mock.return_value = None
        code, output = self.set_zone("Pacific/Auckland")
        self.assertEqual(code, 0)
        self.assertIn("Docker status unavailable", output)
        self.assertEqual(cli.read_env()["TARI_TIMEZONE"], "Pacific/Auckland")
        self.compose_mock.assert_not_called()

    def test_timezone_recreates_an_enabled_running_runner_without_requesting_a_job(self):
        config_path = self.local / "state/openclaw.json"
        config = json.loads(config_path.read_text())
        config["plugins"]["entries"]["tari-dailygrad"] = {
            "enabled": True, "config": {"enabled": True, "timeZone": "UTC"}}
        config_path.write_text(json.dumps(config))
        self.running_mock.side_effect = lambda service="tari": True
        self.assertEqual(self.set_zone("Asia/Tokyo")[0], 0)
        self.assertEqual([call.args for call in self.compose_mock.call_args_list], [
            ("up", "-d", "--no-build", "--force-recreate", "tari"),
            ("--profile", "dailygrad-runner", "up", "-d", "--no-build", "--force-recreate", "dailygrad-runner"),
        ])

    def test_timezone_leaves_stopped_runner_stopped(self):
        config_path = self.local / "state/openclaw.json"
        config = json.loads(config_path.read_text())
        config["plugins"]["entries"]["tari-dailygrad"] = {
            "enabled": True, "config": {"enabled": True, "timeZone": "UTC"}}
        config_path.write_text(json.dumps(config))
        self.running_mock.side_effect = lambda service="tari": service == "tari"
        self.assertEqual(self.set_zone("Europe/Paris")[0], 0)
        self.compose_mock.assert_called_once_with("up", "-d", "--no-build", "--force-recreate", "tari")

    def test_recreation_failure_reports_saved_settings_and_can_retry(self):
        self.running_mock.return_value = True
        self.compose_mock.return_value = 9
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(self.set_zone("Europe/London")[0], 9)
        self.assertIn("Saved timezone Europe/London", error.getvalue())
        self.assertEqual(cli.read_env()["TARI_TIMEZONE"], "Europe/London")
        self.compose_mock.return_value = 0
        self.assertEqual(self.set_zone("Europe/London")[0], 0)
        self.assertEqual(self.compose_mock.call_count, 2)

    def test_invalid_zone_has_no_writes_or_docker_calls(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "valid IANA"):
            self.set_zone("Invalid/Not_A_Zone")
        self.assertEqual(self.snapshot(), before)
        self.running_mock.assert_not_called()
        self.compose_mock.assert_not_called()

    def test_valid_set_can_repair_an_invalid_existing_setting(self):
        path = self.local / "runtime.env"
        path.write_text(path.read_text().replace("TARI_TIMEZONE=UTC", "TARI_TIMEZONE=Invalid/Zone"))
        self.assertEqual(self.set_zone("Europe/London")[0], 0)
        self.assertEqual(cli.read_env()["TARI_TIMEZONE"], "Europe/London")

    def test_invalid_config_or_user_marker_causes_no_partial_writes(self):
        for bad in ["config", "user"]:
            with self.subTest(bad=bad):
                config_path = self.local / "state/openclaw.json"
                user_path = self.local / "state/workspace/USER.md"
                original_config, original_user = config_path.read_bytes(), user_path.read_bytes()
                if bad == "config":
                    config_path.write_text('{"agents": []}')
                else:
                    user_path.write_text("<!-- tari:timezone:start -->\nUser's own notes\n")
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.set_zone("Europe/London")
                self.assertEqual(self.snapshot(), before)
                config_path.write_bytes(original_config)
                user_path.write_bytes(original_user)

    def test_legacy_marker_migration_and_repeat_set_are_idempotent(self):
        user_path = self.local / "state/workspace/USER.md"
        legacy = ("# User preferences\n\n<!-- observed: installation | status: active -->\n"
                  "Prefer UTC for local dates, scheduling, and daily summaries unless\n"
                  "the user requests another timezone.\n\nKeep my separate notes.\n")
        user_path.write_text(legacy)
        self.set_zone("America/Los_Angeles")
        before = self.snapshot()
        self.set_zone("America/Los_Angeles")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(user_path.read_text().count("<!-- tari:timezone:start -->"), 1)
        self.assertIn("Keep my separate notes.", user_path.read_text())

    def test_user_file_without_managed_stanza_is_preserved(self):
        user_path = self.local / "state/workspace/USER.md"
        user_path.write_text("Custom profile without an installation marker.\n")
        self.set_zone("Asia/Kolkata")
        self.assertTrue(user_path.read_text().startswith("Custom profile without an installation marker.\n"))
        self.assertIn("Prefer Asia/Kolkata", user_path.read_text())

    def test_symlink_user_file_is_refused_before_changes(self):
        user_path = self.local / "state/workspace/USER.md"
        destination = self.local / "other-note.md"
        destination.write_text("Do not follow this link")
        user_path.unlink()
        user_path.symlink_to(destination)
        env_before = (self.local / "runtime.env").read_bytes()
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.set_zone("Europe/London")
        self.assertEqual(destination.read_text(), "Do not follow this link")
        self.assertEqual((self.local / "runtime.env").read_bytes(), env_before)

    def test_cli_timezone_dispatch(self):
        with patch("sys.argv", ["tari", "timezone", "Europe/Berlin"]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(cli.read_env()["TARI_TIMEZONE"], "Europe/Berlin")


class LocalDayBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TARI_GOOGLE_ACCOUNT": "fixture@example.net"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def calendar_bounds(self, local_day, zone):
        response = subprocess.CompletedProcess([], 0, '{"events": [], "complete": true}', "")
        with patch.object(planner, "TZ_NAME", zone), patch.object(planner.subprocess, "run", return_value=response) as run:
            planner.read_calendar(datetime.fromisoformat(local_day).date())
        command = run.call_args.args[0]
        lo = datetime.fromisoformat(command[command.index("--start") + 1])
        hi = datetime.fromisoformat(command[command.index("--end") + 1])
        self.assertEqual((lo.hour, hi.hour), (0, 0))
        return lo, hi

    def test_spring_forward_local_day_spans_23_elapsed_hours(self):
        lo, hi = self.calendar_bounds("2026-03-08", "America/New_York")
        self.assertEqual((hi.timestamp() - lo.timestamp()) / 3600, 23)
        self.assertEqual(lo.isoformat(), "2026-03-08T00:00:00-05:00")
        self.assertEqual(hi.isoformat(), "2026-03-09T00:00:00-04:00")

    def test_fall_back_local_day_spans_25_elapsed_hours(self):
        lo, hi = self.calendar_bounds("2026-11-01", "America/New_York")
        self.assertEqual((hi.timestamp() - lo.timestamp()) / 3600, 25)
        self.assertEqual(lo.isoformat(), "2026-11-01T00:00:00-04:00")
        self.assertEqual(hi.isoformat(), "2026-11-02T00:00:00-05:00")

    def test_today_and_tomorrow_use_local_date_near_utc_midnight(self):
        cases = [
            ("America/New_York", "2026-03-09T03:45:00+00:00", "2026-03-08", "2026-03-09"),
            ("America/Los_Angeles", "2026-10-21T01:00:00+00:00", "2026-10-20", "2026-10-21"),
            ("Asia/Tokyo", "2026-10-20T23:30:00+00:00", "2026-10-21", "2026-10-22"),
        ]
        for zone, instant, today, tomorrow in cases:
            with self.subTest(zone=zone), patch.object(planner, "TZ_NAME", zone), patch.dict(os.environ, {"TARI_PLANNER_NOW": instant}):
                self.assertEqual(planner.parse_day("today").isoformat(), today)
                self.assertEqual(planner.parse_day("tomorrow").isoformat(), tomorrow)


if __name__ == "__main__":
    unittest.main()
