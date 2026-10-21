# Optional Google setup

Tari works without Google. Add this integration only when you want Gmail or
Calendar access. All commands below run from the public repository root and use
its container and ignored `.local/` state. They do not use a host `gog` installation
or another assistant's tokens. No live Google authorization was used in development;
the integration tests use mocked command responses.

## Create your own OAuth client

1. Create or select your own project in [Google Cloud Console](https://console.cloud.google.com/).
   In **APIs & Services → Library**, enable **Gmail API** and **Google Calendar API**.
   See Google's [Gmail setup guide](https://developers.google.com/workspace/gmail/api/quickstart/python)
   and [Calendar setup guide](https://developers.google.com/workspace/calendar/api/quickstart/python).
2. Open **Google Auth platform → Branding**, choose **Get started** if needed, and
   provide the app name, support email, and contact email. Under **Audience**, use
   **External** for a personal account; use Internal only if eligible for your
   Workspace organisation. While External is in Testing, add your own Google
   account under **Test users**. In **Data Access**, declare the required scopes
   below. Google's [consent setup](https://developers.google.com/workspace/guides/configure-oauth-consent)
   explains the current Console labels and verification requirements.
3. Under **Clients → Create client**, select **Desktop app**, name it, and download
   its JSON. Use an OAuth client, not an API key or service account. See Google's
   [credential guide](https://developers.google.com/workspace/guides/create-credentials).
4. After `./tari init`, manually copy that downloaded JSON to
   `.local/google/client_secret.json` and restrict it to your user (`chmod 600
   .local/google/client_secret.json`). Do not put it under `config/`, paste it into
   chat, or reuse a private production token store. The container sees it at
   `/home/node/.config/gogcli/client_secret.json`.

For read access, the intended scopes are `gmail.readonly` and `calendar.readonly`
(full URI prefix: `https://www.googleapis.com/auth/`). Calendar writes, if explicitly
enabled later, additionally use `calendar.events.owned`: event changes on calendars
you own, rather than broad calendar administration. Google documents the scope
meanings in its [Gmail](https://developers.google.com/workspace/gmail/api/auth/scopes)
and [Calendar](https://developers.google.com/workspace/calendar/api/auth) references.

## Authorize the read-only profile

Set `TARI_GOOGLE_ACCOUNT=you@example.com` in the ignored `.local/runtime.env`, using
your actual Google account and simple unquoted values. Leave `TARI_CALENDAR_WRITES=0`.
Run `./tari start` to recreate the container with the updated environment. From a
terminal, register the client and authorize only the two required services:

```sh
./tari gog -- --client tari-read auth credentials set /home/node/.config/gogcli/client_secret.json
./tari gog -- --client tari-read auth add you@example.com --services gmail,calendar --gmail-scope readonly --readonly --manual
./tari gog -- --client tari-read auth list --check
```

The `--` separator passes global `gog` flags through the launcher. Open the printed
authorization URL in your browser, select your account, inspect the permissions,
and paste the resulting redirect URL into the same terminal when prompted. A local
redirect page may fail to load; the manual flow uses its complete address. Finish
with the same client and installation that started the flow. Do not paste the URL
into chat or publish it. See the pinned [gog auth reference](https://github.com/openclaw/gogcli/blob/v0.40.0/docs/commands/gog-auth-add.md)
for manual-flow and scope flags.

The `tari-read` profile is selected by the wrapper for every read. Do not authorize
its default broad Gmail scope or use `--services all`. Inspect the returned account,
services/scopes, and individual health results; a command exit code alone does not
prove that every token works. A Workspace administrator may restrict consent.

Test through the assistant's bounded interface:

```sh
./tari exec ./bin/tari-google gmail search 'newer_than:1d -category:promotions -category:social' --max 10
./tari exec ./bin/tari-google calendar calendars
./tari exec ./bin/tari-google calendar list --start '2027-04-12T00:00:00+00:00' --end '2027-04-13T00:00:00+00:00' --json
```

Replace example dates and offsets with the desired window. Calendar listings retain
calendar ownership and report incomplete reads as failures. Gmail search preserves
pagination under `data`; use `--page` when a next-page token exists. A single page
is not a complete inbox recap. Gmail supports search/read only and cannot send,
draft, label, archive, delete, or change read state through the wrapper.

## Optional calendar writes

Keep the read profile intact. Register the JSON for a separate named profile and
authorize Calendar only, with the owned-event scope appended:

```sh
./tari gog -- --client tari-calendar-write auth credentials set /home/node/.config/gogcli/client_secret.json
./tari gog -- --client tari-calendar-write auth add you@example.com --services calendar --readonly --extra-scopes https://www.googleapis.com/auth/calendar.events.owned --manual
./tari gog -- --client tari-calendar-write auth list --check
```

Here `--readonly` chooses the base read-only scopes during authorization;
`--extra-scopes` deliberately adds owned-event writing. This profile is not a
read-only Calendar grant. The read and write names are separate `gog` token buckets;
see the pinned [client reference](https://github.com/openclaw/gogcli/blob/v0.40.0/docs/auth-clients.md).

Only after you intend to enable writes, set `TARI_CALENDAR_WRITES=1` in
`.local/runtime.env` and run `./tari start`. The assistant's policy restricts writes
to `primary`; the wrapper additionally rejects calendars you do not own. It refuses
recurring-series changes. Every write needs a concrete proposal and its token:

```sh
./tari exec ./bin/tari-google calendar propose create primary --summary 'Example appointment' --start '2027-04-12T14:00:00+00:00' --end '2027-04-12T15:00:00+00:00'
./tari exec ./bin/tari-google calendar apply TOKEN --confirm TOKEN
```

Do not run the example against your calendar unless you want that actual event.
Use the token printed by the proposal. A clear direct event/time request authorizes
creation; extracted or inferred events must first be shown as a complete list for
user confirmation. Deletion needs approval for that specific event. Tokens are a
technical safeguard, not user consent. Readback must confirm the remote change;
the wrapper never treats a proposal as a completed write.

The agent can obtain and submit a proposal token itself. A matching token is a
workflow check, not proof of independent human approval. Calendar writes stay
disabled by default; this release does not add a separate human approval service.

## Expiry, storage, and disconnecting

External OAuth apps in Testing issue seven-day refresh tokens for these user-data
scopes. Re-run the same explicit authorization command when needed. Publishing an
OAuth app changes its status and may introduce verification requirements; it is
a separate owner decision, not part of Tari setup. Other revocation and administrator
rules can also invalidate access. See Google's [expiration rules](https://developers.google.com/identity/protocols/oauth2/#expiration).

OAuth files and the file-keyring are stored only under `.local/google/`. Its keyring
password is generated in `.local/runtime.env`. Both are private and ignored by Git;
protect them together and do not share diagnostics containing secrets. The source
templates contain no account or credential. No background auth polling is installed.

To disable wrapper writes, set `TARI_CALENDAR_WRITES=0` and run `./tari start`.
To disconnect Google, revoke this app's access in your Google account, clear
`TARI_GOOGLE_ACCOUNT`, and recreate the container. Removing a local configuration
entry alone does not revoke an already issued Google token. Do not delete or alter
another installation's files while disconnecting this one.
