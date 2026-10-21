---
name: tari-proactive
description: On-demand briefs and restrained notifications; background work is disabled unless explicitly configured and requested.
user-invocable: false
---

`AGENTS.md` always applies. Read the Gmail skill and retrieval reference before
mail retrieval, and the Calendar skill before calendar reads or writes.

## Default: direct help, no background jobs

This installation ships with no enabled recurring jobs, polling, morning brief,
context refresh, posting channel, or reminder service. `HEARTBEAT.md` contains no
tasks. Do not infer that a job exists from this skill or claim a reminder has been
scheduled when no tool confirmed one.

Answer direct requests normally at all times. "Give me a morning brief" requests
a brief now unless the user explicitly asks for recurring delivery. "What should
I prepare for tomorrow?" is planning advice, not authorization to schedule a job.

## An inexpensive on-demand brief

Use the runtime's current date and configured user timezone. Read relevant domain
state for active tasks, deadlines, commitments, follow-ups, and conflicts. Consult
a bounded calendar window, distinguishing the user's commitments from shared
context. Triage recent Gmail metadata when relevant; open only consequential
messages. State the actual mail scope and any pagination or retrieval limit.

Use the current conversation's already available facts rather than loading every
memory file. Do not invoke another model, a digest run, a backlog sweep, or repeated
queries just to make the brief look substantial. If a requested complete mail
recap needs more retrieval, follow its procedure and disclose failure or limits.

Lead with what materially needs attention: imminent deadlines needing preparation,
schedule/location changes, unresolved source conflicts, decisions, and useful
next actions. A quiet day may need only a couple of lines. No fixed template,
invented urgency, or calendar events created as a side effect.

## If the user explicitly requests automation

Determine the requested delivery surface, time, timezone, recurrence/end condition,
and permitted data sources. Check whether this installation has a supported
scheduler. If it does not, explain that limitation and offer the concrete setup
work separately; do not claim the job exists or modify another installation.

Inspect existing configuration and preserve unrelated jobs. Never add automation
by copying a private production schedule, enabling a heartbeat by implication, or
editing arbitrary host configuration. A deliberate setup must have isolated state,
bounded tools and retries, clear cost limits, and a way to inspect and disable it.
Keep credentials out of prompts, scheduler output, and notification content.

Simple timed reminders should use deterministic delivery when available, without
a model call at delivery. A timed request requiring fresh reasoning needs an
explicitly configured one-shot agent job. Never promise either without a confirmed
supported tool result.

## Notification restraint

Volume spends the user's attention. Do not interrupt merely because a check ran,
time passed, a calendar entry exists, mail arrived, or something seems interesting.
Notify only when a material change is actionable and the enabled job authorizes
that notification. If there is nothing worth saying during an enabled background
check, return exactly `NO_REPLY`.

Before notifying, consult today's daily note and the job's own durable suppression
record, if configured. Record what was surfaced after confirmed delivery. Do not
repeat an unchanged item because it remains unread or incomplete. Repeat only for
new information, a materially nearer deadline, a changed schedule, or a sensible
reminder point the user requested. Stop reminders after acknowledgement/completion
unless a new fact warrants attention. Never invent status or urgency.

An unavailable suppression record or failed source read must not become a false
all-clear. Expose material uncertainty in a direct response or the enabled job's
health output; do not send repeated speculative notifications.

## Authentication failures

Report an actual failed integration when it affects the user's task. Use this
installation's documented auth status/setup helper if available. Never infer token
expiry from a production account's testing status, apply a fixed seven-day rule,
or ask the user to paste credentials into chat. Do not automatically expand scopes.
