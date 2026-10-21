from datetime import datetime, timezone
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("brief", str(ROOT / "workspace/bin/tari-brief-context"))
spec = importlib.util.spec_from_loader(loader.name, loader)
brief = importlib.util.module_from_spec(spec)
loader.exec_module(brief)


class BriefTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "bin").mkdir()
        (self.root / "domains").mkdir()
        shutil.copyfile(ROOT / "workspace/bin/day-planner", self.root / "bin/day-planner")
        (self.root / "USER.md").write_text("Prefers a concise brief.\n")
        (self.root / "MEMORY.md").write_text("# Memory\n")
        (self.root / "domains/work.md").write_text("Fictional report due tomorrow; source: direct user update.\n")
        env = patch.dict(os.environ, {"TARI_WORKSPACE": str(self.root), "TARI_TIMEZONE": "Europe/Berlin", "TARI_GOOGLE_ACCOUNT": "", "GOG_ACCOUNT": ""})
        env.start()
        self.addCleanup(env.stop)
        self.now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)

    def test_actual_local_planner_and_notes_without_optional_integrations(self):
        result = brief.collect(now=self.now)
        self.assertEqual(result["date"], "2026-10-21")
        self.assertEqual(result["sources"]["planner"]["status"], "available")
        self.assertEqual(result["sources"]["planner"]["data"]["schedule"], [])
        self.assertEqual(result["sources"]["gmail"]["status"], "not_configured")
        self.assertEqual(result["sources"]["calendar"]["status"], "not_configured")
        self.assertIn("report", result["sources"]["domains"]["files"][0]["text"])
        self.assertFalse((self.root / "planner").exists())
        self.assertTrue(result["read_only"])

    def test_available_google_uses_offset_window_and_bounded_read_only_mail(self):
        def fake(command, **kwargs):
            if "day-planner" in command[1]:
                payload = {"schedule": [], "checklist": []}
            elif command[2] == "calendar":
                payload = {"events": [], "complete": True}
            else:
                payload = {"data": {"messages": [{"id": "fictional"}], "nextPageToken": "p2"}}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        with patch.dict(os.environ, {"TARI_GOOGLE_ACCOUNT": "owner@example.com"}), patch.object(brief.subprocess, "run", side_effect=fake) as run:
            result = brief.collect("tomorrow", now=self.now)
        self.assertEqual(result["date"], "2026-10-22")
        self.assertEqual(result["calendar_window"]["start"], "2026-10-22T00:00:00+02:00")
        self.assertTrue(result["sources"]["gmail"]["pagination_remaining"])
        self.assertFalse(result["sources"]["gmail"]["scope"]["complete_inbox_recap"])
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(calls[-1][-2:], ["--max", "20"])
        self.assertTrue(calls[-1][-3].startswith("after:"))
        self.assertFalse(any("apply" in call or "run" in call or "repair" in call for call in calls))

    def test_failed_google_is_unknown_not_empty(self):
        with patch.dict(os.environ, {"TARI_GOOGLE_ACCOUNT": "owner@example.com"}), patch.object(brief.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "PRIVATE UPSTREAM ERROR")):
            result = brief.collect(now=self.now)
        for source in ["calendar", "gmail"]:
            self.assertEqual(result["sources"][source]["status"], "unavailable")
            self.assertNotIn("data", result["sources"][source])
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_explicit_local_only_skips_configured_google(self):
        with patch.dict(os.environ, {"TARI_GOOGLE_ACCOUNT": "owner@example.com"}), patch.object(brief, "helper", return_value={"status": "available"}) as helper:
            result = brief.collect(local_only=True, now=self.now)
        helper.assert_called_once()
        self.assertEqual(result["sources"]["calendar"]["status"], "not_requested")

    def test_midnight_today_tomorrow_follows_user_not_utc(self):
        chosen, start, end = brief.window("today", ZoneInfo("America/Los_Angeles"), datetime(2026, 10, 21, 1, tzinfo=timezone.utc))
        self.assertEqual(chosen.isoformat(), "2026-10-20")
        tomorrow, _, _ = brief.window("tomorrow", ZoneInfo("America/Los_Angeles"), datetime(2026, 10, 21, 1, tzinfo=timezone.utc))
        self.assertEqual(tomorrow.isoformat(), "2026-10-21")

    def test_dst_calendar_windows_are_23_and_25_hours(self):
        for day, hours in [("2026-03-08", 23), ("2026-11-01", 25)]:
            _, start, end = brief.window(day, ZoneInfo("America/New_York"), self.now)
            self.assertEqual((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds(), hours * 3600)

    def test_invalid_timezone_has_clear_error(self):
        with patch.dict(os.environ, {"TARI_TIMEZONE": "Invalid/Zone"}):
            with self.assertRaisesRegex(ValueError, "Invalid TARI_TIMEZONE"):
                brief.collect(now=self.now)

    def test_symlink_and_bounded_domain_content(self):
        (self.root / "domains/linked.md").symlink_to(self.root / "USER.md")
        (self.root / "domains/large.md").write_text("x" * 5000)
        result = brief.collect(now=self.now)
        files = {item["source"]: item for item in result["sources"]["domains"]["files"]}
        self.assertEqual(files["domains/linked.md"]["status"], "unavailable")
        self.assertTrue(files["domains/large.md"]["truncated"])
        self.assertEqual(len(files["domains/large.md"]["text"]), 3000)


if __name__ == "__main__":
    unittest.main()
