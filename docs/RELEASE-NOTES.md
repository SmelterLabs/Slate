# Release notes

What changed in each release that a person or an agent using Slate would notice, newest first.
How to deploy a new version is in `deploy/server/RUNBOOK.md`.

## Unreleased

- A message sent to an agent while it is working on the card reaches the running agent instead
  of waiting for the run to end, and the comment shows "Seen while working". The typing line
  has a Stop button (`POST /items/{id}/stop`). Runners get both through the heartbeat answer
  (`messages`, `stop`), the stream's new `control` message and the command's `SLATE_CONTROL`
  file; a command that ignores them behaves as before.
- The migration history starts at one baseline revision. A database already at Slate 0.1.0's
  schema upgrades as before. A database from before Slate 0.1.0 must be upgraded with Slate
  0.1.0 first; the upgrade refuses it and says so.
- The ClickUp importer (`slate import clickup`) and `slate backfill-doc-text` are removed.

## 0.1.0

The first public release.
