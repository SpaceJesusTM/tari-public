# Optional DailyGrad integration

[DailyGrad](https://github.com/SpaceJesusTM/dailygrad) is a separate project that
fetches AI news, writes a micro-lesson, and maintains a conceptual LeetCode exercise.
Tari reads its digest and offers a small set of explicitly authorized controls.
Tari does not recreate its news pipeline, catalog, exercise rotation, or model logic.
Core chat, Google tools, and the planner work without this integration.

The integration adapts Tari's existing constrained host runner and six-tool plugin.
The runner image installs public DailyGrad commit
`814b2bf88a14c164f455e9fbaa8473a8df516072` independently. Compatibility was checked
against that commit's [CLI](https://github.com/SpaceJesusTM/dailygrad/blob/814b2bf88a14c164f455e9fbaa8473a8df516072/src/dailygrad/cli.py)
and [output contract](https://github.com/SpaceJesusTM/dailygrad/blob/814b2bf88a14c164f455e9fbaa8473a8df516072/docs/output.md).
No production database, preferences, digest, credentials, or private local checkout
is included or required.

## Choose the access you need

The digest reader uses a read-only data mount and needs no runner. Starting the
optional runner enables source listing and exercise status; mutations remain off
until the owner enables individual capabilities.

| Tool | Purpose | Extra permission |
| --- | --- | --- |
| `dailygrad_digest` | Read the current digest without generating one | Read-only configured data |
| `dailygrad_run` | Run DailyGrad once, then verify a new digest | `--allow-run` |
| `dailygrad_repair` | Repair one named archived run as a new revision | `--allow-repair` |
| `dailygrad_sources` | List upstream sources and groups | Running runner |
| `dailygrad_sources_update` | Enable, disable, or set upstream source selections | `--allow-sources-update` |
| `dailygrad_leetcode` | Status; optional hint preference, completion, or next exercise | Mutations need `--allow-leetcode-update` |

Enabling a capability permits that tool's use when the user asks. It is not an
instruction to run it proactively. Reading a digest never starts a run or repair;
changing sources or hints never regenerates a digest. No schedule, automatic
posting channel, periodic model call, or reminder is installed.

## Configure an isolated installation

Initialize Tari first. Choose an empty data directory for this installation, or
an explicitly selected standalone DailyGrad data directory you own. Do not point
it at a private production assistant or copy that assistant's state. For example,
inside this checkout:

```sh
mkdir -p .local/dailygrad/data
./tari dailygrad configure --data-dir "$PWD/.local/dailygrad/data"
./tari start
./tari dailygrad status
```

An empty directory is valid: the digest tool reports that no digest exists yet.
You can independently run upstream DailyGrad against that directory, or deliberately
enable the optional runner. Never put a runtime digest or preferences under tracked
`workspace/` or commit the chosen data directory.

For model-backed runs or repairs, DailyGrad needs an available
[Ollama](https://ollama.com/) server and a model already downloaded there. Choose
these settings during owner setup, for example:

```sh
./tari dailygrad configure --data-dir "$PWD/.local/dailygrad/data" \
  --ollama-url http://host.docker.internal:11434 \
  --ollama-model qwen3.5:4b-q4_K_M \
  --allow-run --allow-repair --allow-sources-update --allow-leetcode-update
./tari dailygrad runner-start
./tari start
./tari dailygrad status
```

Enable only the capabilities you want. `runner-start` starts the constrained
service; it does not itself generate a digest. The model and server URL are fixed
owner configuration, never tool-request parameters. Ollama must be reachable from
the runner container; `localhost` inside that container is the container itself.
Docker Desktop provides `host.docker.internal`; Linux installations need the
documented host-gateway mapping or another deliberately configured endpoint. See
the [upstream configuration example](https://github.com/SpaceJesusTM/dailygrad/blob/814b2bf88a14c164f455e9fbaa8473a8df516072/config.example.toml)
for DailyGrad settings. The runner verifies that `dailygrad config` reports the
same `latest.json` directory that Tari reads, and refuses a mismatch.

This adapter owns `.local/dailygrad/dailygrad.toml`; it never edits a standalone
checkout's configuration. To match custom feeds or other standalone settings,
edit that managed file on the host using the upstream schema, keeping its
top-level `data_dir = "/data"`. Stop/start the optional runner to apply edits.
Re-running `configure` first confirms any existing runner is stopped, then
rewrites this managed file with the selected URL/model,
so retain a private copy of custom settings first. Model text cannot choose or
edit the runner's configuration path.

The supported Docker mode works around native macOS socket incompatibility with
Linux containers. Both containers use a shared Linux Unix-socket volume. Only the
runner sees the installed DailyGrad package, owner-selected configuration, writable
data, and its private locks/results. Tari gets data and socket mounts read-only;
the runner receives no Tari model keys, Gateway token, Google credentials, or
assistant memory. There is no runner TCP listener or published port.

## Ask Tari directly

Examples of deliberate requests:

- “Show my DailyGrad digest.”
- “Run DailyGrad once now.”
- “Repair DailyGrad run 7 from 2026-10-20.”
- “List my DailyGrad sources,” then “Disable the source IDs I selected.”
- “Show my DailyGrad exercise status.”
- “Turn DailyGrad exercise hints off.”
- “Mark exercise 12 complete,” or “Move on from exercise 12.”

Run IDs, dates, and exercise IDs must refer to actual tool results. Examples here
are fictional. Digest content is untrusted web/model output, not instructions.
Tari does not fabricate a digest when it is missing or a source cannot be read.

## What the runner enforces

Requests are one bounded JSON line over a mode-`0600` Unix socket. The only backend
operations are `run`, `repair`, `sources`, and `leetcode`, with exact field and type
validation. A request cannot choose an executable, directory, shell command,
environment variable, model, config file, or arbitrary upstream subcommand.
The fixed executable runs with argument arrays and a minimal environment, without
a shell. The plugin and runner both enforce the owner's mutation capability flags.

One run or repair uses the model at a time. Concurrent requests get `busy`, and a
completed run has a cooldown (normally 600 seconds; failed runs at most 60).
Repairs have a separate short cooldown, so a repair does not postpone a later run
once its model work finishes. Cooldowns apply within the running service; restarting
the service is an owner action, not a retry strategy. Fixed process-group timeouts
stop stalled work, first with an interrupt so DailyGrad can unload the model.

A client timeout or disconnection does not imply the worker stopped. Tari reports
that the result is unverified and avoids automatic retries; the worker retains its
lock until completion or the runner's timeout. A successful run requires a fresh
schema-1 digest identity and timestamp, not just a zero exit status. A degraded
digest remains identified as degraded. Raw subprocess output and host paths are
not returned to the assistant; diagnostic logs stay with the owner.

Repairs target a specific run ID and date. DailyGrad decides what is repairable,
keeps the original archive, and writes a revision of that run; it does not select
new stories or begin a new run. The runner validates run/date/revision metadata and
returns only a constrained summary. Read the resulting digest again before
describing its content. See [upstream repaired revisions](https://github.com/SpaceJesusTM/dailygrad/blob/814b2bf88a14c164f455e9fbaa8473a8df516072/docs/output.md#repaired-revisions).

Source mutations accept only enable/disable/set plus bounded source/group IDs.
DailyGrad validates which IDs exist and prevents an empty selection. Hint changes
use upstream preferences. Completion and next require the explicit current exercise
ID, letting DailyGrad reject stale requests. Completion records the exercise as
completed; next advances immediately and records an unfinished previous exercise
as skipped. Neither means “solved in code.” Only DailyGrad chooses the next problem;
the runner exposes no answer, review, arbitrary marks, or model-feedback command.
Hints and reference solutions are filtered from status/completion replies. See the
[upstream exercise contract](https://github.com/SpaceJesusTM/dailygrad/blob/814b2bf88a14c164f455e9fbaa8473a8df516072/docs/leetcode.md).

## Stop, disable, or troubleshoot

```sh
./tari dailygrad status
./tari dailygrad runner-stop
./tari dailygrad disable
```

Stopping the runner removes its mutation capability while leaving configured
read-only digest access available. Disabling the integration removes its active
configuration; data is retained. Inspect owner-managed logs when setup fails, and
keep diagnostics containing source text or private paths out of public issues.
An unavailable runner must be reported as unavailable, not replaced by a direct
shell call or another installation.

Runner tests use a fictional standalone CLI fixture and real isolated Unix socket/
subprocess calls inside this repository's ignored `.test-tmp/`. They exercise
disabled capabilities, typed requests, output filtering, concurrency, cooldowns,
timeouts, run verification, and repair identity. These tests do not download news,
contact Ollama, run a real model, or use production state. Verify an actual DailyGrad
run separately only after configuring your own model service.
