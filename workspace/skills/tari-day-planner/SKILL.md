---
name: tari-day-planner
description: "Manage Tari's opt-in local day planner, time blocks, commute blocks, and persistent checklist through bin/day-planner."
user-invocable: false
---

# Day planner

Use this skill when the user clearly names their planner or checklist, or continues
an unmistakable planner operation. An ordinary request for planning advice does
not authorize changes. Words such as “urgent”, “schedule”, or “tomorrow” alone do
not select the planner. Ask when the intended target is unclear.

Run `./bin/day-planner` for the full command reference. Use this helper for every
planner read and mutation. Never edit planner Markdown by hand, reconstruct a
planner from memory, or substitute a Calendar summary when the helper fails.

The view has three separate kinds of entries:

- Calendar entries come from the user's primary calendar and accepted shared
  invitations, read live through `tari-google`. They provide schedule context;
  they do not establish deadlines or prove that work is complete.
- Planner blocks are local time blocks that never create Calendar events.
- Commute blocks are local travel time that occupies time like a Planner block.

The categorized checklist persists across dates. Each task has a completion flag
and an independent optional status: Priority, Start, or In-progress. The default
public checklist is empty; sections belong to the user.

## Commands

```sh
./bin/day-planner show today
./bin/day-planner show tomorrow --schedule
./bin/day-planner show tomorrow --json
./bin/day-planner free tomorrow --after 12:00 --before 18:00 --min 90m
./bin/day-planner add tomorrow 13:00-14:00 "Project work"
./bin/day-planner place tomorrow 90m "Write draft" --after 14:00
./bin/day-planner move tomorrow "Project work" 16:00-17:00
./bin/day-planner move today b2 --to-day tomorrow
./bin/day-planner resize tomorrow b2 2h
./bin/day-planner rename tomorrow b2 "Review draft"
./bin/day-planner remove tomorrow @16:00
./bin/day-planner commute-add tomorrow to "Team meeting" --duration 45m
./bin/day-planner commute-add tomorrow home "Team meeting" --duration 45m

./bin/day-planner checklist --status priority
./bin/day-planner section-add "Personal"
./bin/day-planner task-add "Personal" "Book appointment" --status priority
./bin/day-planner task-status "Book appointment" in-progress
./bin/day-planner task-complete "Book appointment"
./bin/day-planner task-reopen "Book appointment"
./bin/day-planner task-rename "Book appointment" "Book annual appointment"
./bin/day-planner task-remove "Book annual appointment"
```

Resolve natural language to explicit dates and unambiguous times using
`TARI_TIMEZONE`. A bare `1` is refused; use `13:00` or `1pm`. Resolve a reference
with the id from `show --json` or `checklist --json` when several titles match.
Blocks stay within one day. A commute `to` an event ends at its start; a commute
`home` begins at its end. Use `--keep-end` when resizing an outbound commute.

`task-add` fails when a section does not exist. Pass `--create` only if the user
clearly intends to create that section. Never turn a suggested schedule into
saved blocks or suggested priorities into checklist changes without authorization.

## Calendar checks and overlap confirmation

The helper reads Calendar commitments before adding, placing, moving, resizing,
or creating commute blocks. It has no Calendar write command. Requests to create
or modify a Calendar event belong to the separate calendar skill and its
confirmation process.

An overlap returns exit 4 and writes nothing. Explain the specific conflicting
items and obtain the user's confirmation before repeating with
`--allow-overlap`. Never pass that flag preemptively. A confirmation covers that
specific overlap, not unrelated future conflicts.

An unavailable or incomplete Calendar returns exit 3 for a mutation and writes
nothing. Tell the user the check failed. Use `--no-calendar` only after the user
explicitly agrees to proceed without checking Calendar. Read-only views can show
local blocks with a visible Calendar warning; never describe the resulting free
slots as verified availability.

The user's own events count unless the user declined. Shared events count only
when the user's attendee response is accepted. Tentative, unanswered, or unknown
shared invitations and other shared-calendar events remain context and do not
block time unless the user explicitly includes one with `--count`. All-day
events are shown but do not block time. Transparent events are shown without
blocking time.

Only when the user asks about a missing event or explicitly says a shared event
involves them, run `./bin/day-planner excluded DAY`. This is diagnostic output;
do not relay unrelated people's event titles or calendar identities. Use
`--count EVENT_ID` on subsequent commands to include that specific event.

## Responses and state

Relay the helper's planner view in its existing order. After a mutation, give a
short confirmation; show the full view when asked. If it fails, report the failure
without inventing a replacement view. Translate backend errors into a short,
useful explanation; do not paste raw stderr or identifiers into normal replies.

State is local Markdown under `planner/`. It contains no Calendar cache. Planner
blocks are plans, not externally confirmed commitments, and do not automatically
create reminders or scheduled jobs. The optional `TARI_PLANNER_DIR` override is
for a separately isolated local state directory, including tests.
