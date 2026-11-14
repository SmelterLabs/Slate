# Release notes

What changed in each release that a person or an agent using Slate would notice, newest first.
How to deploy a new version is in `deploy/server/RUNBOOK.md`.

## Unreleased

- While an agent works a card, the task window shows what it is doing right now in a small
  scrolling box under "is typing…" (`POST /work/{id}/activity`, the runner's `activity`
  progress line). The lines are live only and never part of the card's history.
- The migration history starts at one baseline revision. A database already at Slate 0.1.0's
  schema upgrades as before. A database from before Slate 0.1.0 must be upgraded with Slate
  0.1.0 first; the upgrade refuses it and says so.
- The ClickUp importer (`slate import clickup`) and `slate backfill-doc-text` are removed.

## 0.1.0

The first public release.
