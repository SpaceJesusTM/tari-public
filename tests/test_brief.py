from datetime import datetime, timezone
import contextlib
import importlib.machinery
import importlib.util
import io
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
        self.assertEqual(result["sources"]["planner"]["quality"], "complete")
        self.assertEqual(result["sources"]["planner"]["data"]["schedule"], [])
        self.assertEqual(result["sources"]["gmail"]["status"], "not_configured")
        self.assertEqual(result["sources"]["calendar"]["status"], "not_configured")
        self.assertIn("report", result["sources"]["domains"]["files"][0]["text"])
        self.assertFalse((self.root / "planner").exists())
        self.assertTrue(result["read_only"])

    def test_memory_is_omitted_by_default_and_requires_explicit_private_opt_in(self):
        (self.root / "MEMORY.md").write_text("Fixture private memory; never a scheduled briefing source.")
        with patch.object(brief, "note", wraps=brief.note) as note:
            result = brief.collect(now=self.now)
        self.assertEqual(result["sources"]["memory"]["status"], "not_requested")
        self.assertFalse(any(call.args[0].name == "MEMORY.md" for call in note.call_args_list))
        self.assertNotIn("Fixture private memory", json.dumps(result))
        result = brief.collect(now=self.now, private_memory=True)
        self.assertEqual(result["sources"]["memory"]["status"], "available")
        self.assertIn("Fixture private memory", result["sources"]["memory"]["text"])

    def test_private_memory_cli_flag_is_explicit(self):
        with patch("sys.argv", ["tari-brief-context", "tomorrow", "--private-memory"]), \
                patch.object(brief, "collect", return_value={"read_only": True}) as collect, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(brief.main(), 0)
        collect.assert_called_once_with("tomorrow", False, private_memory=True)

    def test_actual_partially_parsed_planner_preserves_sanitized_warnings(self):
        day = self.root / "planner/days/2026-10-21.md"
        day.parent.mkdir(parents=True)
        day.write_text("# Day Plan — 2026-10-21\n## Schedule\n"
                       "- 13:00-14:00 | planner | Fictional draft <!-- b1 -->\n"
                       "PRIVATE UNPARSEABLE SCHEDULE NOTE\n")
        checklist = self.root / "planner/checklist.md"
        checklist.write_text("# Planner Checklist\n## Work\n"
                             "- [ ] Fictional report <!-- t1 -->\nPRIVATE UNPARSEABLE CHECKLIST NOTE\n")
        before = (day.read_bytes(), checklist.read_bytes())
        result = brief.collect(now=self.now)
        planner = result["sources"]["planner"]
        self.assertEqual(planner["status"], "partial")
        self.assertEqual(planner["quality"], "malformed")
        codes = {warning["code"] for warning in planner["warnings"]}
        self.assertIn("PLANNER_SCHEDULE_MALFORMED", codes)
        self.assertIn("PLANNER_CHECKLIST_MALFORMED", codes)
        self.assertEqual(len(planner["data"]["schedule"]), 1)
        self.assertEqual(len(planner["data"]["checklist"][0]["tasks"]), 1)
        self.assertNotIn("PRIVATE UNPARSEABLE", json.dumps(result))
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual((day.read_bytes(), checklist.read_bytes()), before)

    def test_missing_planner_helper_is_unavailable_rather_than_empty(self):
        (self.root / "bin/day-planner").unlink()
        source = brief.collect(now=self.now)["sources"]["planner"]
        self.assertEqual(source["status"], "unavailable")
        self.assertEqual(source["quality"], "unavailable")
        self.assertNotIn("data", source)

    def test_malformed_helper_json_and_shape_are_distinguished_from_unavailability(self):
        for payload in ["{invalid", "[]", '{"schedule": {}, "checklist": []}',
                        '{"schedule": ["invalid"], "checklist": []}',
                        '{"schedule": [], "checklist": [], "data_status": []}',
                        '{"schedule": [], "checklist": [], "complete": "false"}']:
            with self.subTest(payload=payload), patch.object(brief.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, payload, "PRIVATE DIAGNOSTIC")):
                source = brief.helper(self.root, "day-planner", [])
                self.assertEqual(source["status"], "unavailable")
                self.assertEqual(source["quality"], "malformed")
                self.assertEqual(source["warnings"][0]["code"], "SOURCE_MALFORMED")
                self.assertNotIn("PRIVATE", json.dumps(source))

    def test_successful_legacy_helper_stderr_becomes_generic_partial_warning(self):
        result = subprocess.CompletedProcess([], 0, '{"schedule": [], "checklist": []}', "PRIVATE RAW PATH OR NOTE")
        with patch.object(brief.subprocess, "run", return_value=result):
            source = brief.helper(self.root, "day-planner", [])
        self.assertEqual(source["status"], "partial")
        self.assertEqual(source["quality"], "incomplete")
        self.assertEqual(source["warnings"][0]["code"], "SOURCE_DIAGNOSTIC_PRESENT")
        self.assertNotIn("PRIVATE", json.dumps(source))

    def test_structured_warning_messages_are_sanitized_and_bounded(self):
        payload = {"schedule": [], "checklist": [], "complete": False, "data_status": "incomplete",
                   "warnings": ["PRIVATE RAW WARNING"],
                   "warning_details": [{"code": "CALENDAR_UNAVAILABLE", "message": "PRIVATE RAW DIAGNOSTIC"}] * 100,
                   "private_diagnostic": "PRIVATE RAW EXTRA FIELD"}
        with patch.object(brief.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")):
            source = brief.helper(self.root, "day-planner", [])
        self.assertEqual(source["status"], "partial")
        self.assertEqual(source["quality"], "incomplete")
        self.assertEqual(len(source["warnings"]), 32)
        self.assertNotIn("PRIVATE", json.dumps(source))
        with patch.object(brief.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "x" * (brief.MAX_OUTPUT + 1), "")):
            source = brief.helper(self.root, "day-planner", [])
        self.assertEqual(source["warnings"][0]["code"], "SOURCE_OUTPUT_LIMIT")

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
