# Setup and configuration

Start with the [Quick Start](../README.md#quick-start). Git, Python 3.10+ with IANA
timezone data, and Docker with Compose v2 are required. macOS Apple Silicon has
been smoke-tested; Linux and Windows through WSL2 use the same commands but still
need platform acceptance checks.

The runtime uses prebuilt OpenClaw **2026.9.9** with a thin overlay providing
**gog 0.40.0**, Python, and Poppler. Gog archives are checksum-verified for arm64
and amd64. No upstream OpenClaw source checkout is required. These are deliberate
compatibility pins: review upstream security updates before changing them.

## Model authentication

For the default `openai/gpt-6.1-sol`, put your API key in the generated, ignored
`.local/runtime.env` as a simple unquoted value:

```dotenv
OPENAI_API_KEY=your-own-api-key
```

Never put credentials in tracked templates or workspace notes. The environment
file also supports `ANTHROPIC_API_KEY` and `OPENROUTER_API_KEY`. Provider access,
model availability, and usage charges depend on your account.

Choose an alternative supported provider/model on the **first initialization**:

```sh
./tari init --timezone UTC --model anthropic/claude-sonnet-4-6 --name Tari
```

Initialization creates only missing files. Running it again with other flags does
not overwrite an existing installation or rotate its secrets. The default model
remains `openai/gpt-6.1-sol` unless you select another.

OpenClaw-managed authentication profiles can also be used without those environment
keys. Start the container, then use its supported provider login:

```sh
./tari start
./tari openclaw models auth login --agent tari
./tari openclaw models status --agent tari --json --check
```

The login command offers methods supplied by installed provider plugins; availability
depends on the provider. Use `./tari openclaw models auth login --help` for options.
Sign in within this installation. Do not copy another application's auth store or
credential files. A successful login does not guarantee access to every model.

To change the model in an existing installation:

```sh
./tari openclaw models set PROVIDER/MODEL
```

Replace `PROVIDER/MODEL` with an actual supported identifier and update `TARI_MODEL`
in `.local/runtime.env` to match for configuration consistency. Restart this
installation if needed to apply configuration changes.

OpenClaw documents its profile and route checks in the
[pinned model CLI reference](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/cli/models.md).

## Diagnostics and acceptance

```sh
./tari doctor
./tari status
./tari logs
```

`doctor` checks the Docker daemon, runtime files, timezone consistency, Gateway
liveness, and `openclaw models status --agent tari --json --check` inside this
installation. It recognizes OpenClaw-managed profiles and supported local/provider
configuration rather than requiring a particular API-key variable.

- **Configured:** OpenClaw's auth/config check passed. Actual inference is unverified.
- **Missing or expired:** a configured model route needs credentials or renewal.
- **Expiring:** a selected credential needs attention soon.
- **Unsupported or unavailable:** a route is incompatible or its runtime unavailable.
- **Unverified:** OpenClaw status could not confirm readiness, including when stopped.

Gateway health and model readiness are separate. No diagnostic success implies a
successful model reply or Google access. The built-in check does not use `--probe`
or make a test inference; it can inspect provider-owned authentication state.
Use two messages to check a real conversation with your own credentials:

```sh
./tari chat --session demo "Call this conversation my planning demo."
./tari chat --session demo "What did I call this conversation?"
```

Real OAuth, Gmail/Calendar operations, model-driven behavior, and optional Ollama
generation require deliberate owner acceptance. Do not publish raw auth diagnostics
or logs: they can contain account and credential-source information.

## Browser interface

Open **http://127.0.0.1:18899/** and enter the token from `./tari token` in the
local Control UI connection settings. Do not share the token, token-bearing URLs,
or screenshots of connection settings. If device pairing is requested:

```sh
./tari openclaw devices list
./tari openclaw devices approve REQUEST_ID
```

Replace `REQUEST_ID` with your local browser's actual pending request and approve
only a device you recognize. For a different host port, choose
`./tari init --port 18999` on first use and open that port instead.

## Private state

| Path | Purpose |
| --- | --- |
| `.local/runtime.env` | Provider settings, generated Gateway/keyring secrets, timezone, port, and installation identity |
| `.local/state/openclaw.json` | Active OpenClaw configuration |
| `.local/state/workspace/` | Active instructions, preferences, memory, planner, and documents |
| `.local/google/` | This installation's OAuth client and encrypted file keyring |
| `.local/dailygrad/` | Optional DailyGrad adapter settings and runner state |

Tracked `workspace/` files are starter templates. The active copies are beneath
`.local/state/workspace/`; editing a template does not replace existing user files.

Customize `SOUL.md` for tone and behavior and `IDENTITY.md` for the name/identity.
`USER.md` holds stable preferences, `MEMORY.md` durable context, and `domains/`
active tasks and commitments with provenance. Ask explicitly which facts to retain.
OpenClaw stores session history independently; semantic memory search needs an
appropriate memory provider. Basic notes and session continuity do not require a
new vector database.

Keep credentials out of Markdown. Git ignore rules are a precaution, not a substitute
for protecting the device and reviewing files before publication.

## Timezone

```sh
./tari timezone
./tari timezone Europe/Berlin
```

UTC is the default. The setter validates IANA zones and synchronizes the environment,
OpenClaw, the managed user preference, and optional DailyGrad. It recreates services
that are already running and leaves stopped services stopped. It does not generate
a digest or schedule a job. Invalid zones fail before configuration writes.

## Updates and shutdown

```sh
./tari stop
./tari start
```

Shutdown affects only this installation's Compose project and preserves its data.
The core publishes only on loopback and has no host-home or Docker-socket mount.
Optional DailyGrad adds selected read-only digest data and a read-only socket;
the standalone package runs in its separate, constrained service.

After updating source, refresh distributed helpers and skills with:

```sh
./tari workspace-update
```

This preserves personal state and user-added files, but replaces distributed
helper/skill files. After reviewing customized startup instructions, optionally run:

```sh
./tari workspace-update --instructions
```

That also replaces `AGENTS.md`, `SOUL.md`, `TOOLS.md`, and `HEARTBEAT.md` from tracked
templates. It preserves `USER.md`, `IDENTITY.md`, `MEMORY.md`, domains, planner data,
documents, and credentials. Keep a private copy of custom instruction edits first,
or merge the relevant instruction changes yourself.

## Optional integrations and development checks

- [Google setup](google.md): separate read-only mail and optional Calendar writes.
- [DailyGrad setup](dailygrad.md): digest-only access or separately enabled controls.
- [API](api.md): trusted backend access, authentication, sessions, and streaming.
- [Security](../SECURITY.md): complete permission and deployment boundaries.

```sh
python3 -B -m unittest discover -s tests
node --test tests/dailygrad-plugin/*.test.mjs
python3 scripts/scan_public.py
```

For Docker smoke checks without installed state or credentials, build the images
as described in [readiness verification](readiness.md#what-was-verified), then run
`python3 scripts/smoke_readiness.py --dailygrad`. It uses disposable local fixtures;
it does not call real Google APIs, news generation, or a model.
