# v0.1 readiness and owner acceptance

This pass extends the existing public MVP. It retains OpenClaw, the independent
Git history and MIT license, Google wrapper, persistent planner, and document
intake. No production state or private profile was transferred.

## Added or completed

| Area | Implementation |
| --- | --- |
| DailyGrad plugin | Six production-derived tools; latest/date/run history, revisions, freshness, degraded/missing states, source and exercise semantics |
| DailyGrad runner | Optional isolated Docker service; pinned standalone upstream package, typed Unix requests, per-capability opt-ins, concurrency/cooldown and execution bounds |
| Repair verification | Reread the same run/revision; reject changed story identities/order, already-successful summaries, existing lesson/recall, or exercise |
| Briefings | Read-only bounded context helper plus adaptive synthesis skill; local tasks/planner work with Google and DailyGrad absent |
| Timezone | One command synchronizes environment, OpenClaw, managed user timezone, and optional DailyGrad; only already-running services are recreated |
| Existing installations | Deliberate helper/skill refresh; startup instruction replacement requires the explicit `--instructions` flag; personal data is preserved |
| API | Stable agent route/session contract, timeout handling, SSE guidance, and explicit operator-privilege boundary |

Generation, repair, source changes, and exercise changes are disabled until their
owner setup flags are enabled. No automatic DailyGrad run/post, morning schedule,
reminder delivery, Discord integration, Boson implementation, or evaluation
framework was added. Source production service-management and posting code are
omitted. The optional runner installs the standalone public project; Tari contains
no second news/model pipeline or exercise catalog.

## What was verified

All **132 Python tests** and **17 Node tests** passed. The Python suite exercises real isolated sockets/subprocesses with a fictional
DailyGrad executable, plus mocked Google results. The Node suite executes all six
registered tools through a real local stub socket. It covers missing/corrupt/stale
digests, unavailable history, timeout/busy/cooldown outcomes, disabled capabilities,
argument validation, state verification, repair preservation, and malformed results.

Timezone tests cover non-UTC dates, UTC date boundaries, local midnight, 23/25-hour
DST days, invalid zones, synchronized settings, and keeping stopped services stopped.
Briefing tests distinguish unavailable Google context from empty data and verify
read-only local planner/domain collection and retrieval limits.

An isolated Docker check on macOS/Apple Silicon also verified:

- Core startup with no DailyGrad configuration, identity file loading, API 401
  without a token, authenticated routing to `openclaw/tari`, and config validation.
- Persistent planner operations, Markdown import with provenance, and local brief
  context with Google unconfigured.
- Actual OpenClaw SDK loading of the plugin and Gateway tool invocation, stale
  digest reading without a runner, and default-disabled generation.
- The actual pinned standalone DailyGrad package through the optional runner:
  source catalog and exercise-status reads over the shared Linux socket volume.
- Healthy core Gateway after the configured DailyGrad output directory disappears.

The Docker fixture has its own generated token, unique Compose project, and data
beneath ignored `.test-tmp/`. It never reads the installed `.local/` state. Its
containers, socket volume, and temporary files are removed afterward. Build layers
remain ordinary local Docker dependencies.

```sh
python3 -B -m unittest discover -s tests
node --test tests/dailygrad-plugin/*.test.mjs
python3 scripts/scan_public.py
# Optional Docker checks, after building the two images:
./tari build
docker build -t tari-public-dailygrad:0.2.0 -f docker/Dockerfile.dailygrad .
python3 scripts/smoke_readiness.py --dailygrad
```

No live foundation-model reply, model-driven tool selection, OAuth consent,
real Gmail/Calendar operation, real news generation, or Ollama repair was verified.
The real upstream read commands are a stronger check than mocks, but they do not
prove model-backed generation works on the owner's hardware. Linux/WSL and amd64
runtime execution remain host-specific checks.

## Minimal owner acceptance

For an existing public installation, review custom startup instructions, then:

