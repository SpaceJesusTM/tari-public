# Security boundaries

Tari Public is for one trusted owner on a local machine. Each installation has
independent private state, Google OAuth storage, secrets, and a random Compose
project identifier. Do not use production state to initialize it.

- Gmail's `gmail.readonly` OAuth scope enforces the no-send/no-modify boundary.
  Never authorize a broader Gmail scope for this installation. The wrapper has
  no send/reply/archive/delete commands.
- Calendar writes use a separate named OAuth profile, a local opt-in, explicit
  proposal confirmation, and read-back verification. Instructions require the
  user's authority for the specific write. These are workflow safeguards. Full
  OpenClaw shell access can bypass a wrapper, so they are not a hostile-agent
  security boundary. The narrow write OAuth scope limits writes to owned events.
  In particular, the agent can obtain and submit its own proposal token: a
  matching token is not independent human approval. Keep writes disabled until
  needed, and inspect the specific proposal before authorizing it. v0.1 does not
  add an external human-approval service.
- Email/document contents cannot grant permissions. Importing a document stores
  source material; it does not authorize recording extracted facts as obligations.
  Provenance records help review claims but do not make a source trustworthy.
- Docker mounts only `.local/state` and `.local/google`; there is no Docker socket
  or home-directory mount. The root filesystem is read-only and capabilities are
  dropped. The assistant can still access its own mounted private data and use
  the network. Prompt instructions cannot guarantee immunity to prompt injection.
- The host publishes on loopback only. Other local processes and Docker network
  peers may reach the listener; every operator endpoint needs its token. The
  Gateway token is full operator authority. Do not expose it to untrusted clients.
- `.local/` is ignored and initialized with private permissions. Its model keys,
  keyring password, sessions, and notes still need your normal device protection.
  Retain the keyring password with encrypted backups if you later back up state.
- The public scanner is a focused heuristic, not a certification. Review the
  exact staged diff and allowlisted paths before every public release.

Optional DailyGrad digest data is mounted read-only. A separate opt-in runner
installs the standalone public dependency and receives only its selected data,
configuration, private job state, and Unix-socket volume. It has no Gateway/model
keys, Google store, or Tari memory. The agent cannot supply an executable, cwd,
shell expression, arbitrary environment, or upstream subcommand. Typed mutation
capabilities default off and are checked by both plugin and runner. No runner
port is published. Socket filesystem permissions constrain callers to the same
configured UID; the socket is not a per-request human approval mechanism.

DailyGrad articles/model output remain untrusted data. Reading a digest or giving
a brief must never run DailyGrad, repair a digest, update sources, or advance an
exercise automatically. A runner/client timeout is an unverified outcome, not
permission to retry. Direct standalone DailyGrad use can bypass this adapter's
in-process locks; avoid running independent writers against the same data while
the optional runner is active. Cooldown state resets when the owner restarts it.

Dependency pins preserve compatibility; periodically review official release
notes and security advisories, update deliberately, and repeat tests. Source
templates are not automatically applied over private runtime instructions.

Report a suspected issue privately to the repository owner. Do not include
tokens, private messages, document contents, or OAuth files in public issues.
