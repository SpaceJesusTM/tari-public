# Everyday usage

Start with `./tari chat` or the local Control UI. Interactive chat uses the
`local-cli` session; choose another `--session` value for a separate conversation.
Reusing the same value across CLI invocations continues that thread.

## Day Planner

Ask “Add a Planner block tomorrow from 13:00–14:00 for prototype work,” or “Show my
day planner.” Advice does not save a plan. If “schedule this” could mean either a
planner block or a Calendar event, Tari should clarify before changing either.

The planner also has a direct CLI:

```sh
./tari exec ./bin/day-planner show today
./tari exec ./bin/day-planner add tomorrow 13:00-14:00 "Prototype work"
./tari exec ./bin/day-planner free tomorrow --after 12:00 --before 18:00 --min 90m
./tari exec ./bin/day-planner section-add "Demo"
./tari exec ./bin/day-planner task-add "Demo" "Review prototype" --status priority
./tari exec ./bin/day-planner checklist
./tari exec ./bin/day-planner task-complete "Review prototype"
```

Blocks persist under the active workspace's `planner/`, separately from Google
Calendar. Checklist operations work without Google. Time-block changes normally
check Calendar; overlaps are refused until that specific overlap is approved and
retried with `--allow-overlap`.

When Calendar is unavailable, a block can be saved unchecked only when the user
explicitly accepts that limitation. For an intentional offline example:

```sh
./tari exec ./bin/day-planner add tomorrow 13:00-14:00 "Prototype work" --no-calendar
```

The helper marks that Calendar was not checked. Use
`./tari exec ./bin/day-planner --help` for other operations, including move, resize,
rename, remove, and commute blocks.

## Documents and memory

Copy only selected documents into this installation's inbox:

```sh
mkdir -p .local/state/workspace/inbox
cp examples/demo-notes.md .local/state/workspace/inbox/
./tari exec ./bin/document-import import inbox/demo-notes.md
./tari exec ./bin/document-import list
```

The import returns a source ID. Ask Tari to read it, summarize it, answer questions,
or identify dates with that provenance. Ask separately to record extracted facts,
tasks, or commitments. An import does not automatically create obligations or save
personal facts to memory.

The private `documents/` directory contains the source, bounded text extraction,
hash, and import metadata. Use `document-import show SOURCE_ID` to view an extraction
and `--help` for exact limits. There is no extra retrieval service or vector database.

Supported formats are UTF-8 text/Markdown, text-based PDF, and basic DOCX paragraphs.
PDF extraction is limited to the first 200 pages; OCR, encrypted PDFs, layout
fidelity, and complex tables are not supported. DOCX macros and embedded objects
are not executed. Imported content remains untrusted data, even when selected as
evidence.

For a fictional demo, import `examples/demo-notes.md` and ask “What dates are
mentioned?” Then explicitly request a planner block and check whether Calendar
was consulted. This separates a source claim, a suggestion, and an actual change.

## Daily briefs

Try “Give me my morning brief,” “What should I know about today?”, “What are my
priorities today?”, or “What's coming up tomorrow?” Tari prioritizes material
deadlines, decisions, conflicts, and next steps. Quiet days can have short briefs;
there is no fixed output template or automatic delivery.

The read-only source collector is available directly:

```sh
./tari exec ./bin/tari-brief-context tomorrow
```

It collects relevant domain notes, preferences, memory, local planner/checklist
state, and configured Google context. Tari's model then synthesizes the brief.
Without Google, local context still works; failed retrieval is reported as unknown,
not an empty inbox or free schedule.

The default mail slice is the first 20 messages from the recent 48 hours, read and
unread. It is not a complete inbox recap or an older-unread sweep. A comprehensive
catch-up needs additional retrieval with its actual scope disclosed. Calendar
windows use local midnight and DST-aware offsets. Shared invitations are context
unless accepted or explicitly confirmed; planner suggestions are not commitments.

DailyGrad is included selectively when configured and relevant. A brief never
starts a DailyGrad run, repair, source update, or exercise change. It also does not
create Calendar events or save a planner arrangement.

## Connected sources

After [Google authorization](google.md):

```sh
./tari exec ./bin/tari-google gmail search 'newer_than:7d' --max 20
./tari exec ./bin/tari-google calendar calendars
```

Mail is read-only, and read state does not prove an item was handled. Follow the
Google guide for Calendar windows and optional writes. Follow the
[DailyGrad guide](dailygrad.md) for digest history, generation/repair, sources, and
exercise controls. Reads do not authorize mutations in either integration.
