"""Constrained runner contracts plus real Unix socket/subprocess fixture checks."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dailygrad_runner", ROOT / "scripts/dailygrad_runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

STUB = r'''
import json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
root = Path.cwd()
data = root / "data"
args = sys.argv[1:]
with (root / "calls").open("a") as stream:
    stream.write(json.dumps(args) + "\n")
mode = (root / "mode").read_text() if (root / "mode").exists() else "normal"
exercise = {"exercise_id": 1, "problem_id": "example-problem", "number": 1,
            "title": "Example problem", "url": "https://leetcode.com/problems/example-problem/",
            "track": "practice", "review": False, "assigned_on": "2026-10-20",
            "day": 1, "days_shown": 1, "outcome": None}
if args == ["config"]:
    print("Latest JSON: " + str(data / "latest.json"))
elif (root / "reply").exists():
    print((root / "reply").read_text())
    sys.exit(2 if mode == "error" else 0)
elif args == ["run"]:
    print("fixture-diagnostic-never-returned", file=sys.stderr)
    if mode in ("slow", "timeout"):
        time.sleep(0.4 if mode == "slow" else 5)
    if mode == "no_digest":
        sys.exit(0)
    old = json.loads((data / "latest.json").read_text()) if (data / "latest.json").exists() else {}
    doc = {"schema_version": 1, "run_id": old.get("run_id", 0) + 1,
           "date": datetime.now(ZoneInfo(os.environ["TZ"])).date().isoformat(),
           "generated_at": datetime.now(timezone.utc).isoformat(),
           "status": "degraded" if mode == "degraded" else "ok", "stories": [], "lesson": {}}
    (data / "latest.json").write_text(json.dumps(doc))
    sys.exit(1 if mode == "degraded" else 0)
elif args[:1] == ["sources"]:
    if mode == "sources_timeout":
        time.sleep(5)
    print(json.dumps({"ok": True, "changed": args[1] != "list", "mode": "all_except",
          "preferences_path": "/fixture/private/preferences.json",
          "sources": [{"id": "example-news", "name": "Example news", "group": "labs", "enabled": True}],
          "groups": [{"id": "labs", "name": "Labs", "sources": ["example-news"]}]}))
elif args[:2] == ["leetcode", "status"]:
    print(json.dumps({"ok": True, "enabled": True, "hints_enabled": True,
          "preferences_problem": None, "completion_pending": True,
          "active_exercise_id": 1, "current": exercise, "next_track": None,
          "hint": "fixture-spoiler", "solution": "fixture-answer"}))
elif args[:2] == ["leetcode", "hints"]:
    print(json.dumps({"ok": True, "changed": True, "hints_enabled": args[2] == "on"}))
elif args[:3] == ["leetcode", "mark", "complete"]:
    exercise["outcome"] = "completed"
    print(json.dumps({"ok": True, "changed": True, "exercise": exercise,
                     "completion_pending": False, "next_track": None}))
elif args[:2] == ["leetcode", "next"]:
    exercise["outcome"] = "skipped"
    print(json.dumps({"ok": True, "changed": True, "previous": exercise, "active": None}))
elif args[:1] == ["repair"]:
    print(json.dumps({"ok": True, "outcome": "repaired", "run_id": int(args[2]), "date": args[4],
          "generated_at": datetime.now(timezone.utc).isoformat(), "revision": 1,
          "previous_revision": 0, "written": True, "status": "ok",
          "attempted": {"stories": [1], "lesson": False},
          "repaired": {"stories": [1], "lesson": False},
          "remaining": {"stories": [], "lesson": False, "leetcode": False},
          "lesson_note": None, "current_files": [], "json": "/fixture/private/archive.json"}))
else:
    sys.exit(2)
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".test-tmp"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="d", dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.binary = self.root / "dg"
        self.binary.write_text("#!" + sys.executable + "\n" + STUB)
        self.binary.chmod(0o700)
        self.process = None
        self.logs = None
        self.sock = self.root / "s" / "runner.sock"
        self.addCleanup(self.stop_server)

    def start_server(self, *flags):
        self.logs = (self.root / "logs").open("w+")
        command = [sys.executable, "-B", str(ROOT / "scripts/dailygrad_runner.py"),
                   "--repo", str(self.root), "--bin", str(self.binary),
                   "--data-dir", str(self.data), "--runner-dir", str(self.root / "r"),
                   "--socket-dir", str(self.sock.parent), "--timezone", "America/Toronto",
                   "--run-timeout", "1", "--run-cooldown", "5", "--sources-timeout", "1", *flags]
        self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                        stdout=self.logs, stderr=self.logs)
        for _ in range(100):
            if self.sock.exists():
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.02)
        self.logs.seek(0)
        self.fail("Fixture runner failed to start: " + self.logs.read())

    def stop_server(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.logs:
            self.logs.close()

    def request(self, payload, disconnect=False):
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(5)
            conn.connect(str(self.sock))
            conn.sendall((json.dumps(payload) + "\n").encode())
            if disconnect:
                return
            raw = b""
            while b"\n" not in raw:
                block = conn.recv(65536)
                if not block:
                    break
                raw += block
        return json.loads(raw)

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]

    def test_strict_parser_rejects_injection_extra_fields_and_wrong_types(self):
        bad = [{"op": "run", "command": "arbitrary"},
               {"op": "run", "env": {}}, {"op": "shell"},
               {"op": "sources", "action": "set", "ids": ["--config"]},
               {"op": "sources", "action": "set", "ids": ["example-news\n"]},
               {"op": "leetcode", "action": "complete", "exercise": True},
               {"op": "leetcode", "action": "answer"},
               {"op": "repair", "run_id": 1, "date": "2026-02-30"},
               {"op": "repair", "run_id": "1", "date": "2026-10-20"}]
        for request in bad:
            with self.subTest(request=request):
                self.assertIsNotNone(runner.parse((json.dumps(request) + "\n").encode())[1])
        self.assertIsNotNone(runner.parse(b'{"op":"run"}\n{}\n')[1])

    def test_default_capabilities_disable_mutations_but_allow_reads(self):
        self.start_server()
        for payload in ({"op": "run"}, {"op": "repair", "run_id": 1, "date": "2026-10-20"},
                        {"op": "sources", "action": "set", "ids": ["example-news"]},
                        {"op": "leetcode", "action": "hints_off"}):
            self.assertEqual(self.request(payload)["error"]["code"], "capability_disabled")
        self.assertEqual(self.calls(), [["config"]])
        self.assertTrue(self.request({"op": "sources", "action": "list"})["ok"])
        status = self.request({"op": "leetcode", "action": "status"})
        self.assertEqual(status["exercise"]["exercise_id"], 1)
        self.assertFalse(status["changed"])
        self.assertNotIn("fixture-spoiler", json.dumps(status))
        self.assertNotIn("fixture-answer", json.dumps(status))
        self.assertEqual(self.sock.stat().st_mode & 0o777, 0o600)

    def test_real_run_verifies_new_digest_and_cooldown_prevents_duplicate(self):
        self.start_server("--allow-run")
        reply = self.request({"op": "run"})
        self.assertTrue(reply["ok"])
        self.assertTrue(reply["new_run"])
        self.assertTrue(reply["is_today"])
        self.assertNotIn("fixture-diagnostic", json.dumps(reply))
        self.assertEqual(self.request({"op": "run"})["result"], "cooldown")
        self.assertEqual(self.calls().count(["run"]), 1)

    def test_another_runner_cannot_steal_a_shared_socket_from_different_state(self):
        self.start_server()
        command = [sys.executable, "-B", str(ROOT / "scripts/dailygrad_runner.py"),
                   "--repo", str(self.root), "--bin", str(self.binary),
                   "--data-dir", str(self.data), "--runner-dir", str(self.root / "r2"),
                   "--socket-dir", str(self.sock.parent)]
        duplicate = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("already owns this socket", duplicate.stdout)
        self.assertTrue(self.request({"op": "sources", "action": "list"})["ok"])

    def test_disconnected_client_does_not_launch_second_concurrent_run(self):
        (self.root / "mode").write_text("slow")
        self.start_server("--allow-run")
        self.request({"op": "run"}, disconnect=True)
        for _ in range(100):
            if ["run"] in self.calls():
                break
            time.sleep(0.01)
        busy = self.request({"op": "run"})
        self.assertEqual(busy["result"], "busy")
        self.assertTrue(busy["running_since"])
        self.assertEqual(self.calls().count(["run"]), 1)

    def test_worker_timeout_reports_failure_and_never_claims_digest(self):
        (self.root / "mode").write_text("timeout")
        self.start_server("--allow-run")
        reply = self.request({"op": "run"})
        self.assertFalse(reply["ok"])
        self.assertTrue(reply["timed_out"])
        self.assertFalse(reply["new_run"])
        self.assertEqual(reply["error"]["code"], "timeout")
        self.assertEqual(self.request({"op": "run"})["result"], "cooldown")

    def test_degraded_digest_is_verified_as_degraded(self):
        (self.root / "mode").write_text("degraded")
        self.start_server("--allow-run")
        result = self.request({"op": "run"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["digest"]["status"], "degraded")
        self.assertEqual(result["exit_status"], 1)

    def test_normal_exit_without_new_digest_is_not_reported_as_success(self):
        (self.root / "mode").write_text("no_digest")
        self.start_server("--allow-run")
        result = self.request({"op": "run"})
        self.assertFalse(result["ok"])
        self.assertFalse(result["new_run"])
        self.assertEqual(result["error"]["code"], "no_new_digest")

    def test_run_and_repair_share_the_model_job_lock(self):
        (self.root / "mode").write_text("slow")
        self.start_server("--allow-run", "--allow-repair")
        self.request({"op": "run"}, disconnect=True)
        for _ in range(100):
            if ["run"] in self.calls():
                break
            time.sleep(0.01)
        reply = self.request({"op": "repair", "run_id": 1, "date": "2026-10-20"})
        self.assertEqual(reply["result"], "busy")
        self.assertFalse(any(call[0] == "repair" for call in self.calls()))

    def test_source_timeout_does_not_claim_a_mutation_succeeded(self):
        (self.root / "mode").write_text("sources_timeout")
        self.start_server("--allow-sources-update")
        reply = self.request({"op": "sources", "action": "set", "ids": ["example-news"]})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["code"], "sources_timeout")

    def test_unexpected_or_mismatched_upstream_result_is_not_verified(self):
        self.assertIsNone(runner.clean_sources({"ok": True, "changed": True, "mode": "other"}))
        self.assertIsNone(runner.clean_leetcode("complete", {"ok": True}, 1))
        self.assertIsNone(runner.clean_repair({"ok": True, "run_id": 2}, 1, "2026-10-20"))

    def test_malformed_source_group_returns_bad_cli_output_without_dropping_socket(self):
        response = {"ok": True, "changed": False, "mode": "all_except", "groups": [],
                    "sources": [{"id": "example-news", "name": "Example", "enabled": True}]}
        (self.root / "reply").write_text(json.dumps(response))
        self.start_server()
        result = self.request({"op": "sources", "action": "list"})
        self.assertEqual(result["error"]["code"], "bad_cli_output")
        response["sources"][0]["group"] = None
        (self.root / "reply").write_text(json.dumps(response))
        self.assertTrue(self.request({"op": "sources", "action": "list"})["ok"])

    def test_repair_cleaner_rejects_missing_nullable_key_and_nested_file_types(self):
        response = {"ok": True, "outcome": "repaired", "run_id": 7, "date": "2026-10-20",
                    "generated_at": "2026-10-20T12:00:00+00:00", "revision": 1,
                    "previous_revision": 0, "written": True, "status": "ok",
                    "attempted": {"stories": [1], "lesson": False},
                    "repaired": {"stories": [1], "lesson": False},
                    "remaining": {"stories": [], "lesson": False, "leetcode": False},
                    "current_files": []}
        self.assertIsNone(runner.clean_repair(response, 7, "2026-10-20"))
        response["lesson_note"] = None
        self.assertIsNotNone(runner.clean_repair(response, 7, "2026-10-20"))
        for bad in [[[]], [{}], [False], [None]]:
            response["current_files"] = bad
            self.assertIsNone(runner.clean_repair(response, 7, "2026-10-20"))
        (self.root / "reply").write_text(json.dumps(response))
        self.start_server("--allow-repair")
        result = self.request({"op": "repair", "run_id": 7, "date": "2026-10-20"})
        self.assertEqual(result["error"]["code"], "bad_cli_output")

    def test_validators_are_total_for_wrong_json_field_types(self):
        sources = {"ok": True, "changed": False, "mode": "all_except", "groups": [],
                   "sources": [{"id": "example-news", "name": "Example", "group": None, "enabled": True}]}
        repair = {"ok": True, "outcome": "repaired", "run_id": 7, "date": "2026-10-20",
                  "generated_at": "2026-10-20T12:00:00+00:00", "revision": 1,
                  "previous_revision": 0, "written": True, "status": "ok",
                  "attempted": {"stories": [1], "lesson": False},
                  "repaired": {"stories": [1], "lesson": False},
                  "remaining": {"stories": [], "lesson": False, "leetcode": False},
                  "current_files": [], "lesson_note": None}
        candidates = [None, {}, [], [[]], {"nested": []}, True, 1, 1.5, ""]
        checks = [(sources, runner.clean_sources),
                  (repair, lambda doc: runner.clean_repair(doc, 7, "2026-10-20")),
                  ({"ok": True, "enabled": True, "hints_enabled": True, "completion_pending": False,
                    "active_exercise_id": None, "current": None, "next_track": None},
                   lambda doc: runner.clean_leetcode("status", doc))]
        for original, cleaner in checks:
            for key in original:
                for value in candidates:
                    with self.subTest(cleaner=cleaner, key=key, value=value):
                        document = deepcopy(original)
                        document[key] = value
                        result = cleaner(document)
                        self.assertTrue(result is None or isinstance(result, dict))

    def test_unhashable_upstream_error_codes_return_errors_without_dropping_socket(self):
        (self.root / "mode").write_text("error")
        (self.root / "reply").write_text(json.dumps({"ok": False, "error": {"code": [], "message": "fixture"}}))
        self.start_server("--allow-leetcode-update", "--allow-repair")
        result = self.request({"op": "sources", "action": "list"})
        self.assertEqual(result["error"]["code"], "bad_cli_output")
        result = self.request({"op": "leetcode", "action": "complete", "exercise": 1})
        self.assertEqual(result["error"]["code"], "bad_cli_output")
        result = self.request({"op": "repair", "run_id": 7, "date": "2026-10-20"})
        self.assertEqual(result["error"]["code"], "bad_cli_output")

    def test_fixed_source_and_leetcode_commands_preserve_upstream_semantics(self):
        self.start_server("--allow-sources-update", "--allow-leetcode-update")
        sources = self.request({"op": "sources", "action": "set", "ids": ["example-news"]})
        self.assertTrue(sources["changed"])
        self.assertIn(["sources", "set", "--json", "--", "example-news"], self.calls())
        self.assertNotIn("preferences_path", json.dumps(sources))
        completed = self.request({"op": "leetcode", "action": "complete", "exercise": 1})
        self.assertEqual(completed["exercise"]["outcome"], "completed")
        self.assertFalse(completed["completion_pending"])
        self.assertIn(["leetcode", "mark", "complete", "--exercise", "1", "--json"], self.calls())
        moved = self.request({"op": "leetcode", "action": "next", "exercise": 1})
        self.assertEqual(moved["previous"]["outcome"], "skipped")
        self.assertIsNone(moved["active"])
        self.assertNotIn(["run"], self.calls())

    def test_repair_is_named_revision_not_a_new_run_and_drops_paths(self):
        self.start_server("--allow-repair")
        repaired = self.request({"op": "repair", "run_id": 7, "date": "2026-10-20"})
        self.assertTrue(repaired["ok"])
        self.assertEqual(repaired["repair"]["run_id"], 7)
        self.assertEqual(repaired["repair"]["revision"], 1)
        self.assertNotIn("/fixture/private", json.dumps(repaired))
        self.assertNotIn(["run"], self.calls())
        self.assertEqual(self.request({"op": "repair", "run_id": 7, "date": "2026-10-20"})["result"], "cooldown")

    def test_digest_identity_refuses_symlink_and_naive_timestamp(self):
        target = self.root / "other.json"
        target.write_text("{}")
        linked = self.data / "latest.json"
        linked.symlink_to(target)
        self.assertEqual(runner.identity(linked)[1], "unreadable_digest")
        linked.unlink()
        linked.write_text(json.dumps({"schema_version": 1, "run_id": 1, "date": "2026-10-20",
                                     "generated_at": "2026-10-20T12:00:00", "status": "ok", "stories": []}))
        self.assertEqual(runner.identity(linked)[1], "invalid_digest")


if __name__ == "__main__":
    unittest.main()
