# Optional native Discord

Discord uses the plugin bundled with pinned OpenClaw `v2026.9.9`. It is unconfigured
by default; CLI chat, browser chat, the Gateway API, and existing tools work without
it. Configure Discord through OpenClaw's native CLI and configuration; native
pairing, user allowlists, and guild/channel policies control access.

## Connect a separate bot

1. In the [Discord Developer Portal](https://discord.com/developers/applications),
   create an application and bot for this installation. Do not reuse production
   credentials. Leave **Public Bot** off if other people should not invite it.
2. For ordinary guild conversations, enable **Message Content Intent**. Enable
   **Server Members Intent** when using member/role matching or channel-audience
   access groups; Presence is optional. For DMs and explicit bot mentions without
   privileged content access, instead set
   `channels.discord.intents.messageContent` to `false`. See the pinned
   [Discord setup](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/channels/discord/setup.md).
3. Generate an OAuth2 invite with `bot` and, for native application commands,
   `applications.commands`. For guild text use, grant View Channels, Send Messages,
   Read Message History, Embed Links, and Attach Files. Add Reactions is optional;
   threads also need Send Messages in Threads. Do not grant Administrator. Invite
   the bot to a private server you control and allow DMs if you want DM pairing.
4. Enable Discord Developer Mode and copy the user, server, and channel IDs you
   intend to authorize. Obtain the bot token on the bot page. Put it in ignored
   `.local/runtime.env` as `DISCORD_BOT_TOKEN=...` using a private local editor;
   keep that file mode `0600`. Never paste a token into chat or a command argument.
   This bot token is separate from Tari's Gateway operator token; never disclose
   the Gateway token to Discord.

After [initial setup](setup.md), run `./tari start` so the container receives the
new environment variable. Native commands operate only inside this installation:

```sh
./tari openclaw channels add --channel discord --use-env
./tari openclaw config set channels.discord.token '{"source":"env","provider":"default","id":"DISCORD_BOT_TOKEN"}' --strict-json
./tari openclaw channels status --channel discord --json
./tari openclaw channels status --channel discord --probe
```

`--use-env` validates the selected plugin's environment requirements; the explicit
SecretRef keeps the bot token out of `.local/state/openclaw.json`. If you later
change `.local/runtime.env`, rerun `./tari start` to recreate the container with
the new environment. Native configuration reload handles supported channel
changes. Do not share raw diagnostics without reviewing them for private data.
See pinned [channel commands](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/cli/channels.md).

## Pair DMs or authorize guild channels

OpenClaw defaults to DM `pairing` and guild `allowlist`. An unknown DM sender
receives a pairing request rather than access to Tari. Send a DM from the account
you want to approve, verify its identity, then approve its code locally:

```sh
./tari openclaw pairing list discord
./tari openclaw pairing approve discord CODE
```

Codes expire after one hour. You may approve additional trusted people, or choose
native `dmPolicy: "allowlist"` with explicit `allowFrom` user IDs. Pairing/allowlists
do not automatically grant native operator-command ownership. See pinned
[pairing](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/channels/pairing.md)
and [Discord access control](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/channels/discord/access-control.md).

To opt into guild use, edit ignored `.local/state/openclaw.json`, preserving its
other settings. Under `channels.discord`, configure the exact IDs, for example:

```json
{
  "groupPolicy": "allowlist",
  "guilds": {
    "YOUR_SERVER_ID": {
      "requireMention": true,
      "users": ["TRUSTED_USER_ID"],
      "channels": {
        "YOUR_CHANNEL_ID": {"enabled": true, "requireMention": true}
      }
    }
  }
}
```

This fragment belongs inside the existing Discord object. Replace placeholders
with numeric IDs. An explicit `channels` map denies unlisted channels; omitting
that map allows every channel in an allowlisted guild. `users` and native `roles`
can restrict senders; if both are configured, either match authorizes a sender.
Keep mention gating unless you deliberately want replies without a mention. Group
DMs are ignored by default and can be configured separately through native policy.
Do not use public/open policies casually on an assistant with private tools.

Every authorized participant can reach the agent and tools allowed by its native
tool policy. Shared guild conversations should use appropriate tool restrictions
and workspace separation before admitting people you do not fully trust. Private
root memory is eligible in direct conversations and filtered from guild/channel
contexts; this is not a guarantee that every private tool result is suitable for
a shared channel. Review pinned [sessions](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/concepts/session.md)
and [workspace rules](https://github.com/openclaw/openclaw/blob/v2026.9.9/docs/concepts/agent-workspace.md).
Tari does not alter OpenClaw's global session, command, plugin, or channel defaults.

## Use, inspect, and disconnect

Ask Tari in an approved conversation to retrieve saved planner information,
authorized Calendar/Gmail information, or an existing DailyGrad digest. Normal
tool permissions still apply; connecting Discord does not generate DailyGrad
data, enable Calendar writes, install dashboards, or schedule background scans.
Scheduled delivery must separately specify an authorized native destination:
`user:USER_ID` for a DM or `channel:CHANNEL_ID` for a channel. Use explicit prefixes
rather than ambiguous bare IDs.

`channels status --probe` checks provider connectivity. A config-only fallback
when the Gateway is unavailable is not a connected bot, and a successful probe
does not verify message delivery. Review and disable any scheduled deliveries to
an account before disconnecting or changing its destinations.

```sh
./tari openclaw channels remove --channel discord
./tari openclaw channels remove --channel discord --delete
```

Without `--delete`, native removal asks to disable the account and retains its
configuration; `--delete` removes its configuration without prompting. Native
removal also asks a reachable Gateway to stop the selected runtime account. For
multiple accounts, select the intended account using native `--account` options.
Remove unused token variables from `.local/runtime.env`, then rerun `./tari start`;
reset a token in Discord's Developer Portal for provider-side revocation. Local
removal alone does not revoke a token at Discord.

Live bot login, authorized/unauthorized access, and message delivery require
separate owner acceptance. Automated tests do not send real Discord messages.
