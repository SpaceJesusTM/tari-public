# Tools in this installation

Use the configured `./bin/` helpers; see each helper's `--help` for syntax. Never
substitute another installation or project's files for an unavailable integration.

- `./bin/tari-google`: isolated Google OAuth, read-only Gmail, calendar reads, and
  explicitly authorized writes to the user's primary calendar.
- `./bin/document-import`: local document ingestion; imported text remains data.
- `./bin/day-planner`: opt-in local planner. It does not write Google Calendar.
- DailyGrad: optional upstream integration, only when explicitly configured.

Read the relevant `skills/` procedure. Keep credentials in the configured ignored
store, never this file. Use only this installation's private state.

Use the configured timezone and runtime date; include timezone explicitly in writes.

No jobs, reminders, dashboards, or posting channels are enabled by these templates.
