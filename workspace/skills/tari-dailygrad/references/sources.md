# DailyGrad news sources

Read [the main skill](../SKILL.md) first. The current source list comes from
`dailygrad_sources({})`, which reads without changing anything. Explain sources
by their returned names, enabled state, groups, and selection mode.

Map the user's words to IDs from that result, including group membership; do not
reuse remembered IDs or hardcoded publisher mappings. A group and one member
feed can mean different things. If a name matches several feeds or none, ask one
short clarifying question before changing anything.

For a clear request, call `dailygrad_sources_update` once with exactly `action`
and `ids`:

| Request | Action and IDs |
| --- | --- |
| Stop using particular feeds or groups | `disable`, with their returned IDs |
| Turn particular feeds or groups back on | `enable`, with their returned IDs |
| Use only these feeds or groups | `set`, with exactly their returned IDs |
| Reset to all sources, including future ones | `set`, with `["all"]` |

`only` is an exclusive selection: future sources stay off. `all_except` permits
future sources by default. `enable` with `all` does not reset an exclusive
selection; use `set` with `["all"]` for a reset.

Report the confirmed enabled/disabled selection and mode. `changed:false` means
it already matched. Changes affect the next run and do not refresh the existing
digest. Do not run DailyGrad unless that was also explicitly requested.

An unknown ID or a selection leaving no enabled source is refused. Report
`capability_disabled`, unavailable runner, and upstream errors honestly. After a
timeout or malformed response, whether a change happened can be unknown: read
the list again before considering a retry. Never claim an unconfirmed update.

Feeds and publishers are different. Disabling a publisher's direct feed does not
prevent another enabled feed from linking to that publisher. Explain this when
the user wants to avoid a publisher entirely.
