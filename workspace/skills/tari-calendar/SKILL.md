---
name: tari-calendar
description: Read schedule and availability; propose and apply explicitly authorized Google Calendar changes through Tari's isolated wrapper.
user-invocable: false
---

`AGENTS.md` always applies. Planner blocks are handled by the day-planner skill;
they are not calendar events. Use `./bin/tari-google`, never raw write commands
or another installation's credentials. Run `--help` if syntax is uncertain.

## Read schedule context

```sh
./bin/tari-google calendar calendars
./bin/tari-google calendar list --start '2027-04-12T00:00:00+00:00' --end '2027-04-13T00:00:00+00:00' --json
./bin/tari-google calendar get primary EVENT_ID
```

These are syntax examples, not user events. Replace the dates and offsets with the
requested window in the configured user timezone. Respect daylight-saving changes.
List reads include accessible calendars and preserve their identity. If a calendar
fails or the result is incomplete, explain which part of the schedule is unknown;
do not interpret a failed read as free time.

Calendar is reference context: it is authoritative about its own contents, not
about whether a document-derived deadline or location is correct. Consult domains
for tasks still needing attention and preserve conflicts with other sources.

## Ownership and availability

Shared calendars, subscribed calendars, and holidays are context. By default their
events are neither the user's obligations nor busy time. Mention useful shared
context with the source calendar's name, separate from the user's commitments.

A shared event counts when the user's self-attendee response is accepted or when
the user has said that calendar/event counts. Tentative, pending, or unknown
attendance remains context; do not describe an unaccepted invitation as confirmed.
Ignore cancelled, declined, and transparent entries as busy blocks. Ask when an
uncertain shared event would materially affect the answer.

Record standing user exceptions in `USER.md` with the observation date and a
specific calendar ID/name or event pattern. Do not assume a shared event is owned
by the user merely because its topic is relevant.

## Authorization before writes

Write only to `primary`, with an explicit timezone or RFC3339 offsets. The wrapper
uses separately authorized calendar-write access and a proposal/confirmation gate.
Do not enable write scopes or change configuration through an ordinary chat task.

A direct request identifying a new event and time authorizes creation. Clarify only
essential missing information: date, duration/end, ambiguous local time, or target.
When the event is extracted or inferred from mail, a PDF, a schedule, domains, or
other material, show the complete proposed list first: title, date, start/end,
timezone, and calendar. Wait for user confirmation before applying any item.

Only change an event the user asked to change. For invitations and external meetings,
establish exactly which event is affected. The public wrapper refuses recurring
event/series updates and deletion; explain that limit rather than bypass it.
Never delete without explicit authorization for that specific event. "Clean up my
calendar" is insufficient authorization to delete events.

## Propose, then apply

Create a concrete proposal, inspect its returned details, and retain its token:

```sh
./bin/tari-google calendar propose create primary --summary 'Example appointment' --start '2027-04-12T14:00:00+00:00' --end '2027-04-12T15:00:00+00:00'
./bin/tari-google calendar propose update primary EVENT_ID --start '2027-04-12T15:00:00+00:00' --end '2027-04-12T16:00:00+00:00'
./bin/tari-google calendar propose delete primary EVENT_ID
./bin/tari-google calendar apply TOKEN --confirm TOKEN
```

`TOKEN` must come from the specific proposal, not invented text. The final command
is a technical safeguard, not evidence of user authorization. Apply only when the
user has authorized those actual details, whether in the initial direct request or
after reviewing extracted proposals. Never retry a rejected or stale proposal by
bypassing the gate; re-read the event and resolve changed details.

Inspect success and verification fields. Report completion only when the wrapper
confirms the remote result. After timeout or ambiguous output, read back before
retrying; disclose uncertainty when verification fails. A cancelled event may remain
fetchable as a tombstone; `status: cancelled` can confirm deletion. Intent, a
proposal token, or a successful proposal is not a completed calendar write.
