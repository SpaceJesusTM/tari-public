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

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("tari_google", str(ROOT / "workspace/bin/tari-google"))
spec = importlib.util.spec_from_loader(loader.name, loader)
google = importlib.util.module_from_spec(spec)
loader.exec_module(google)
OWN = {"id": "owner@example.com", "accessRole": "owner", "primary": True}
EVENT = {"id": "event1", "etag": '"revision1"', "summary": "Meeting",
         "start": {"dateTime": "2026-10-20T09:00:00-04:00"},
         "end": {"dateTime": "2026-10-20T10:00:00-04:00"}}


class GoogleTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".test-tmp")
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"TARI_GOOGLE_ACCOUNT": "owner@example.com",
                              "TARI_WORKSPACE": self.temp.name, "TARI_TIMEZONE": "America/Toronto",
                              "TARI_CALENDAR_WRITES": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_mail_read_is_scoped_no_shell_and_keeps_pagination(self):
        response = subprocess.CompletedProcess([], 0, json.dumps({"messages": [], "nextPageToken": "page2"}), "")
        with patch.object(google.subprocess, "run", return_value=response) as run:
            result = google.run_gog(["gmail", "messages", "search", "label:inbox"], "gmail.messages.search")
        cmd = run.call_args.args[0]
        self.assertIn("--readonly", cmd)
        self.assertIn("--gmail-no-send", cmd)
        self.assertIn("--enable-commands-exact", cmd)
        self.assertEqual(cmd[cmd.index("--client") + 1], "tari-read")
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertEqual(result["nextPageToken"], "page2")

    def test_mail_mutations_and_flag_injection_rejected_before_subprocess(self):
        with patch.object(google.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            for argv in (["gmail", "send"], ["gmail", "read", "--force"], ["auth", "add"]):
                with self.assertRaises(SystemExit):
                    google.main(argv)
            self.assertEqual(google.main(["gmail", "search", "--", "--account=other@example.com"]), 1)
        run.assert_not_called()

    def test_lists_shared_calendar_without_claiming_ownership(self):
        shared = {"id": "shared@example.com", "accessRole": "reader"}
        row = {**EVENT, "iCalUID": "shared-event@example.test", "attendees": [{"email": "owner@example.com", "self": True, "responseStatus": "accepted"}],
               "transparency": "opaque"}
        with patch.object(google, "run_gog", side_effect=[{"calendars": [OWN, shared]}, {"events": []}, {"events": [row]}]):
            result = google.list_events("2026-10-20T00:00:00-04:00", "2026-10-21T00:00:00-04:00")
        self.assertTrue(result["complete"])
        self.assertEqual(result["events"][0]["calendar_id"], shared["id"])
        self.assertFalse(result["events"][0]["calendar_primary"])
        self.assertTrue(result["events"][0]["attendees"][0]["self"])
        self.assertEqual(result["events"][0]["iCalUID"], "shared-event@example.test")

    def test_pagination_fetches_every_page_and_fails_on_repeated_cursor(self):
        with patch.object(google, "run_gog", side_effect=[{"events": [EVENT], "nextPageToken": "p2"}, {"events": []}]) as run:
            self.assertEqual(len(google.collection(["calendar", "events", "primary"], "calendar.events", "events")), 1)
            self.assertEqual(run.call_args.args[0][-2:], ["--page", "p2"])
        with patch.object(google, "run_gog", return_value={"events": [], "nextPageToken": "repeated"}):
            with self.assertRaises(google.GoogleError):
                google.collection(["calendar", "events"], "calendar.events", "events")

    def test_missing_calendar_fails_closed(self):
        with patch.object(google, "run_gog", return_value={"calendars": []}):
            with self.assertRaises(google.GoogleError):
                google.list_events("2026-10-20T00:00:00+00:00", "2026-10-21T00:00:00+00:00")

    def test_error_never_discloses_upstream_tokens(self):
        result = subprocess.CompletedProcess([], 1, "", "token=SECRET PRIVATE CONTENT")
        with patch.object(google.subprocess, "run", return_value=result):
            with self.assertRaises(google.GoogleError) as error:
                google.run_gog(["gmail", "get", "abc"], "gmail.get")
        self.assertNotIn("SECRET", str(error.exception))

    def test_calendar_write_default_denied(self):
        with patch.object(google, "run_gog") as run:
            with self.assertRaises(google.GoogleError):
                google.apply("a" * 32, "a" * 32)
        run.assert_not_called()

    def test_secondary_calendar_proposals_are_rejected_before_event_or_write_access(self):
        secondary = {"id": "secondary@example.test", "accessRole": "owner", "primary": False}
        for operation in ["create", "update", "delete"]:
            with self.subTest(operation=operation):
                argv = ["calendar", "propose", operation, secondary["id"]]
                if operation != "create":
                    argv.append(EVENT["id"])
                if operation != "delete":
                    argv += ["--summary", "Reviewed meeting", "--start", "2026-10-20T11:00:00-04:00",
                             "--end", "2026-10-20T12:00:00-04:00"]
                with patch.object(google, "calendars", return_value=[OWN, secondary]), \
                        patch.object(google, "event") as event_read, patch.object(google, "run_gog") as run:
                    with self.assertRaisesRegex(google.GoogleError, "primary calendar"):
                        google.propose(google.parser().parse_args(argv))
                event_read.assert_not_called()
                run.assert_not_called()
        self.assertFalse((Path(self.temp.name) / "state/google-proposals").exists())

    def test_primary_canonical_id_is_supported_and_ambiguous_primary_fails_closed(self):
        args = google.parser().parse_args(["calendar", "propose", "create", OWN["id"], "--summary", "Canonical primary",
                                          "--start", "2026-10-20T11:00:00-04:00", "--end", "2026-10-20T12:00:00-04:00"])
        with patch.object(google, "calendars", return_value=[OWN]):
            proposal = google.propose(args)
        self.assertEqual(proposal["change"]["calendar_id"], OWN["id"])
        ambiguous = {"id": "other@example.test", "accessRole": "owner", "primary": True}
        for discovered in [[{**OWN, "primary": False}], [OWN, ambiguous], [{**OWN, "accessRole": "writer"}]]:
            with self.subTest(calendars=discovered), patch.object(google, "calendars", return_value=discovered):
                with self.assertRaisesRegex(google.GoogleError, "primary calendar"):
                    google.owned_calendar("primary")

    def make_proposal(self, operation="create"):
        argv = ["calendar", "propose", operation, "primary"]
        if operation != "create":
            argv.append(EVENT["id"])
        if operation != "delete":
            argv += ["--summary", "Reviewed meeting", "--start", "2026-10-20T11:00:00-04:00",
                     "--end", "2026-10-20T12:00:00-04:00"]
        with patch.object(google, "calendars", return_value=[OWN]), patch.object(google, "event", return_value={**EVENT, "calendar_id": OWN["id"]}):
            return google.propose(google.parser().parse_args(argv))

    def test_exact_confirmation_required(self):
        proposal = self.make_proposal()
        with patch.dict(os.environ, {"TARI_CALENDAR_WRITES": "1"}), patch.object(google, "run_gog") as run:
            with self.assertRaises(google.GoogleError):
                google.apply(proposal["proposal"], "wrong")
        run.assert_not_called()

    def test_apply_rejects_preexisting_secondary_proposal_without_consuming_it(self):
        proposal = self.make_proposal()
        token = proposal["proposal"]
        path = google.proposal_dir() / (token + ".json")
        payload = json.loads(path.read_text())
        secondary = {"id": "secondary@example.test", "accessRole": "owner", "primary": False}
        payload["calendar_id"] = secondary["id"]
        path.write_text(json.dumps(payload))
        with patch.dict(os.environ, {"TARI_CALENDAR_WRITES": "1"}), \
                patch.object(google, "calendars", return_value=[OWN, secondary]), \
                patch.object(google, "event") as event_read, patch.object(google, "run_gog") as run:
            with self.assertRaisesRegex(google.GoogleError, "primary calendar"):
                google.apply(token, token)
        event_read.assert_not_called()
        run.assert_not_called()
        self.assertEqual(json.loads(path.read_text())["status"], "pending")

    def test_concurrent_apply_lock_prevents_any_google_call(self):
        proposal = self.make_proposal()
        token = proposal["proposal"]
        with (google.proposal_dir() / (token + ".lock")).open("w") as held:
            google.fcntl.flock(held, google.fcntl.LOCK_EX | google.fcntl.LOCK_NB)
            with patch.dict(os.environ, {"TARI_CALENDAR_WRITES": "1"}), patch.object(google, "run_gog") as run:
                with self.assertRaisesRegex(google.GoogleError, "already being applied"):
                    google.apply(token, token)
                run.assert_not_called()
        payload = json.loads((google.proposal_dir() / (token + ".json")).read_text())
        self.assertEqual(payload["status"], "pending")

    def test_create_requires_reread_and_consumes_proposal(self):
        proposal = self.make_proposal()
        after = {**EVENT, "summary": "Reviewed meeting", "start": {"dateTime": "2026-10-20T15:00:00Z"},
                 "end": {"dateTime": "2026-10-20T16:00:00Z"}}
        with patch.dict(os.environ, {"TARI_CALENDAR_WRITES": "1"}), patch.object(google, "calendars", return_value=[OWN]), \
                patch.object(google, "run_gog", return_value={"event": {"id": EVENT["id"]}}) as run, \
                patch.object(google, "event", return_value=after) as read:
            result = google.apply(proposal["proposal"], proposal["confirmation"])
            self.assertTrue(result["verified"])
            read.assert_called_once_with(OWN["id"], EVENT["id"], absent=False, write_profile=True)
            self.assertTrue(run.call_args.kwargs["write"])
            with self.assertRaises(google.GoogleError):
                google.apply(proposal["proposal"], proposal["confirmation"])
        self.assertIn("--summary=Reviewed meeting", run.call_args.args[0])

    def test_primary_update_and_delete_keep_reread_verification(self):
        before = {**EVENT, "calendar_id": OWN["id"]}
        for operation in ["update", "delete"]:
            with self.subTest(operation=operation):
                proposal = self.make_proposal(operation)
                after = ({**EVENT, "summary": "Reviewed meeting", "start": {"dateTime": "2026-10-20T15:00:00Z"},
                          "end": {"dateTime": "2026-10-20T16:00:00Z"}} if operation == "update"
                         else {"id": EVENT["id"], "status": "cancelled"})
                with patch.dict(os.environ, {"TARI_CALENDAR_WRITES": "1"}), \
                        patch.object(google, "calendars", return_value=[OWN]), \
                        patch.object(google, "event", side_effect=[before, after]) as read, \
                        patch.object(google, "run_gog", return_value={}) as write:
                    result = google.apply(proposal["proposal"], proposal["confirmation"])
                self.assertTrue(result["verified"])
                self.assertEqual(write.call_args.args[0][:3], ["calendar", operation, OWN["id"]])
                self.assertEqual(read.call_count, 2)
                self.assertEqual(read.call_args.kwargs, {"absent": operation == "delete", "write_profile": True})

    def test_stale_update_and_recurring_changes_rejected(self):
        proposal = self.make_proposal("update")
        with patch.dict(os.environ, {"TARI_CALENDAR_WRITES": "1"}), patch.object(google, "calendars", return_value=[OWN]), \
                patch.object(google, "event", return_value={**EVENT, "etag": "new"}), patch.object(google, "run_gog") as run:
            with self.assertRaises(google.GoogleError):
                google.apply(proposal["proposal"], proposal["confirmation"])
            run.assert_not_called()
        args = google.parser().parse_args(["calendar", "propose", "delete", "primary", "event1"])
        with patch.object(google, "calendars", return_value=[OWN]), patch.object(google, "event", return_value={**EVENT, "recurringEventId": "series"}):
            with self.assertRaises(google.GoogleError):
                google.propose(args)

    def test_failed_verification_does_not_report_success(self):
        proposal = self.make_proposal()
        with patch.dict(os.environ, {"TARI_CALENDAR_WRITES": "1"}), patch.object(google, "calendars", return_value=[OWN]), \
                patch.object(google, "run_gog", return_value={"id": EVENT["id"]}), patch.object(google, "event", return_value=EVENT):
            with self.assertRaises(google.GoogleError):
                google.apply(proposal["proposal"], proposal["confirmation"])

    def test_deletion_requires_explicit_absence(self):
        result = subprocess.CompletedProcess([], 1, "", "googleapi: Error 404: Not Found")
        with patch.object(google.subprocess, "run", return_value=result):
            self.assertEqual(google.event("primary", "event1", absent=True), {"absent": True})
        result.stderr = "authentication unavailable"
        with patch.object(google.subprocess, "run", return_value=result):
            with self.assertRaises(google.GoogleError):
                google.event("primary", "event1", absent=True)


if __name__ == "__main__":
    unittest.main()
