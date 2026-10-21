"""Workflow policy tests against a fixture native scheduler; no services or models."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tari_automation_under_test", REPO / "scripts/tari_automation.py")
automation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(automation)
TARGET = "user:123456789012345678"
CHANNEL_TARGET = "channel:987654321098765432"


class NativeScheduler:
    """Model the pinned public job wire contract and declaration-key upserts."""

    def __init__(self):
        self.jobs = {}
        self.calls = []
        self.runs = {}
        self.fail = set()
        self.accounts = [{"accountId": "default", "configured": True, "running": True, "connected": True}]
        self.channel_reply = None
        self.add_transform = None
        self.show_transform = None
        self.ignore_enable = False
        self.ignore_disable = False
        self.timeout_after_enable = False
        self.timeout_after_add = False
        self.before_enable = None
        self.list_transform = None

    def __call__(self, *arguments):
        self.calls.append(arguments)
        operation = arguments[:2]
        if operation in self.fail:
            raise OSError("fixture diagnostic must not leak")
        if operation == ("channels", "status"):
            return deepcopy(self.channel_reply if self.channel_reply is not None else
                            {"channelAccounts": {"discord": self.accounts}})
        if operation == ("automations", "add"):
            def flag(name, default=None):
                return arguments[arguments.index(name) + 1] if name in arguments else default
            key = flag("--declaration-key")
            previous = next((job for job in self.jobs.values() if job.get("declarationKey") == key), None)
            job_id = previous["id"] if previous else f"{len(self.jobs) + 1:08x}-9e47-4a8b-9b4e-7217bfa00000"
            job = {
                "id": job_id, "declarationKey": key, "name": flag("--name"),
                "enabled": "--disabled" not in arguments,
                "agentId": flag("--agent"), "sessionTarget": flag("--session"),
                "schedule": {"kind": "cron", "expr": flag("--cron"), "tz": flag("--tz"),
                             "staggerMs": 0 if "--exact" in arguments else 300000},
                "payload": {"kind": "agentTurn", "message": flag("--message"),
                            "model": flag("--model"), "thinking": flag("--thinking"),
                            "fallbacks": flag("--fallbacks", "").split(",") if flag("--fallbacks") else [],
                            "toolsAllow": flag("--tools", "").split(","),
                            "lightContext": "--light-context" in arguments,
                            "timeoutSeconds": int(flag("--timeout-seconds", "300"))},
                "delivery": {"mode": "announce" if "--announce" in arguments else "none",
                             "channel": flag("--channel"), "to": flag("--to")},
                "state": deepcopy(previous.get("state", {})) if previous else {},
            }
            if "--command-argv" in arguments:
                job["schedule"] = {"kind": "at", "at": flag("--at")}
                job["payload"] = {
                    "kind": "command", "argv": json.loads(flag("--command-argv")),
                    "toolsAllow": flag("--tools", "").split(",") if flag("--tools") else [],
                    "timeoutSeconds": int(flag("--timeout-seconds", "30")),
                    "outputMaxBytes": int(flag("--output-max-bytes", "8192")),
                }
                job["deleteAfterRun"] = "--keep-after-run" not in arguments
            if flag("--account"):
                job["delivery"]["accountId"] = flag("--account")
            if self.add_transform:
                self.add_transform(job)
            self.jobs[job_id] = deepcopy(job)
            if self.timeout_after_add:
                raise subprocess.TimeoutExpired("fixture-native", 1)
            return {"updated": True, "job": deepcopy(job)} if previous else {"created": True, "job": deepcopy(job)}
        if operation == ("automations", "show"):
            job = deepcopy(self.jobs.get(arguments[2]))
            return self.show_transform(job) if self.show_transform else job
        if operation == ("automations", "list"):
            jobs = deepcopy(list(self.jobs.values()))
            return self.list_transform(jobs) if self.list_transform else {"jobs": jobs}
        if operation in (("automations", "enable"), ("automations", "disable")):
            job = self.jobs[arguments[2]]
            if operation[1] == "enable" and self.before_enable:
                self.before_enable(job)
            ignored = self.ignore_enable if operation[1] == "enable" else self.ignore_disable
            if not ignored:
                job["enabled"] = operation[1] == "enable"
            if self.timeout_after_enable and operation[1] == "enable":
                raise subprocess.TimeoutExpired("fixture-native", 1)
            return deepcopy(job)
        if operation == ("automations", "runs"):
            return {"entries": deepcopy(self.runs.get(arguments[2], []))}
        raise AssertionError(f"Unexpected native operation: {arguments!r}")


class MorningBriefTests(unittest.TestCase):
    def setUp(self):
        base = REPO / ".test-tmp"
        base.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state = self.directory / "workflow-state"
        self.native = NativeScheduler()
        # No Google or DailyGrad integration is present, and the main model is independent.
        self.config = {"agents": {"defaults": {"model": {"primary": "openai/main-fixture"}}}}
        self.manager = self.new_manager()

    def new_manager(self):
        return automation.Manager(self.state, self.config, "America/Toronto", self.native,
                                  now=lambda: datetime(2026, 10, 21, 12, tzinfo=timezone.utc))

    def enable(self, **kwargs):
        return self.manager.enable_brief(target=TARGET, **kwargs)

    def only_job(self):
        self.assertEqual(len(self.native.jobs), 1)
        return next(iter(self.native.jobs.values()))

    def test_fresh_install_has_no_jobs_or_state_and_disable_does_not_contact_scheduler(self):
        self.assertEqual(self.manager.brief_status(), {"configured": False, "enabled": False, "registered": False})
        self.assertEqual(self.manager.disable_brief(), {"configured": False, "enabled": False})
        self.assertEqual(self.native.jobs, {})
        self.assertEqual(self.native.calls, [])
        self.assertFalse(self.manager.path.exists())

    def test_opt_in_registers_actual_native_id_without_claiming_delivery(self):
        result = self.enable()
        job = self.only_job()
        self.assertTrue(result["registered"])
        self.assertTrue(result["enabled"])
        self.assertEqual(result["job_id"], job["id"])
        self.assertNotEqual(result["job_id"], job["name"])
        self.assertFalse(result["delivered"])
        self.assertIn("not yet verified", result["delivery_note"])
        self.assertTrue(job["enabled"])
        self.assertEqual(job["agentId"], "tari")
        self.assertEqual(job["sessionTarget"], "isolated")
        self.assertEqual(job["schedule"], {"kind": "cron", "expr": "0 9 * * *", "tz": "America/Toronto", "staggerMs": 0})
        self.assertEqual(job["delivery"], {"mode": "announce", "channel": "discord", "to": TARGET})

    def test_time_timezone_model_thinking_and_account_are_owner_configurable(self):
        self.native.accounts.append({"accountId": "hackathon", "configured": True, "running": True, "connected": True})
        result = self.manager.enable_brief(clock="07:45", time_zone="Europe/Paris", model="anthropic/fixture-model",
                                           thinking="low", channel="discord", target=CHANNEL_TARGET, account="hackathon")
        job = self.only_job()
        self.assertEqual(job["schedule"], {"kind": "cron", "expr": "45 7 * * *", "tz": "Europe/Paris", "staggerMs": 0})
        self.assertEqual(job["payload"]["model"], "anthropic/fixture-model")
        self.assertEqual(job["payload"]["thinking"], "low")
        self.assertEqual(job["delivery"]["accountId"], "hackathon")
        self.assertEqual(result["destination"], {"channel": "discord", "to": CHANNEL_TARGET, "account": "hackathon"})
        self.assertIn("Europe/Paris", job["payload"]["message"])

    def test_daily_schedule_preserves_iana_timezone_for_native_dst_handling(self):
        self.enable(clock="09:00", time_zone="America/Toronto")
        job = self.only_job()
        self.assertEqual(job["schedule"]["expr"], "0 9 * * *")
        self.assertEqual(job["schedule"]["tz"], "America/Toronto")
        # No fixed UTC offset or custom timing calculation is stored.
        self.assertNotIn("at", job["schedule"])
        self.assertNotIn("everyMs", job["schedule"])

    def test_brief_has_bounded_read_tools_no_exec_and_no_more_expensive_fallback(self):
        original = deepcopy(self.config)
        self.enable()
        payload = self.only_job()["payload"]
        self.assertEqual(set(payload["toolsAllow"]), {"tari_brief_context", "dailygrad_digest"})
        self.assertEqual(payload["fallbacks"], [])
        self.assertTrue(payload["lightContext"])
        self.assertEqual(payload["timeoutSeconds"], 300)
        self.assertEqual(payload["model"], "openai/gpt-6.1-sol")
        self.assertEqual(payload["thinking"], "medium")
        self.assertEqual(self.config, original)
        self.assertFalse(any(flag in call for call in self.native.calls for flag in ("--exec", "--command", "--script")))

    def test_existing_data_sources_are_optional_and_no_planner_calendar_or_dailygrad_mutation_occurs(self):
        planner = self.directory / "workspace/planner/day.json"
        planner.parent.mkdir(parents=True)
        planner.write_text('{"fixture":"keep saved planner unchanged"}\n')
        memory = self.directory / "workspace/MEMORY.md"
        memory.write_text("Fixture curated memory must remain unchanged\n")
        before = {path: path.read_bytes() for path in (planner, memory)}
        result = self.enable()
        self.manager.brief_status()
        self.manager.history(result["job_id"], result["key"])
        self.manager.disable_brief()
        self.assertEqual({path: path.read_bytes() for path in (planner, memory)}, before)
        self.assertEqual(set(call[:2] for call in self.native.calls), {
            ("channels", "status"), ("automations", "add"), ("automations", "show"),
            ("automations", "enable"), ("automations", "disable"), ("automations", "runs"),
        })

    def test_reenable_and_reconfiguration_upsert_the_same_native_declaration(self):
        first = self.enable()
        second = self.enable(clock="08:15", model="openai/another-fixture-model", thinking="high")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(self.native.jobs), 1)
        self.assertEqual(self.only_job()["schedule"]["expr"], "15 8 * * *")
        self.assertEqual(self.only_job()["payload"]["model"], "openai/another-fixture-model")
        declarations = [call[call.index("--declaration-key") + 1] for call in self.native.calls
                        if call[:2] == ("automations", "add")]
        self.assertEqual(len(set(declarations)), 1)

    def test_restart_reads_persisted_actual_id_and_native_status(self):
        first = self.enable()
        self.manager = self.new_manager()
        status = self.manager.brief_status()
        self.assertTrue(status["registered"])
        self.assertTrue(status["enabled"])
        self.assertEqual(status["job_id"], first["job_id"])
        self.assertEqual(self.manager.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.manager.settings()["morning"]["job_id"], first["job_id"])

    def test_lost_local_record_does_not_create_second_declarative_native_job(self):
        first = self.enable()
        self.manager.path.unlink()
        self.assertFalse(self.new_manager().brief_status()["configured"])
        second = self.new_manager().enable_brief(target=TARGET)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(len(self.native.jobs), 1)

    def test_unrelated_jobs_even_with_same_display_name_are_preserved(self):
        unrelated_id = "6e536c6e-0d75-46c0-a63e-84c80d7aae2d"
        unrelated = {"id": unrelated_id, "declarationKey": "unrelated-owner-job", "name": "Tari morning brief",
                     "enabled": True, "payload": {"kind": "fixture"}, "state": {"lastRunStatus": "ok"}}
        self.native.jobs[unrelated_id] = deepcopy(unrelated)
        self.enable()
        self.enable(clock="10:00")
        self.manager.disable_brief()
        self.assertEqual(len(self.native.jobs), 2)
        self.assertEqual(self.native.jobs[unrelated_id], unrelated)
        self.assertFalse(any(unrelated_id in call for call in self.native.calls))

    def test_disable_is_verified_and_survives_restart(self):
        first = self.enable()
        result = self.manager.disable_brief()
        self.assertFalse(result["enabled"])
        self.assertFalse(self.native.jobs[first["job_id"]]["enabled"])
        self.assertFalse(self.new_manager().brief_status()["enabled"])
        self.assertIn("in-flight", result["note"])
        self.manager.disable_brief()
        self.assertEqual(len(self.native.jobs), 1)

    def test_native_enable_refusal_never_reports_success(self):
        self.native.ignore_enable = True
        with self.assertRaises(automation.WorkflowError):
            self.enable()
        self.assertFalse(self.only_job()["enabled"])
        self.assertFalse(self.manager.settings()["morning"]["enabled"])

    def test_unexpected_active_registration_is_quiesced_before_policy_acceptance(self):
        self.native.add_transform = lambda job: job.update(enabled=True)
        self.enable()
        mutations = [call[:2] for call in self.native.calls if call[:2] in {
            ("automations", "add"), ("automations", "disable"), ("automations", "enable")}]
        self.assertEqual(mutations, [("automations", "add"), ("automations", "disable"), ("automations", "enable")])
        self.assertTrue(self.only_job()["enabled"])

    def test_unexpected_active_registration_that_cannot_be_disabled_is_not_claimed(self):
        self.native.add_transform = lambda job: job.update(enabled=True)
        self.native.ignore_disable = True
        with self.assertRaisesRegex(automation.WorkflowError, "could not be disabled"):
            self.enable()
        self.assertFalse(self.manager.path.exists())
        self.assertFalse(any(call[:2] == ("automations", "enable") for call in self.native.calls))

    def test_native_disable_refusal_does_not_mark_saved_job_disabled(self):
        self.enable()
        self.native.ignore_disable = True
        with self.assertRaises(automation.WorkflowError):
            self.manager.disable_brief()
        self.assertTrue(self.only_job()["enabled"])
        self.assertTrue(self.manager.settings()["morning"]["enabled"])

    def test_timed_out_enable_has_unknown_outcome_and_status_recovers_actual_native_state(self):
        self.native.timeout_after_enable = True
        with self.assertRaisesRegex(automation.WorkflowError, "could not be verified"):
            self.enable()
        self.assertTrue(self.only_job()["enabled"])
        self.assertFalse(self.manager.settings()["morning"]["enabled"])
        self.assertTrue(self.new_manager().brief_status()["enabled"])
        self.assertEqual(len(self.native.jobs), 1)

    def test_invalid_schedule_model_thinking_or_zone_cannot_register_jobs(self):
        variants = [{"clock": "9:00"}, {"clock": "24:00"}, {"clock": "09:60"}, {"clock": "09:00;anything"},
                    {"time_zone": "Mars/Fixture"}, {"model": "model with spaces"}, {"model": "$(fixture)"},
                    {"thinking": "unbounded"}]
        for arguments in variants:
            with self.subTest(arguments=arguments), self.assertRaises(automation.WorkflowError):
                self.enable(**arguments)
        self.assertEqual(self.native.jobs, {})
        self.assertEqual(self.native.calls, [])

    def test_missing_ambiguous_or_unsupported_destinations_never_enable_jobs(self):
        variants = [{"target": None}, {"target": "123456789012345678"}, {"target": "@someone"},
                    {"target": "user:123"}, {"target": "user:123456789012345678\n"},
                    {"target": TARGET, "channel": "unknown"}, {"target": TARGET, "account": "../other"}]
        for arguments in variants:
            with self.subTest(arguments=arguments), self.assertRaises(automation.WorkflowError):
                self.manager.enable_brief(**arguments)
        self.assertEqual(self.native.jobs, {})
        self.assertEqual(self.native.calls, [])

    def test_unconfigured_disconnected_stopped_or_unknown_account_blocks_activation(self):
        for changed in ({"configured": False}, {"running": False}, {"connected": False}, {"accountId": "other"}):
            row = {"accountId": "default", "configured": True, "running": True, "connected": True, **changed}
            self.native.accounts = [row]
            with self.subTest(changed=changed), self.assertRaises(automation.WorkflowError):
                self.enable()
        self.assertEqual(self.native.jobs, {})
        self.assertFalse(self.manager.path.exists())

    def test_native_offline_before_registration_does_not_save_or_claim_a_job(self):
        self.native.fail.add(("channels", "status"))
        with self.assertRaises(automation.WorkflowError) as caught:
            self.enable()
        self.assertNotIn("fixture diagnostic", str(caught.exception))
        self.assertEqual(self.native.jobs, {})
        self.assertFalse(self.manager.path.exists())

    def test_config_only_status_never_authorizes_delivery_even_with_stale_connected_rows(self):
        for unavailable in ({"configOnly": True}, {"gatewayReachable": False}):
            self.native.channel_reply = {"channelAccounts": {"discord": self.native.accounts}, **unavailable}
            with self.subTest(unavailable=unavailable), self.assertRaises(automation.WorkflowError):
                self.enable()
        self.assertEqual(self.native.jobs, {})

    def test_missing_or_mismatched_native_job_is_unverified_and_cannot_be_disabled(self):
        result = self.enable()
        job = self.native.jobs.pop(result["job_id"])
        self.assertEqual(self.manager.brief_status()["registered"], "unverified")
        self.assertEqual(self.manager.brief_status()["enabled"], "unverified")
        with self.assertRaises(automation.WorkflowError):
            self.manager.disable_brief()
        job["declarationKey"] = "unrelated-job-with-reused-id"
        self.native.jobs[job["id"]] = job
        self.assertEqual(self.manager.brief_status()["registered"], "unverified")
        with self.assertRaises(automation.WorkflowError):
            self.manager.disable_brief()
        self.assertTrue(job["enabled"])
        self.assertFalse(any(call[:2] == ("automations", "disable") for call in self.native.calls))

    def test_registration_contract_mismatch_is_left_disabled(self):
        changes = [
            lambda job: job["payload"].update(kind="command"),
            lambda job: job["payload"].update(model="unrequested/expensive-model"),
            lambda job: job["payload"].update(fallbacks=["unrequested/expensive-model"]),
            lambda job: job["payload"].update(toolsAllow=["exec", "tari_brief_context"]),
            lambda job: job["payload"].update(toolsAllow=["tari_brief_context", ["exec"]]),
            lambda job: job["payload"].update(lightContext=False),
            lambda job: job["payload"].update(message="Invoke an administrative tool instead"),
            lambda job: job["payload"].update(timeoutSeconds=900),
            lambda job: job["schedule"].update(kind="at"),
            lambda job: job["schedule"].update(staggerMs=300000),
            lambda job: job["schedule"].update(tz="UTC"),
            lambda job: job["delivery"].update(to=CHANNEL_TARGET),
            lambda job: job["delivery"].update(accountId="unrequested-account"),
            lambda job: job.update(sessionTarget="main"),
            lambda job: job.update(agentId="another-agent"),
        ]
        for change in changes:
            self.native.add_transform = change
            with self.subTest(change=change), self.assertRaisesRegex(automation.WorkflowError, "left disabled"):
                self.enable()
            self.assertFalse(self.only_job()["enabled"])
            self.assertFalse(self.manager.path.exists())
        self.assertFalse(any(call[:2] == ("automations", "enable") for call in self.native.calls))

    def test_wrong_native_delivery_account_is_never_enabled(self):
        self.native.accounts.append({"accountId": "selected", "configured": True, "running": True, "connected": True})
        self.native.add_transform = lambda job: job["delivery"].update(accountId="wrong-account")
        with self.assertRaisesRegex(automation.WorkflowError, "left disabled"):
            self.enable(account="selected")
        self.assertFalse(self.only_job()["enabled"])

    def test_malformed_native_job_fields_produce_controlled_refusal(self):
        for key, value in (("payload", []), ("schedule", None), ("delivery", "unexpected")):
            self.native.add_transform = lambda job, key=key, value=value: job.update({key: value})
            with self.subTest(key=key), self.assertRaises(automation.WorkflowError):
                self.enable()
            self.assertFalse(self.only_job()["enabled"])

    def test_malformed_native_channel_status_produces_controlled_refusal(self):
        for reply in ({"channelAccounts": []}, {"channelAccounts": None}, {"channelAccounts": {"discord": {}}}, []):
            self.native.channel_reply = reply
            with self.subTest(reply=reply), self.assertRaises(automation.WorkflowError):
                self.enable()
        self.assertEqual(self.native.jobs, {})

    def test_state_and_history_report_delivery_failures_without_private_diagnostics(self):
        result = self.enable()
        job = self.only_job()
        job["state"] = {"nextRunAtMs": 1803128400000, "lastRunStatus": "ok", "lastDeliveryStatus": "failed",
                        "lastDelivered": False, "lastDeliveryError": "fixture credential should be omitted",
                        "lastError": "fixture private source diagnostic", "unexpected": "fixture private metadata"}
        self.native.runs[job["id"]] = [
            {"runId": f"fixture-{index}", "status": "ok", "deliveryStatus": "failed", "delivered": False,
             "summary": "fixture private message", "error": "fixture sensitive error", "durationMs": 120}
            for index in range(12)
        ] + ["malformed entry"]
        status = self.manager.brief_status()
        self.assertTrue(status["state"]["delivery_error"])
        self.assertTrue(status["state"]["execution_error"])
        self.assertFalse(status["state"]["lastDelivered"])
        history = self.manager.history(result["job_id"], result["key"])
        self.assertEqual(len(history), 10)
        self.assertTrue(all(row["deliveryStatus"] == "failed" and row["delivered"] is False for row in history))
        text = json.dumps({"status": status, "history": history})
        for forbidden in ("fixture credential", "fixture private", "fixture sensitive"):
            self.assertNotIn(forbidden, text)
        self.assertIn(("automations", "runs", job["id"], "--limit", "10", "--json"), self.native.calls)

    def test_history_requires_confirmed_job_ownership(self):
        result = self.enable()
        self.native.jobs[result["job_id"]]["declarationKey"] = "unrelated"
        with self.assertRaises(automation.WorkflowError):
            self.manager.history(result["job_id"], result["key"])
        self.assertFalse(any(call[:2] == ("automations", "runs") for call in self.native.calls))

    def test_malformed_persisted_settings_are_not_overwritten(self):
        self.state.mkdir()
        self.manager.path.write_text('{"version":1,"reminders":[],"morning":"private invalid fixture"}')
        before = self.manager.path.read_bytes()
        with self.assertRaises(automation.WorkflowError):
            self.enable()
        self.assertEqual(self.manager.path.read_bytes(), before)
        self.assertEqual(self.native.jobs, {})

    def test_state_symlink_is_refused_before_native_access(self):
        selected = self.directory / "selected-fixture"
        selected.mkdir()
        self.state.symlink_to(selected, target_is_directory=True)
        with self.assertRaises(automation.WorkflowError):
            self.new_manager()
        self.assertEqual(self.native.calls, [])


class ReminderTimeTests(unittest.TestCase):
    NOW = datetime(2050, 1, 1, 12, tzinfo=timezone.utc)

    def test_local_datetime_uses_the_configured_timezone(self):
        result = automation.parse_when("2050-07-01T09:30", "America/Toronto", self.NOW)
        self.assertEqual(result, datetime(2050, 7, 1, 13, 30, tzinfo=timezone.utc))
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_explicit_offset_and_z_resolve_absolute_time(self):
        for raw in ("2050-01-02T09:00-05:00", "2050-01-02T14:00Z", "2050-01-02T15:00+01:00"):
            with self.subTest(raw=raw):
                self.assertEqual(automation.parse_when(raw, "America/Toronto", self.NOW),
                                 datetime(2050, 1, 2, 14, tzinfo=timezone.utc))

    def test_dst_nonexistent_and_ambiguous_wall_times_require_clarification(self):
        for raw in ("2050-03-13T02:30", "2050-11-06T01:30"):
            with self.subTest(raw=raw), self.assertRaises(automation.WorkflowError):
                automation.parse_when(raw, "America/Toronto", self.NOW)

    def test_dst_folds_are_unambiguous_with_explicit_offsets(self):
        first = automation.parse_when("2050-11-06T01:30-04:00", "America/Toronto", self.NOW)
        second = automation.parse_when("2050-11-06T01:30-05:00", "America/Toronto", self.NOW)
        self.assertEqual(first, datetime(2050, 11, 6, 5, 30, tzinfo=timezone.utc))
        self.assertEqual(second, datetime(2050, 11, 6, 6, 30, tzinfo=timezone.utc))

    def test_duration_means_elapsed_time_across_spring_and_fall_dst(self):
        cases = [
            (datetime(2050, 3, 13, 6, 50, tzinfo=timezone.utc), datetime(2050, 3, 13, 7, 10, tzinfo=timezone.utc)),
            (datetime(2050, 11, 6, 5, 50, tzinfo=timezone.utc), datetime(2050, 11, 6, 6, 10, tzinfo=timezone.utc)),
        ]
        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(automation.parse_when("20m", "America/Toronto", now), expected)

    def test_positive_duration_units_are_supported_without_model_interpretation(self):
        expected = {"30s": "2050-01-01T12:00:30+00:00", "20m": "2050-01-01T12:20:00+00:00",
                    "2h": "2050-01-01T14:00:00+00:00", "1d": "2050-01-02T12:00:00+00:00"}
        for raw, iso in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(automation.parse_when(raw, "America/Toronto", self.NOW).isoformat(), iso)

    def test_past_malformed_unresolved_and_out_of_range_times_are_rejected(self):
        cases = ("2049-12-31T09:00Z", "2050-01-01T12:00Z", "tomorrow morning", "4 PM", "2050-01-02",
                 "2050-02-30T09:00", "0m", "-5m", "367d", "10000000d", "", None)
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(automation.WorkflowError):
                automation.parse_when(raw, "America/Toronto", self.NOW)


class ReminderTests(unittest.TestCase):
    def setUp(self):
        base = REPO / ".test-tmp"
        base.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state = self.directory / "workflow-state"
        self.native = NativeScheduler()
        self.now = datetime(2050, 1, 1, 12, tzinfo=timezone.utc)
        self.config = {"agents": {"defaults": {"model": {"primary": "openai/main-fixture"}}}}
        self.manager = self.new_manager()

    def new_manager(self):
        return automation.Manager(self.state, self.config, "America/Toronto", self.native, now=lambda: self.now)

    def configure(self, **kwargs):
        return self.manager.configure_reminders(target=TARGET, **kwargs)

    def create(self, when="20m", text="Review the fixture presentation", request_key=None):
        return self.manager.create_reminder(when, text, request_key=request_key)

    def only_job(self):
        self.assertEqual(len(self.native.jobs), 1)
        return next(iter(self.native.jobs.values()))

    def test_reminders_are_disabled_until_an_explicit_connected_destination_is_configured(self):
        result = self.manager.list_reminders()
        self.assertFalse(result["configured"])
        self.assertEqual(result["reminders"], [])
        with self.assertRaisesRegex(automation.WorkflowError, "disabled"):
            self.create()
        self.assertEqual(self.native.jobs, {})
        self.assertEqual(self.native.calls, [])

    def test_configure_authorizes_destination_but_does_not_create_a_job(self):
        result = self.configure(time_zone="Europe/Paris")
        self.assertTrue(result["configured"])
        self.assertEqual(result["destination"], {"channel": "discord", "to": TARGET})
        self.assertEqual(result["timezone"], "Europe/Paris")
        self.assertEqual(self.native.jobs, {})
        self.assertEqual(self.native.calls, [("channels", "status", "--channel", "discord", "--json")])
        self.assertTrue(self.new_manager().list_reminders()["configured"])

    def test_one_shot_is_a_fixed_bounded_printf_command_without_any_model_invocation(self):
        original = deepcopy(self.config)
        self.configure()
        result = self.create()
        job = self.only_job()
        self.assertEqual(result["job_id"], job["id"])
        self.assertTrue(result["registered"])
        self.assertTrue(result["enabled"])
        self.assertFalse(result["delivered"])
        self.assertEqual(job["schedule"], {"kind": "at", "at": "2050-01-01T12:20:00.000Z"})
        self.assertEqual(job["payload"], {"kind": "command", "argv": ["/usr/bin/printf", "%s", "Reminder: Review the fixture presentation"],
                                          "toolsAllow": [], "timeoutSeconds": 30, "outputMaxBytes": 8192})
        self.assertFalse(job["deleteAfterRun"])
        self.assertEqual(job["sessionTarget"], "isolated")
        self.assertEqual(job["agentId"], "tari")
        self.assertEqual(job["delivery"], {"mode": "announce", "channel": "discord", "to": TARGET})
        self.assertEqual(self.config, original)
        self.assertIn("registration confirmed", result["registration_note"].lower())
        self.assertNotIn(("automations", "run", job["id"]), self.native.calls)

    def test_literal_shell_syntax_and_printf_formats_remain_data_in_actual_subprocess(self):
        self.configure()
        marker = self.directory / "must-not-be-created"
        text = f"Review $(touch {marker}); `touch {marker}`; %s %n; --help; 'quoted'"
        self.create(text=text)
        job = self.only_job()
        self.assertEqual(job["payload"]["argv"][:2], ["/usr/bin/printf", "%s"])
        completed = subprocess.run(job["payload"]["argv"], cwd=self.directory, capture_output=True,
                                   text=True, check=True, timeout=5)
        self.assertEqual(completed.stdout, "Reminder: " + text)
        self.assertEqual(completed.stderr, "")
        self.assertFalse(marker.exists())

    def test_saved_intent_precedes_add_and_actual_server_id_is_saved_before_enable(self):
        self.configure()
        observed = []
        def before_add(job):
            records = self.manager.settings()["reminders"]
            record = records[job["declarationKey"]]
            self.assertIsNone(record["job_id"])
            observed.append("intent_saved")
        def before_enable(job):
            record = self.manager.settings()["reminders"][job["declarationKey"]]
            self.assertEqual(record["job_id"], job["id"])
            self.assertFalse(job["enabled"])
            observed.append("native_id_saved")
        self.native.add_transform = before_add
        self.native.before_enable = before_enable
        self.create()
        self.assertEqual(observed, ["intent_saved", "native_id_saved"])
        self.assertEqual(self.manager.path.stat().st_mode & 0o777, 0o600)

    def test_failed_persistence_of_actual_id_cannot_enable_an_untracked_job(self):
        self.configure()
        original_save = self.manager.save
        def save_unless_registered(value):
            if any(record.get("job_id") for record in value["reminders"].values()):
                raise OSError("fixture disk failure")
            original_save(value)
        self.manager.save = save_unless_registered
        with self.assertRaises((OSError, automation.WorkflowError)):
            self.create()
        self.assertFalse(self.only_job()["enabled"])
        self.assertFalse(any(call[:2] == ("automations", "enable") for call in self.native.calls))

    def test_configured_timezone_resolves_local_time_and_new_destinations_do_not_retarget_existing_jobs(self):
        self.configure()
        first = self.create(when="2050-01-02T09:00", text="Original Toronto destination")
        original = deepcopy(self.native.jobs[first["job_id"]])
        self.manager.configure_reminders(target=CHANNEL_TARGET, time_zone="Europe/Paris")
        second = self.create(when="2050-01-02T09:00", text="New Paris destination")
        self.assertEqual(first["at"], "2050-01-02T14:00:00.000Z")
        self.assertEqual(second["at"], "2050-01-02T08:00:00.000Z")
        self.assertEqual(self.native.jobs[first["job_id"]], original)
        self.assertEqual(second["destination"]["to"], CHANNEL_TARGET)

    def test_same_request_key_is_idempotent_and_conflicting_reuse_is_refused(self):
        self.configure()
        first = self.create(request_key="fixture-request-1")
        self.now = datetime(2050, 1, 1, 12, 5, tzinfo=timezone.utc)
        second = self.create(request_key="fixture-request-1")
        self.assertTrue(second["already_registered"])
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["at"], second["at"])
        for arguments in ({"when": "30m"}, {"text": "Different fixture content"}):
            with self.subTest(arguments=arguments), self.assertRaises(automation.WorkflowError):
                self.create(request_key="fixture-request-1", **arguments)
        self.assertEqual(len(self.native.jobs), 1)

    def test_recent_duplicate_duration_does_not_shift_or_duplicate_the_original_reminder(self):
        self.configure()
        first = self.create()
        self.now = datetime(2050, 1, 1, 12, 0, 20, tzinfo=timezone.utc)
        second = self.create()
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["at"], second["at"])
        self.assertTrue(second["already_registered"])
        self.assertEqual(len(self.native.jobs), 1)

    def test_equivalent_absolute_time_forms_deduplicate_by_instant_text_and_destination(self):
        self.configure()
        first = self.create(when="2050-01-02T09:00-05:00")
        second = self.create(when="2050-01-02T14:00Z")
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertTrue(second["already_registered"])
        self.assertEqual(len(self.native.jobs), 1)

    def test_restart_preserves_actual_ids_and_native_cancellation_state(self):
        self.configure()
        first = self.create(request_key="persisted-fixture")
        self.manager = self.new_manager()
        rows = self.manager.list_reminders()["reminders"]
        self.assertEqual([row["job_id"] for row in rows], [first["job_id"]])
        self.assertTrue(rows[0]["enabled"])
        self.manager.cancel_reminder(first["job_id"])
        self.assertEqual(self.new_manager().list_reminders()["reminders"], [])
        rows = self.new_manager().list_reminders(history=True)["reminders"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["cancelled"])
        self.assertFalse(rows[0]["enabled"])

    def test_completed_failed_or_cancelled_duplicates_never_reenable_or_retry_delivery(self):
        self.configure()
        first = self.create(request_key="never-replay-fixture")
        job = self.only_job()
        self.now = datetime(2050, 1, 1, 12, 40, tzinfo=timezone.utc)
        for outcome in ({"lastRunStatus": "ok", "lastDelivered": True, "lastDeliveryStatus": "delivered"},
                        {"lastRunStatus": "ok", "lastDelivered": False, "lastDeliveryStatus": "unknown"},
                        {"lastRunStatus": "error", "lastDelivered": False, "lastDeliveryStatus": "failed"}):
            job["enabled"] = False
            job["state"] = outcome
            before = len(self.native.calls)
            result = self.create(request_key="never-replay-fixture")
            self.assertTrue(result["already_registered"])
            self.assertFalse(result["enabled"])
            self.assertEqual(result["job_id"], first["job_id"])
            self.assertFalse(any(call[:2] in (("automations", "add"), ("automations", "enable"), ("automations", "run"))
                                 for call in self.native.calls[before:]))
        self.manager.cancel_reminder(first["job_id"])
        self.assertFalse(self.create(request_key="never-replay-fixture")["enabled"])

    def test_missing_local_metadata_adopts_existing_declaration_without_replaying_it(self):
        self.configure()
        first = self.create(when="2050-01-02T14:00Z")
        job = self.only_job()
        job["enabled"] = False
        job["state"] = {"lastRunStatus": "ok", "lastDeliveryStatus": "unknown"}
        saved = self.manager.settings()
        saved["reminders"] = {}
        self.manager.save(saved)
        before = len(self.native.calls)
        result = self.new_manager().create_reminder("2050-01-02T14:00Z", "Review the fixture presentation")
        self.assertEqual(result["job_id"], first["job_id"])
        self.assertTrue(result["already_registered"])
        self.assertFalse(result["enabled"])
        self.assertFalse(any(call[:2] in (("automations", "add"), ("automations", "enable"))
                             for call in self.native.calls[before:]))

    def test_unknown_add_outcome_is_recovered_without_automatic_enable(self):
        self.configure()
        self.native.timeout_after_add = True
        with self.assertRaises(automation.WorkflowError):
            self.create(request_key="unknown-add-fixture")
        record = next(iter(self.manager.settings()["reminders"].values()))
        self.assertIsNone(record["job_id"])
        self.assertFalse(self.only_job()["enabled"])
        self.native.timeout_after_add = False
        before = len(self.native.calls)
        recovered = self.new_manager().create_reminder("20m", "Review the fixture presentation", "unknown-add-fixture")
        self.assertTrue(recovered["registered"])
        self.assertTrue(recovered["already_registered"])
        self.assertFalse(recovered["enabled"])
        self.assertEqual(len(self.native.jobs), 1)
        self.assertFalse(any(call[:2] in (("automations", "add"), ("automations", "enable"))
                             for call in self.native.calls[before:]))

    def test_restart_listing_persists_recovered_native_id_so_actual_id_cancellation_works(self):
        self.configure()
        self.native.timeout_after_add = True
        with self.assertRaises(automation.WorkflowError):
            self.create(request_key="list-recovers-fixture")
        actual_id = self.only_job()["id"]
        self.assertIsNone(next(iter(self.manager.settings()["reminders"].values()))["job_id"])
        self.native.timeout_after_add = False
        restarted = self.new_manager()
        listed = restarted.list_reminders(history=True)["reminders"]
        self.assertEqual([row["job_id"] for row in listed], [actual_id])
        recovered = next(iter(restarted.settings()["reminders"].values()))
        self.assertEqual(recovered["job_id"], actual_id)
        result = restarted.cancel_reminder(actual_id)
        self.assertTrue(result["cancelled"])
        self.assertTrue(result["future_runs_disabled"])
        self.assertFalse(self.native.jobs[actual_id]["enabled"])

    def test_unknown_add_with_no_visible_native_job_refuses_replay(self):
        self.configure()
        self.native.fail.add(("automations", "add"))
        with self.assertRaises(automation.WorkflowError):
            self.create(request_key="unknown-no-row-fixture")
        self.native.fail.clear()
        before = len(self.native.calls)
        with self.assertRaisesRegex(automation.WorkflowError, "unknown outcome"):
            self.create(request_key="unknown-no-row-fixture")
        self.assertEqual(self.native.jobs, {})
        self.assertFalse(any(call[:2] == ("automations", "add") for call in self.native.calls[before:]))

    def test_enable_timeout_is_not_replayed_and_listing_recovers_actual_native_state(self):
        self.configure()
        self.native.timeout_after_enable = True
        with self.assertRaises(automation.WorkflowError):
            self.create(request_key="unknown-enable-fixture")
        self.assertTrue(self.only_job()["enabled"])
        self.assertEqual(len(self.manager.list_reminders()["reminders"]), 1)
        before = len(self.native.calls)
        repeated = self.create(request_key="unknown-enable-fixture")
        self.assertTrue(repeated["already_registered"])
        self.assertTrue(repeated["enabled"])
        self.assertFalse(any(call[:2] == ("automations", "enable") for call in self.native.calls[before:]))

    def test_registration_that_finishes_after_due_time_is_left_disabled(self):
        self.configure()
        self.native.add_transform = lambda job: setattr(self, "now", datetime(2050, 1, 1, 12, 0, 2, tzinfo=timezone.utc))
        with self.assertRaises(automation.WorkflowError):
            self.create(when="2050-01-01T12:00:01Z")
        self.assertFalse(self.only_job()["enabled"])
        self.assertFalse(any(call[:2] == ("automations", "enable") for call in self.native.calls))

    def test_cancel_by_actual_id_exact_text_or_unique_id_prefix_retains_native_history(self):
        self.configure()
        first = self.create(text="First fixture task")
        second = self.create(text="Second fixture task")
        third = self.create(text="Third fixture task")
        for reference, job_id in ((first["job_id"], first["job_id"]), ("Second fixture task", second["job_id"]),
                                  (third["job_id"][:8], third["job_id"])):
            result = self.manager.cancel_reminder(reference)
            self.assertTrue(result["cancelled"])
            self.assertTrue(result["future_runs_disabled"])
            self.assertEqual(result["job_id"], job_id)
            self.assertFalse(self.native.jobs[job_id]["enabled"])
        self.assertEqual(len(self.native.jobs), 3)
        self.assertFalse(any(call[:2] == ("automations", "remove") for call in self.native.calls))

    def test_ambiguous_text_short_reference_or_unrelated_job_id_cannot_cancel_any_job(self):
        self.configure()
        self.create(when="20m", text="Shared fixture label")
        self.create(when="40m", text="Shared fixture label")
        unrelated_id = "d05c09dc-988e-4518-a7a6-df31a7f54c43"
        self.native.jobs[unrelated_id] = {"id": unrelated_id, "declarationKey": "someone-elses-job", "enabled": True}
        before = deepcopy(self.native.jobs)
        for reference in ("Shared fixture label", unrelated_id, "0000", "absent fixture"):
            with self.subTest(reference=reference), self.assertRaises(automation.WorkflowError):
                self.manager.cancel_reminder(reference)
        self.assertEqual(self.native.jobs, before)

    def test_cancel_running_job_discloses_that_delivery_cannot_be_recalled(self):
        self.configure()
        reminder = self.create()
        self.only_job()["state"] = {"runningAtMs": 2524652400000}
        result = self.manager.cancel_reminder(reminder["job_id"])
        self.assertTrue(result["future_runs_disabled"])
        self.assertIn("in-flight", result["delivery_note"])
        self.assertIn("cannot be recalled", result["delivery_note"])
        self.assertNotIn("delivered", result)

    def test_disable_failure_does_not_claim_cancellation(self):
        self.configure()
        reminder = self.create()
        self.native.ignore_disable = True
        with self.assertRaises(automation.WorkflowError):
            self.manager.cancel_reminder(reminder["job_id"])
        record = next(iter(self.manager.settings()["reminders"].values()))
        self.assertFalse(record.get("cancelled", False))
        self.assertTrue(self.only_job()["enabled"])

    def test_owned_job_can_still_be_revoked_after_its_native_payload_was_widened(self):
        self.configure()
        reminder = self.create()
        job = self.only_job()
        job["payload"] = {"kind": "agentTurn", "model": "unrequested/fixture", "toolsAllow": ["exec"]}
        result = self.manager.cancel_reminder(reminder["job_id"])
        self.assertTrue(result["future_runs_disabled"])
        self.assertFalse(job["enabled"])
        self.assertIn(job["id"], self.native.jobs)

    def test_missing_native_reminder_is_unverified_instead_of_silently_dropped(self):
        self.configure()
        reminder = self.create()
        self.native.jobs.clear()
        rows = self.manager.list_reminders()["reminders"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["registered"], "unverified")
        self.assertEqual(rows[0]["job_id"], reminder["job_id"])
        with self.assertRaises(automation.WorkflowError):
            self.manager.cancel_reminder(reminder["job_id"])

    def test_retained_run_history_distinguishes_execution_from_delivery_without_raw_details(self):
        self.configure()
        reminder = self.create()
        job = self.only_job()
        job["enabled"] = False
        job["state"] = {"lastRunStatus": "ok", "lastDelivered": False, "lastDeliveryStatus": "unknown"}
        self.native.runs[job["id"]] = [{"runId": "fixture-reminder-run", "status": "ok", "deliveryStatus": "unknown",
                                         "delivered": False, "summary": "private fixture text", "error": "sensitive fixture"}]
        self.assertEqual(self.manager.list_reminders()["reminders"], [])
        rows = self.manager.list_reminders(history=True)["reminders"]
        self.assertEqual(rows[0]["state"]["lastDeliveryStatus"], "unknown")
        self.assertFalse(rows[0]["state"]["lastDelivered"])
        history = self.manager.reminder_history(reminder["job_id"])
        self.assertEqual(history["entries"], [{"runId": "fixture-reminder-run", "status": "ok", "deliveryStatus": "unknown", "delivered": False}])
        self.assertNotIn("sensitive fixture", json.dumps(history))
        self.assertNotIn("private fixture", json.dumps(history))

    def test_widened_or_nondeterministic_native_payload_is_left_disabled(self):
        changes = [
            lambda job: job["payload"].update(kind="agentTurn"),
            lambda job: job["payload"].update(argv=["sh", "-c", "printf fixture"]),
            lambda job: job["payload"].update(model="openai/unrequested"),
            lambda job: job["payload"].update(thinking="high"),
            lambda job: job["payload"].update(fallbacks=[]),
            lambda job: job["payload"].update(env={"FIXTURE": "unsafe-extra-authority"}),
            lambda job: job["payload"].update(cwd="/"),
            lambda job: job["payload"].update(input="unrequested stdin"),
            lambda job: job["payload"].update(toolsAllow=["exec"]),
            lambda job: job["payload"].update(timeoutSeconds=0),
            lambda job: job["payload"].update(outputMaxBytes=65536),
            lambda job: job.update(deleteAfterRun=True),
            lambda job: job.update(sessionTarget="main"),
            lambda job: job["delivery"].update(to=CHANNEL_TARGET),
            lambda job: job["delivery"].update(bestEffort=True),
        ]
        self.configure()
        for index, change in enumerate(changes):
            self.native.add_transform = change
            with self.subTest(index=index), self.assertRaises(automation.WorkflowError):
                self.create(text=f"Contract fixture {index}")
        self.assertTrue(all(job["enabled"] is False for job in self.native.jobs.values()))
        self.assertFalse(any(call[:2] == ("automations", "enable") for call in self.native.calls))

    def test_invalid_text_credentials_and_unresolved_time_never_create_native_jobs(self):
        self.configure()
        cases = ("", "  ", "x" * 1501, "fixture\x00content", "password=fixture-secret",
                 "verification code is 123456", "https://example.invalid/reset-password?token=fixture")
        for text in cases:
            with self.subTest(text=text), self.assertRaises(automation.WorkflowError):
                self.create(text=text)
        for when in ("tomorrow morning", "4 PM", "2050-03-13T02:30", "2050-11-06T01:30"):
            with self.subTest(when=when), self.assertRaises(automation.WorkflowError):
                self.create(when=when)
        self.assertEqual(self.native.jobs, {})

    def test_invalid_request_identity_never_registers_a_job(self):
        self.configure()
        for key in ("", "../fixture", "fixture\nkey", "x" * 81, 7):
            with self.subTest(key=key), self.assertRaises(automation.WorkflowError):
                self.create(request_key=key)
        self.assertEqual(self.native.jobs, {})

    def test_native_offline_and_malformed_inventory_never_claim_registration(self):
        self.configure()
        self.native.fail.add(("channels", "status"))
        with self.assertRaises(automation.WorkflowError):
            self.create()
        self.native.fail.clear()
        for transform in (lambda jobs: {"jobs": None}, lambda jobs: {"jobs": ["malformed"]}, lambda jobs: {"unexpected": []}):
            self.native.list_transform = transform
            with self.subTest(transform=transform), self.assertRaises(automation.WorkflowError):
                self.create()
        self.assertEqual(self.native.jobs, {})
        self.assertEqual(self.manager.settings()["reminders"], {})

    def test_duplicate_native_declarations_require_review_without_modifying_unrelated_jobs(self):
        self.configure()
        first = self.create()
        copied = deepcopy(self.only_job())
        copied["id"] = "afe28b1a-6109-4a4a-87ee-a67bf4ba6669"
        self.native.jobs[copied["id"]] = copied
        saved = self.manager.settings()
        saved["reminders"] = {}
        self.manager.save(saved)
        before = deepcopy(self.native.jobs)
        with self.assertRaises(automation.WorkflowError):
            self.create()
        self.assertEqual(self.native.jobs, before)
        self.assertIn(first["job_id"], self.native.jobs)


if __name__ == "__main__":
    unittest.main()