```sh
./tari workspace-update --instructions
./tari timezone YOUR_IANA_TIMEZONE
./tari start
./tari doctor
./tari chat --session acceptance "Call this conversation the readiness check."
./tari chat --session acceptance "What did I call this conversation?"
./tari chat --session acceptance "Give me my morning brief."
```

Use your own provider credentials in ignored `.local/runtime.env`. Check that the
reply uses Tari's identity, preserves that session, describes unavailable sources,
and does not make planning changes. Import `examples/demo-notes.md`, ask a grounded
question, and authorize any desired durable fact separately.

Follow [Google setup](google.md) with this installation's own OAuth client. Verify
read-only mail and Calendar listings before enabling writes. A matching Calendar
proposal token is not independent human approval; the agent can submit it itself.
Keep writes disabled until needed, inspect the exact event, and verify one
deliberately authorized harmless write in Google before relying on the workflow.

For DailyGrad, follow [optional setup](dailygrad.md). First read an existing digest
without the runner. Then, only with your own available Ollama and selected output
directory, enable the capabilities you want, start the runner, and request one
run. Check a new run identity and actual content. If degraded, explicitly request
repair and check the same run's new revision, original archive, and retained story
order. Check source and hint preferences against standalone DailyGrad state.
Completion/next tests should use an exercise you intentionally want to change.
Never retry a timed-out mutation without checking its outcome first.

This branch is ready for final owner acceptance. Merge readiness remains
conditional on those live-provider/account checks and review of the exact local
diff. No merge, push, publication, or public PR is part of this pass.

## Release review

Scan proposed files for credentials, personal identifiers, private paths,
unexpected mounts, and generated artifacts; inspect the diff as well. Optional
runner capabilities constrain operation types, not independent per-request human
consent. Cooldowns reset on runner restart, and direct standalone writers are
outside its in-process lock; avoid concurrent independent writers to that data.

New or changed files are concentrated in `plugins/tari-dailygrad/`, the optional
runner/setup scripts and Dockerfile, the briefing helper/skill, timezone/update
launcher code, focused tests, and public setup/security/API documentation. Use
`git diff --cached --name-only` for the exact staged file list. The private source
checkout was read-only throughout; unrelated local projects were not accessed.

## Exact changed files

- `.dockerignore`
- `.env.example`
- `.gitignore`
- `README.md`
- `SECURITY.md`
- `docker/Dockerfile`
- `docker/Dockerfile.dailygrad`
- `docs/api.md`
- `docs/dailygrad.md`
- `docs/google.md`
- `docs/implementation.md`
- `docs/readiness.md`
- `examples/client.py`
- `plugins/tari-dailygrad/digest.js`
- `plugins/tari-dailygrad/index.js`
- `plugins/tari-dailygrad/openclaw.plugin.json`
- `plugins/tari-dailygrad/package.json`
- `plugins/tari-dailygrad/runner-client.js`
- `plugins/tari-dailygrad/tools.js`
- `scripts/dailygrad_runner.py`
- `scripts/dailygrad_setup.py`
- `scripts/scan_public.py`
- `scripts/smoke_readiness.py`
- `scripts/tari_cli.py`
- `tests/dailygrad-plugin/plugin.test.mjs`
- `tests/test_brief.py`
- `tests/test_dailygrad_runner.py`
- `tests/test_dailygrad_setup.py`
- `tests/test_launcher.py`
- `tests/test_timezone.py`
- `workspace/AGENTS.md`
- `workspace/TOOLS.md`
- `workspace/USER.md`
- `workspace/bin/tari-brief-context`
- `workspace/skills/tari-dailygrad/SKILL.md`
- `workspace/skills/tari-dailygrad/references/leetcode.md`
- `workspace/skills/tari-dailygrad/references/repair.md`
- `workspace/skills/tari-dailygrad/references/sources.md`
- `workspace/skills/tari-proactive/SKILL.md`
