# Optional morning briefs and reminders

Fresh installations have no scheduled brief or reminder jobs. These features use
OpenClaw's native scheduler and delivery; Tari adds fixed-purpose commands rather
than another scheduler. Heartbeat settings remain unchanged.

Fresh v0.1 installations already load the workflow tools. Installations created
before v0.1 need them installed, plus the updated workspace instructions:

```sh
./tari automation setup
./tari workspace-update --instructions
./tari start
```

These commands update this installation's image/configuration and instructions;
they create no jobs. Run them deliberately. Read-only on-demand briefs use the
same bounded context collector as scheduled briefs.

Connect Discord using [native Discord setup](discord.md) first. Native pairing,
allowlists, guild/channel admission and owner permissions still apply; there is
no additional DM-only chat policy. Scheduled delivery requires an explicit
`user:USER_ID` or `channel:CHANNEL_ID` destination. Replace the placeholders below
with actual numeric IDs. For a named bot account, add `--account ACCOUNT_ID` to
`morning-brief enable` or `reminders configure`; omission selects the native
default account. Registration requires a reachable Gateway and that account's
configured, running, connected status. Connectivity alone does not prove delivery
to the intended destination.

Configure model authentication separately through [model setup](setup.md#model-authentication)
and the [native model CLI](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/cli/models.md).
A login does not guarantee access to a particular model. To explicitly enable a
daily brief, choose its local time, IANA timezone, model and destination:

```sh
./tari morning-brief enable \
  --time 09:00 --timezone America/Toronto \
  --model openai/gpt-6.1-sol --thinking medium \
  --to 'channel:YOUR_CHANNEL_ID'
./tari morning-brief status
./tari morning-brief history
./tari morning-brief disable
```

Repeating `enable` updates the same managed daily job, including an intentional
change of time, timezone, model or destination; it does not create another daily
brief. It also re-enables a stopped job. This registers future execution without
an immediate test run. The defaults are `09:00`, the installation timezone,
`openai/gpt-6.1-sol`, and `medium` thinking. The model and thinking level are
stored on the native job, independent of the model used for ordinary chat. The
job has an empty fallback chain: an unavailable model is reported rather than
silently promoting to another model.

Each morning run is isolated, has a 300-second budget and permits only
`tari_brief_context` and, when installed and allowed, `dailygrad_digest`. It reads
planner/checklist context, relevant domain/preferences notes and configured
Calendar/Gmail information. Curated `MEMORY.md` and Gmail bodies/previews are
omitted; Gmail is reduced to bounded sender/subject/date metadata. Missing,
partial, stale or degraded sources must be disclosed. DailyGrad reads an existing
digest only: the brief cannot generate or repair one, change sources/exercises,
write planner/memory/Calendar state, administer jobs or send messages itself.
Native announcement delivery sends the final result.

Reminders require a separate, explicit destination configuration, which creates
no jobs. A later configuration change applies to new reminders; existing reminders
retain their original destination.

```sh
./tari reminders configure --to 'user:YOUR_USER_ID' --timezone America/Toronto
./tari reminders create 20m 'Take a short walk' --request-key walk-request-1
./tari reminders create '2026-12-15T09:00:00-05:00' 'Review the agenda'
./tari reminders list
./tari reminders list --history
./tari reminders history JOB_ID
./tari reminders cancel JOB_ID
```

`WHEN` accepts positive durations such as `20m`, `2h` or `1d`, or an ISO date/time
within the next 366 days. Offset-free ISO input uses the configured reminder
timezone, or the installation timezone when no override is configured. Ambiguous
or nonexistent times at daylight-saving transitions are rejected; use a valid
time with an explicit UTC offset. Reminder text is limited to 1–1500 characters.
Credentials, authentication/recovery codes and security links are unsuitable
reminder content and clear matches are rejected.

Use a stable `--request-key` only for the same exact request. Reusing it with
different text, time input or destination is rejected; an unknown result must be
inspected before retrying. Cancellation/history accept an actual job ID, a unique
ID prefix of at least eight characters, or exact unambiguous reminder text. Prefer
the ID returned by `list` when several reminders have similar text.

Each reminder stores a native one-shot command with fixed argv
`["/usr/bin/printf", "%s", "Reminder: " + text]`, no shell evaluation, no model or
agent tools, a 30-second execution budget and bounded output. Scheduled reminder
delivery uses no LLM. Asking Tari in chat to register a reminder can still use the
normal conversational model. Chat reminder create/list/cancel tools require native
owner authorization; approving another Discord participant for ordinary chat does
not grant ownership. The caller cannot redirect a reminder away from the
owner-configured destination or supply another executable, arguments or credentials.

A registration acknowledgement is not a delivery receipt. Check native history's
`completionStatus`, `deliveryStatus` and `delivered`, alongside execution `status`;
execution can succeed while required delivery fails or remains unknown. Status and
list results also expose safe state/error indicators. A backend error or
`registered: "unverified"` means the outcome is unverified, not that the job is
absent or completed. Inspect status/history before another mutation; do not blindly
retry an uncertain registration. The adapter retains up to 500 reminder
registration records and refuses further creation at that limit for owner review.
History commands return up to ten recent run metadata entries without raw backend
errors or generated output.

The Gateway must be running for execution. Native persisted jobs and history
survive restarts, missed recurring ticks are coalesced, and an overdue or interrupted
one-shot can execute late during recovery. This does not guarantee every missed
morning brief is replayed or that reminders arrive at the exact requested instant.
Native announcement delivery retries known transient failures only while no payload
may have reached the recipient. Partial/ambiguous sends are not automatically
replayed. A completed one-shot with failed or unknown required delivery remains
disabled for inspection; its payload is not automatically run again. Successful
reminders also become disabled, with `keep-after-run` retaining their job/history.
There is no absolute exactly-once guarantee across interruptions. Disabling a brief
or cancelling a reminder prevents future execution; an in-flight delivery may still
finish, and an already posted message cannot be recalled by cancellation.

Brief filtering and the Discord outbound text hook block high-confidence known
credentials/authentication content as defense in depth. They are not complete DLP:
novel or obscured secrets can escape detection, attachment bytes are not inspected,
and native outbound hooks can fail open after errors/timeouts. Choose a destination
appropriate for the underlying sources. Normal Discord admission does not make
private tool results appropriate for every shared channel.

Real bot login, authorized/unauthorized access, model inference and delivery still
need deliberate owner acceptance. Automated fixtures do not establish those live
outcomes. Review the pinned native documentation for [automation lifecycle and
restart recovery](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/automation/cron-jobs/how-it-works.md),
[delivery outcomes](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/automation/cron-jobs/delivery.md)
and [payload/tool policies](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/automation/cron-jobs/payloads.md).
