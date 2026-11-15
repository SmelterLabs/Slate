# Release notes

What changed in each release that a person or an agent using Slate would notice, newest first.
How to deploy a new version is in `deploy/server/RUNBOOK.md`.

## Unreleased

- An agent's answer keeps what its run did: a "N steps" button under the comment opens the
  run's lines (`run_steps` on the comment, `GET /comments/{id}/run`; new table `comment_runs`).
  The lines are no longer lost when the run ends, as long as the run leaves a comment.
- The task window's All view is quieter about a run: no progress notes, no Ask or end-of-run
  bookkeeping lines, and "via API" / "via MCP" only in History.
- While an agent works a card, the task window shows what it is doing right now in a small
  scrolling box under "is typing…" (`POST /work/{id}/activity`, the runner's `activity`
  progress line). The lines are never events in the card's history.
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
