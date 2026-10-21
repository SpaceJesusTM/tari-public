# Historical v0 MVP notes

> **Historical record.** These notes describe the first MVP and are kept for
> provenance. Several statements are no longer current: v0.1 adds the DailyGrad
> read-only adapter and optional runner, optional native Discord, opt-in scheduled
> morning briefs, and deterministic reminders, and its test counts differ. For
> current functionality and verification, see [v0.1 readiness](readiness.md), the
> [README](../README.md), and [briefs & reminders](automation.md).

The candidate is an independently initialized public codebase. The existing MIT
license and independent public Git history are preserved. Only selected tracked
source instructions and the standalone planner were reviewed from private Tari;
the private checkout was never executed or changed. Personal profiles, memory,
credentials, runtime files, databases, and untracked work were not imported.

## Keep, adapt, omit

| Component | Decision |
| --- | --- |
| Tari identity, soul, source precedence | Retain tone and behavioural hierarchy; remove owner-specific examples |
| Preferences, memory, active domain state | Empty public templates with provenance and explicit document-persistence consent |
| Mature standalone day-planner | Retain Markdown state, checklist, commute, free time, overlap checks, and Calendar failure protections |
| Calendar workflow | Distinguish primary/confirmed shared participation from context; separate owned-event write credentials; proposal confirmation and remote verification |
| Gmail workflow | Preserve useful retrieval/triage rules, bounded pagination and scope honesty; read-only OAuth and command allowlist |
| Source intake | Local inbox plus bounded TXT/MD/PDF/DOCX import, source hash, timestamp, and explicit untrusted-content markers |
| Deployment | One thin image over pinned prebuilt OpenClaw; one Compose service; isolated private state |
| Proactive work | On-demand briefing skill; no unattended jobs or reminder delivery installed |
| DailyGrad | Optional document export/import now; independent read-only adapter deferred |
| Production infrastructure | Omit dashboards, Discord routing, Bible/personal planner presentation, migrations, backup tooling, incident history, evaluation framework, remote workers, and runner scheduling |

Dependencies: Docker/Compose, host Python 3.10+, upstream OpenClaw 2026.9.9,
gog 0.40.0, container Python 3, and Poppler `pdftotext`. No upstream source build,
new agent framework, custom HTTP service, vector database, or host `gog` is needed.
Check upstream release notes before advancing either compatibility pin.

## Verification performed

- Built the image on macOS/Apple Silicon; verified the gog archive against the
  official release checksum. Both arm64 and amd64 checksums are present; amd64
  execution was not tested on this machine.
- Validated generated configuration with the pinned OpenClaw CLI.
- Started an isolated public Gateway. Health returned 200; `/v1/models` returned
  401 without credentials and 200 with its generated token, including `openclaw/tari`.
- Exercised the actual container planner: a Calendar-unchecked block, persistent
  task creation/completion, and day rendering with explicit unavailable-Calendar
  disclosure. Tested missing Google configuration as a graceful failure.
- Imported fictional Markdown, basic DOCX, and a text-based PDF through the actual
  container helpers, recording provenance without writing assistant memory.
- Checked pinned gog flags offline using its actual CLI help; reviewed upstream
  JSON envelopes, pagination, OAuth scope selection, and named-client behavior.
- Ran **59 passing tests** in the standard-library unit suite with isolated state inside this checkout.
  It covers planner operations and consent/conflict handling; Google allowlists,
  pagination, proposal locking, stale updates, and verified writes; document bounds,
  path/symlink protections, and provenance; idempotent initialization and local
  chat request/session authentication. Google subprocess results are mocked.
- Scanned the public candidate for credential patterns, private infrastructure
  references, non-example account addresses, unapproved paths, symlinks, and
  generated artifacts. Also reviewed behavioral/documentation consistency.

The smoke container was stopped after verification. Generated demonstration
documents/planner data were removed, and a clean private template workspace was
initialized for the handoff. No real account or model credentials are configured.

No real provider key or Google account was used. Live model replies, actual
multi-turn model recall, embedding-backed memory, OAuth consent/refresh, and real
Gmail/Calendar responses still need owner verification. The HTTP/session contract
and request construction are checked; a healthy Gateway is not a verified LLM
conversation. Google mocks do not prove a particular account has the right scopes.
Linux/WSL support and an amd64 build remain host-specific verification tasks.

## Manual acceptance after adding your credentials

1. Run `./tari init --timezone YOUR_IANA_TIMEZONE` on a fresh checkout. Add only
   your provider key to `.local/runtime.env`, then `./tari start` and `./tari doctor`.
2. Run `./tari chat --session acceptance "Please remember that my demo colour is blue."`
   followed by a question in that session. Check private workspace memory if you
   explicitly authorized durable storage. Start another thread to check the
   difference between session history and durable memory.
3. Follow [Google setup](google.md). Verify a bounded Gmail query and Calendar
   listing before enabling any writes. Inspect scopes and individual auth results.
4. If you opt into Calendar writes, propose one harmless event, approve its exact
   fields, apply the token once, and verify it in Google Calendar. A timeout or
   unverified response requires inspection before any retry. Recurring/all-day
   mutations and attendee invitations are outside the v0 write surface.
5. Import `examples/demo-notes.md`; ask for a summary and source ID, then explicitly
   authorize any extracted fact you want saved. Test a Planner block independently
   of Calendar, including overlap refusal and explicit offline confirmation.
6. Review `git diff --cached` and run `python3 scripts/scan_public.py --staged`
   before deciding whether to publish. This task does not push or publish files.

## Future curation from private Tari

Keep the repositories independent. For a useful future change, inspect only its
specific tracked source files, copy selected logic into a local public branch,
replace identifying examples/defaults, and review dependencies. Never merge or
mirror private history. Do not transfer environment files, state, sessions,
profiles, personal notes, operational reports, or experimental infrastructure.

Run the relevant public smoke tests and focused scan, inspect the complete staged
diff, then obtain the owner's publication approval. No automatic sync tooling is
installed. Public template/helper updates must also be applied deliberately to an
existing installation's private workspace; initialization intentionally preserves
existing files, including customized instructions and helper code.
