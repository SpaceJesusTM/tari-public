"""Planner invariants, with isolated local state and mocked Calendar reads."""

import contextlib
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


REPO = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("tari_day_planner", str(REPO / "workspace/bin/day-planner"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
planner = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(planner)


def event(identifier="meeting", calendar="primary", start="2026-10-21T10:00:00+00:00",
          end="2026-10-21T11:00:00+00:00", **extra):
    return {"id": identifier, "calendar_id": calendar, "summary": identifier,
            "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


class PlannerTests(unittest.TestCase):
    def setUp(self):
        # Never use host /tmp, a real workspace, or a user's Google credentials.
        self.temp = tempfile.TemporaryDirectory(prefix=".planner-test-", dir=REPO / "tests")
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"TARI_GOOGLE_ACCOUNT": "alex@example.net",
                                          "TARI_PLANNER_NOW": "2026-10-20T09:02:00+00:00"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.settings = patch.multiple(planner, ROOT=self.temp.name, TZ_NAME="UTC")
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.events = []
        self.calendar = patch.object(planner.subprocess, "run", side_effect=self.calendar_read)
        self.mock_run = self.calendar.start()
        self.addCleanup(self.calendar.stop)

    def calendar_read(self, command, **kwargs):
        self.assertEqual(command[1:3], ["calendar", "list"])
        self.assertIn("--start", command)
        self.assertIn("--end", command)
        return subprocess.CompletedProcess(command, 0, json.dumps({"events": self.events, "complete": True}), "")

    def run_cli(self, *arguments):
        output, error = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["day-planner", *arguments]), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            try:
                planner.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        return code, output.getvalue(), error.getvalue()

    def day_path(self, date="2026-10-21"):
        return Path(self.temp.name) / "days" / (date + ".md")

    def test_add_and_idempotent_duplicate(self):
        self.assertEqual(self.run_cli("add", "tomorrow", "13:00-14:00", "Write draft")[0], 0)
        before = self.day_path().read_bytes()
        code, out, _ = self.run_cli("add", "tomorrow", "13:00-14:00", "Write draft")
        self.assertEqual(code, 0)
        self.assertIn("Already planned", out)
        self.assertEqual(self.day_path().read_bytes(), before)
        self.assertIn("<!-- b1 -->", before.decode())

    def test_calendar_overlap_refuses_without_writing(self):
        self.events = [event()]
        code, _, error = self.run_cli("add", "tomorrow", "10:30-11:30", "Draft")
        self.assertEqual(code, 4)
        self.assertIn("overlaps", error)
        self.assertFalse(self.day_path().exists())

    def test_confirmed_overlap_can_be_saved(self):
        self.events = [event()]
        code, out, _ = self.run_cli("add", "tomorrow", "10:30-11:30", "Draft", "--allow-overlap")
        self.assertEqual(code, 0)
        self.assertIn("Overlaps", out)
        self.assertTrue(self.day_path().exists())

    def test_local_overlap_refuses_and_preserves_existing_bytes(self):
        self.run_cli("add", "tomorrow", "13:00-14:00", "Draft")
        before = self.day_path().read_bytes()
        self.assertEqual(self.run_cli("add", "tomorrow", "13:30-15:00", "Review")[0], 4)
        self.assertEqual(self.day_path().read_bytes(), before)

    def test_calendar_failure_and_explicit_unchecked_write(self):
        self.mock_run.side_effect = OSError("mock unavailable")
        code, _, error = self.run_cli("add", "tomorrow", "13:00-14:00", "Draft")
        self.assertEqual(code, 3)
        self.assertIn("Nothing was changed", error)
        self.assertFalse(self.day_path().exists())
        code, output, _ = self.run_cli("add", "tomorrow", "13:00-14:00", "Draft", "--no-calendar")
        self.assertEqual(code, 0)
        self.assertIn("Calendar was not checked", output)

    def test_partial_or_malformed_calendar_cannot_allow_write(self):
        payloads = [{}, {"events": {}}, {"events": [], "complete": False},
                    {"events": [], "nextPageToken": "more"}, {"events": [None]}]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.mock_run.side_effect = None
                self.mock_run.return_value = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
                self.assertEqual(self.run_cli("add", "tomorrow", "13:00-14:00", "Draft")[0], 3)
                self.assertFalse(self.day_path().exists())

    def test_unreadable_counted_event_time_refuses_write(self):
        for bad in [event(start="bad"), event(start="2026-10-21T10:00:00"),
                    event(end="2026-10-21T09:00:00+00:00")]:
            with self.subTest(event=bad):
                self.events = [bad]
                self.assertEqual(self.run_cli("add", "tomorrow", "13:00-14:00", "Draft")[0], 3)
                self.assertFalse(self.day_path().exists())

    def test_unknown_calendar_identity_refuses_write(self):
        self.events = [event(calendar="")]
        self.assertEqual(self.run_cli("add", "tomorrow", "13:00-14:00", "Draft")[0], 3)

    def test_shared_calendar_only_counts_user_attendance(self):
        self.events = [event("Other person's appointment", calendar="shared@example.net")]
        code, out, _ = self.run_cli("show", "tomorrow", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["schedule"], [])
        self.events[0]["attendees"] = [{"self": True, "responseStatus": "accepted"}]
        self.assertEqual(self.run_cli("add", "tomorrow", "10:30-11:30", "Draft")[0], 4)

    def test_shared_pending_tentative_or_unknown_response_stays_context(self):
        for response in ["needsAction", "tentative", "unknown", None]:
            with self.subTest(response=response):
                self.events = [event(calendar="shared@example.net",
                                     attendees=[{"self": True, "responseStatus": response}])]
                code, out, _ = self.run_cli("show", "tomorrow", "--json")
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["schedule"], [])
                self.assertEqual(self.run_cli("add", "tomorrow", "10:30-11:30", "Draft",
                                              "--count", "meeting")[0], 4)
                self.assertFalse(self.day_path().exists())

    def test_primary_pending_or_tentative_response_still_blocks(self):
        for response in ["needsAction", "tentative", None]:
            with self.subTest(response=response):
                self.events = [event(attendees=[{"self": True, "responseStatus": response}])]
                self.assertEqual(self.run_cli("add", "tomorrow", "10:30-11:30", "Draft")[0], 4)
                self.assertFalse(self.day_path().exists())

    def test_unaccepted_shared_event_bad_time_does_not_invalidate_calendar(self):
        self.events = [event(calendar="shared@example.net", start="bad",
                             attendees=[{"self": True, "responseStatus": "tentative"}])]
        self.assertEqual(self.run_cli("add", "tomorrow", "10:30-11:30", "Draft")[0], 0)

    def test_declined_event_does_not_block_unless_explicitly_counted(self):
        self.events = [event(attendees=[{"self": True, "responseStatus": "declined"}])]
        code, out, _ = self.run_cli("show", "tomorrow", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["schedule"], [])
        self.assertEqual(self.run_cli("add", "tomorrow", "10:30-11:30", "Draft", "--count", "meeting")[0], 4)

    def test_primary_calendar_flag_counts_account_alias_as_own(self):
        self.events = [event(calendar="account-alias@example.net", calendar_primary=True)]
        self.assertEqual(self.run_cli("add", "tomorrow", "10:30-11:30", "Draft")[0], 4)

    def test_secondary_calendar_ownership_alone_is_not_attendance(self):
        self.events = [event(calendar="project-calendar@example.net", calendar_access_role="owner")]
        self.assertEqual(self.run_cli("add", "tomorrow", "10:30-11:30", "Draft")[0], 0)

    def test_all_day_and_transparent_events_are_visible_without_blocking(self):
        self.events = [event("free", transparency="transparent"),
                       {"id": "holiday", "calendar_id": "primary", "summary": "Holiday",
                        "start": {"date": "2026-10-21"}, "end": {"date": "2026-10-22"}}]
        code, out, _ = self.run_cli("show", "tomorrow", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["schedule"]), 2)
        self.assertEqual(self.run_cli("add", "tomorrow", "10:00-11:00", "Draft")[0], 0)

    def test_multiday_event_clips_to_local_day(self):
        self.events = [event(start="2026-10-20T23:00:00+00:00", end="2026-10-22T01:00:00+00:00")]
        code, out, _ = self.run_cli("show", "tomorrow", "--json")
        self.assertEqual(code, 0)
        item = json.loads(out)["schedule"][0]
        self.assertEqual((item["start"], item["end"]), (0, 1440))

    def test_calendar_duplicates_keep_counted_copy(self):
        self.events = [event("shared-copy", calendar="shared@example.net", iCalUID="same"),
                       event("own-copy", calendar="primary", iCalUID="same")]
        code, out, _ = self.run_cli("show", "tomorrow", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["schedule"]), 1)
        self.assertEqual(json.loads(out)["schedule"][0]["id"], "own-copy")

    def test_show_calendar_failure_keeps_local_state_and_warning(self):
        self.run_cli("add", "tomorrow", "13:00-14:00", "Draft")
        before = self.day_path().read_bytes()
        self.mock_run.side_effect = OSError("mock unavailable")
        code, out, _ = self.run_cli("show", "tomorrow", "--json")
        self.assertEqual(code, 0)
        value = json.loads(out)
        self.assertEqual(value["schedule"][0]["title"], "Draft")
        self.assertTrue(value["warnings"])
        self.assertEqual(self.day_path().read_bytes(), before)

    def test_place_finds_first_slot_after_calendar(self):
        self.events = [event()]
        self.assertEqual(self.run_cli("place", "tomorrow", "90m", "Draft", "--after", "10:00", "--before", "14:00")[0], 0)
        self.assertIn("11:00-12:30", self.day_path().read_text())

    def test_free_time_rounds_today_up_to_five_minutes(self):
        day = planner.parse_day("today")
        self.assertEqual(planner.free_windows(day, [], 8 * 60, 12 * 60), [(9 * 60 + 5, 12 * 60)])

    def test_move_resize_commute_and_remove(self):
        self.run_cli("add", "tomorrow", "13:00-14:00", "Draft")
        self.assertEqual(self.run_cli("move", "tomorrow", "Draft", "15:00")[0], 0)
        self.assertEqual(self.run_cli("resize", "tomorrow", "Draft", "90m", "--keep-end")[0], 0)
        self.assertIn("14:30-16:00", self.day_path().read_text())
        self.assertEqual(self.run_cli("commute-add", "tomorrow", "to", "Draft", "--duration", "30m")[0], 0)
        self.assertIn("14:00-14:30 | commute | Commute", self.day_path().read_text())
        self.assertEqual(self.run_cli("remove", "tomorrow", "@14:00")[0], 0)
        self.assertNotIn("| commute |", self.day_path().read_text())

    def test_cross_day_move_preserves_duration_and_does_not_copy_calendar(self):
        self.run_cli("add", "today", "13:00-14:00", "Draft")
        self.assertEqual(self.run_cli("move", "today", "Draft", "--to-day", "tomorrow")[0], 0)
        self.assertIn("13:00-14:00 | planner | Draft", self.day_path().read_text())
        self.assertNotIn("| planner |", self.day_path("2026-10-20").read_text())
        self.assertNotIn("calendar", self.day_path().read_text().lower())

    def test_checklist_status_completion_and_duplicate_prevention(self):
        self.assertEqual(self.run_cli("section-add", "Personal")[0], 0)
        self.assertEqual(self.run_cli("task-add", "Personal", "Book appointment", "--status", "priority")[0], 0)
        self.run_cli("task-add", "Personal", "Book appointment")
        self.assertEqual(self.run_cli("task-status", "t1", "in-progress")[0], 0)
        self.assertEqual(self.run_cli("task-complete", "t1")[0], 0)
        cl = planner.Checklist()
        self.assertEqual(len(cl.tasks()), 1)
        self.assertTrue(cl.tasks()[0]["done"])
        self.assertEqual(cl.tasks()[0]["status"], "In-progress")
        self.assertEqual(self.run_cli("task-reopen", "t1")[0], 0)
        self.assertFalse(planner.Checklist().tasks()[0]["done"])
        self.assertEqual(self.run_cli("section-remove", "Personal")[0], 2)

    def test_missing_section_requires_explicit_create(self):
        self.assertEqual(self.run_cli("task-add", "Personal", "Book appointment")[0], 2)
        self.assertFalse((Path(self.temp.name) / "checklist.md").exists())
        self.assertEqual(self.run_cli("task-add", "Personal", "Book appointment", "--create")[0], 0)

    def test_checklist_json_respects_section_status_and_completion(self):
        self.run_cli("task-add", "Personal", "Book appointment", "--create", "--status", "priority")
        self.run_cli("task-add", "Work", "Send draft", "--create", "--status", "start")
        self.run_cli("task-complete", "t1")
        code, out, _ = self.run_cli("checklist", "--json", "--section", "Personal", "--status", "priority")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [{"section": "Personal", "tasks": []}])
        _, out, _ = self.run_cli("checklist", "--json", "--section", "Personal", "--all")
        self.assertEqual(json.loads(out)[0]["tasks"][0]["id"], "t1")
        self.run_cli("task-add", "Personal", "Read book")
        _, out, _ = self.run_cli("checklist", "--json", "--section", "Personal", "--status", "none")
        self.assertEqual([t["text"] for t in json.loads(out)[0]["tasks"]], ["Read book"])

    def test_ambiguous_reference_refuses_to_guess(self):
        self.run_cli("add", "tomorrow", "13:00-14:00", "Draft one")
        self.run_cli("add", "tomorrow", "15:00-16:00", "Draft two")
        before = self.day_path().read_bytes()
        self.assertEqual(self.run_cli("remove", "tomorrow", "Draft")[0], 2)
        self.assertEqual(self.day_path().read_bytes(), before)

    def test_malformed_state_is_never_overwritten(self):
        self.day_path().parent.mkdir(parents=True)
        bad = "# Day Plan — 2026-10-21\nuser's unrecognized note\n"
        self.day_path().write_text(bad)
        self.assertEqual(self.run_cli("add", "tomorrow", "13:00-14:00", "Draft")[0], 5)
        self.assertEqual(self.day_path().read_text(), bad)

    def test_json_view_exposes_generic_parse_quality_without_raw_notes_or_paths(self):
        self.day_path().parent.mkdir(parents=True)
        self.day_path().write_text("# Day Plan — 2026-10-21\n## Schedule\n"
                                   "- 13:00-14:00 | planner | Fictional draft <!-- b1 -->\n"
                                   "PRIVATE MALFORMED NOTE\n")
        code, out, error = self.run_cli("show", "tomorrow", "--json", "--no-calendar")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["data_status"], "malformed")
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["schedule"]), 1)
        self.assertIn("PLANNER_SCHEDULE_MALFORMED", [item["code"] for item in result["warning_details"]])
        self.assertNotIn("PRIVATE MALFORMED", out + error)
        self.assertNotIn(self.temp.name, out + error)

    def test_json_local_scope_is_complete_but_failed_calendar_is_incomplete(self):
        _, out, _ = self.run_cli("show", "tomorrow", "--json", "--no-calendar")
        result = json.loads(out)
        self.assertTrue(result["complete"])
        self.assertEqual(result["data_status"], "complete")
        self.mock_run.side_effect = OSError("fixture unavailable")
        _, out, _ = self.run_cli("show", "tomorrow", "--json")
        result = json.loads(out)
        self.assertFalse(result["complete"])
        self.assertEqual(result["data_status"], "incomplete")
        self.assertEqual(result["warning_details"][0]["code"], "CALENDAR_UNAVAILABLE")

    def test_malformed_source_day_cannot_partially_move(self):
        self.run_cli("add", "today", "13:00-14:00", "Draft")
        with self.day_path("2026-10-20").open("a") as stream:
            stream.write("unrecognized note\n")
        self.assertEqual(self.run_cli("move", "today", "Draft", "--to-day", "tomorrow")[0], 5)
        self.assertFalse(self.day_path().exists())

    def test_ambiguous_times_invalid_zone_and_markdown_injection_refused(self):
        for value in ["1", "24:01", "25:00", "9:75", "0am"]:
            with self.subTest(time=value), self.assertRaises(planner.Fail):
                planner.parse_time(value)
        self.assertEqual(planner.parse_time("1pm"), 13 * 60)
        self.assertEqual(planner.parse_time("24:00", end=True), 1440)
        for title in ["bad <!-- b3 -->", "two\nlines", "bad | kind"]:
            with self.subTest(title=title), self.assertRaises(planner.Fail):
                planner.clean(title, "title", "|")
        with patch.object(planner, "TZ_NAME", "Not/A_Real_Zone"), self.assertRaises(planner.Fail):
            planner.tz()

    def test_timezone_controls_relative_date(self):
        with patch.object(planner, "TZ_NAME", "America/Los_Angeles"), patch.dict(os.environ, {"TARI_PLANNER_NOW": "2026-10-20T01:00:00+00:00"}):
            self.assertEqual(str(planner.parse_day("today")), "2026-10-19")
            self.assertEqual(str(planner.parse_day("tomorrow")), "2026-10-20")

    def test_missing_google_account_fails_before_process(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.run_cli("add", "2026-10-21", "13:00-14:00", "Draft")[0], 3)
        self.mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
