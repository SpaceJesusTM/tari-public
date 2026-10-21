# v0.1 readiness and owner acceptance

Tari Public v0.1 is a standalone, single-owner assistant on pinned OpenClaw
`2026.9.9`. It needs no production Tari state, credentials, or infrastructure.
This page separates what is implemented, what automated and isolated runtime
checks verified, and what still needs live owner acceptance.

## What v0.1 includes

| Area | Implementation | Default |
| --- | --- | --- |
| Chat | Local CLI, Control UI, and authenticated Gateway API with persistent sessions | On |
| Planner & documents | Persistent local planner/checklist; TXT/MD/PDF/DOCX import with provenance | On |
| Memory | Workspace notes and curated `MEMORY.md`; explicit consent before saving facts | On |
| Gmail & Calendar | Read-only Gmail; multi-calendar reads; proposal-confirmed writes to the discovered primary calendar only | Off until Google setup; writes separately |
| DailyGrad | Six tools: digest/history reads without a runner; generation, repair, sources, and exercises via an opt-in constrained runner | Off until setup; mutations individually |
| Discord | Official OpenClaw plugin with native pairing, allowlists, guild channels, and mention gating | Not installed |
| Morning brief | Native scheduled isolated agent turn; own time, IANA timezone, model, thinking; read-only tools; no fallback model | No job |
| Reminders | Native one-shot command job; fixed `printf` argv; no model at delivery; owner-only chat tools | No job, no destination |
| Background work | Heartbeat and OpenClaw autonomous skill learning | Off |

Excluded from v0.1 by design: recurring Gmail/Calendar scans, autonomy modes,
custom heartbeats, automatic DailyGrad generation, Discord dashboards, team
permissions, voice, and new external APIs.

## Release verification

### Automated tests

Run during development only for the changed component; run everything once
before a release:

```sh
python3 -B -m unittest discover -s tests
node --test tests/dailygrad-plugin/*.test.mjs tests/workflows-plugin/*.test.mjs
python3 scripts/scan_public.py
```

Results for this release candidate: **238 Python tests**, **17 DailyGrad plugin
tests**, and **26 workflow plugin tests** passed; the public scanner reported
0 findings. Python tests use a fixture scheduler, mocked Google results, and a
fictional DailyGrad executable. Node tests exercise registered plugin tools with
stub runners. Fixtures do not prove native compatibility; the checks below do.

### Isolated Docker and pinned-runtime checks

```sh
./tari build
docker build -t tari-public-dailygrad:0.2.0 -f docker/Dockerfile.dailygrad .
python3 scripts/smoke_readiness.py --dailygrad
```

The smoke check uses a disposable fixture under ignored `.test-tmp/` with its own
token and Compose project. It never reads installed `.local/` state and calls no
model, Google API, Discord, or news generation. On macOS/Apple Silicon it passed:

- Startup, identity, API authentication and routing, config validation, planner,
  document import, and brief context with Google unconfigured.
- No enabled native jobs on a fresh installation.
- Workflow plugin loading in the real Gateway: `tari_brief_context` works in
  operator and cron contexts; reminder tools are withheld from cron turns.
- Morning brief against the real scheduler: declaration-key upsert keeps one job,
  the stored model/thinking/tools/timezone/delivery contract is verified, and
  enable/disable are confirmed.
- Reminders against the real scheduler: a fixture reminder actually ran its fixed
  argv with shell syntax left inert, reported `delivered: false` because no
  Discord was configured, was left disabled without retry, and cancellation
  worked.
- DailyGrad: plugin registration, stale digest without a runner, disabled
  mutations, the actual standalone package through the runner, and a healthy
  Gateway after its data directory disappears.

Pinned-runtime review during this closeout also confirmed: jobs and run history
persist across a container restart without replaying a fired one-shot; the
`automations add/show/list/runs/enable/disable` JSON contracts; `channels status`
account fields; the plugin tool-context owner fields; and `message_sending` hook
semantics. Exploratory checks installed `@openclaw/discord@2026.9.9` in the
read-only container and validated the env-backed token configuration without a
token, so nothing contacted Discord.

### Fixed during closeout

- Brief and reminder registration read `automations add` as returning the bare job;
  the pinned Gateway returns `{"created"|"updated": true, "job": {...}}`, so every
  real registration would have failed as "outcome unknown". Fixed, with the test
  fixture corrected to the real shape.
- OpenClaw's default autonomous skill learning created an enabled weekly
  model-backed review job. Fresh installs now set it `off`.
- Discord is not bundled in the pinned image. The guide now installs the matching
  official plugin, and the container has a small npm cache tmpfs so that install
  works with a read-only root filesystem.
- Reminder tools now require an explicit native owner everywhere; the Discord
  guide explains how the first pairing approval sets that owner.

## Live owner acceptance (outstanding)

These need your accounts, credentials, possible model charges, or real outbound
delivery, so they were not run. Use a separate Discord bot and test server.

```sh
# 1. Model, identity, session continuity, planner tool use
./tari doctor
./tari chat --session acceptance "Call this conversation the readiness check."
./tari chat --session acceptance "What did I call this conversation?"
./tari chat --session acceptance "Add a planner block tomorrow 13:00-14:00 called Acceptance test."
./tari exec ./bin/day-planner show tomorrow

# 2. Confirm nothing runs in the background by default
./tari openclaw automations list --all --json   # expect no "enabled": true
```

3. **Google:** follow [Google setup](google.md); check a bounded Gmail search and
   a Calendar listing. Only if wanted, enable writes and apply one harmless
   proposal to the primary calendar, then confirm it in Google.
4. **DailyGrad:** follow [DailyGrad setup](dailygrad.md); read an existing digest
   without the runner. Optionally enable generation and request one run.
5. **Discord:** follow [Discord setup](discord.md), approve your own DM pairing,
   chat in a DM, and optionally in one allowlisted server channel with a mention.
   Confirm an unapproved account receives only a pairing prompt.
6. **Morning brief:**

   ```sh
   ./tari morning-brief enable --time 09:00 --timezone YOUR_IANA_ZONE --to 'user:YOUR_USER_ID'
   ./tari morning-brief status    # time, timezone, model, thinking, enabled
   ./tari openclaw automations run JOB_ID   # optional: one immediate paid run
   ./tari morning-brief history
   ./tari morning-brief disable   # unless you want it daily
   ```

7. **Reminders:**

   ```sh
   ./tari reminders configure --to 'user:YOUR_USER_ID'
   ./tari reminders create 2m 'Tari acceptance reminder'
   ./tari reminders create 1h 'Tari cancellation test'
   ./tari reminders cancel 'Tari cancellation test'
   ./tari reminders list --history   # after 2 minutes: first shows delivered
   ```

   Confirm the first message arrives in Discord, the second never does, and the
   history entry shows `"delivered": true`. In a Discord DM, ask Tari to create
   and cancel a reminder as the owner, then confirm a non-owner cannot.

Linux/WSL and amd64 runtime execution remain host-specific checks.

## Known limitations

- Registration is not delivery; check history. Disabling cannot recall a sent
  message, and an interrupted one-shot may run late after recovery.
- Sensitive-content filters are defense in depth, not data-loss prevention.
- Authorized guild members can reach the agent's permitted tools and private data;
  channel allowlists do not isolate users from each other.
- Calendar proposal tokens are workflow checks, not independent human approval.
- PDF intake needs selectable text; semantic memory search needs a provider.

## Release review

Before merging, review the complete diff and run the scanner. Check for
credentials, personal identifiers, private paths, unexpected mounts, and generated
artifacts. No production repository, private state, or real `.local/` contents
were read during this closeout.
