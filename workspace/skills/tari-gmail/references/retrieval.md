# Gmail retrieval: bounded work and honest completeness

Read the parent skill first. Gmail remains read-only throughout. Read state is a
hint, never evidence that an item was handled or seen. These defaults are generic;
no production account's mailbox behavior or backlog has been imported.

## Choose the scope before searching

Honor an explicit user window, sender, category, or query. For an ordinary recent
catch-up, start with the last day regardless of read state, excluding Promotions
and Social. Do not silently restrict to Gmail's Primary category: consequential
messages can be classified as Updates or other categories.

Use metadata first and start with a small page (for example 20). Open bodies only
for plausible actions, deadlines, changes, or requested detail. A quick on-demand
brief may deliberately stop at a bounded page, but must then say it covered that
bounded set and may omit other mail. It must never claim full inbox completeness.

An older-unread sweep is optional, not part of every brief. When useful, search
`is:unread older_than:1d -category:promotions -category:social` separately with a
small page. Explicitly call it a sample if not fully paginated. Do not reread the
entire backlog on each run or use unread-only searches for recent catch-ups.

## When the user asks for a complete recap

1. **Define the exact scope.** State its window and exclusions. If the user asks
   for all mail, include the categories the usual default would exclude.
2. **Query wide, filter precisely.** Relative Gmail operators are useful for a
   broad search. For an exact local day, use a wider window and compare each
   message's internal timestamp with explicit start/end instants in the user's
   timezone. Header `Date` is sender-supplied; Gmail date strings alone do not
   establish a local-day boundary. Account for daylight-saving offsets.
3. **Follow page tokens.** `--max` is a page size or limit, not a completeness
   claim. Inspect returned `nextPageToken`/pagination metadata and pass the next
   token using `--page` (upstream pagination is retained inside the wrapper's
   `data` object). Continue until exhausted, or disclose the precise limit.
   A wrapper error is not an empty page.
4. **Separate unread inventory when requested.** If asked about unread mail,
   retrieve that inventory in its own query; it may include messages outside the
   recent window. A recent-mail request alone does not authorize claiming that
   every older unread item has been checked.
5. **Merge and deduplicate by message ID.** A thread can contain multiple messages;
   account for the actual message results and do not count one message twice.
6. **Triage before opening.** Summarise routine messages from reliable metadata
   when sufficient; read bodies before asserting specific requested actions or
   deadlines. Group routine messages with counts. Every message inside the
   declared complete scope must appear individually or in a group.
7. **Disclose failures and limits.** A rate limit, failed body read, unknown
   timestamp, pagination cap, or truncated tool response means uncertainty.
   Explain which scope was covered and what remains unchecked. Stop rather than
   retry in a loop or hide missing results behind "nothing important".

## Cost, repetition, and targeted checks

Registration/RSVP checks should use narrow queries for the particular event, then
read the relevant confirmation. A generic invitation or unread label does not
prove registration or attendance. Avoid huge OR queries and broad body downloads.

For repeated explicitly enabled checks, preserve a small durable record of message
IDs already surfaced or judged routine, plus date and reason, in installation-local
state. Daily notes can record what was surfaced today. A cross-day ledger is needed
for backlog suppression; a daily note alone resets at midnight. Do not create a
second mailbox archive. Bound any ledger and store no bodies or credentials.

An item is worth repeating only after a material change: a reply, nearer actionable
deadline, changed schedule, or user-requested follow-up. "Still unread" is not a
change. When no repeated checks are enabled, no backlog ledger is needed.
