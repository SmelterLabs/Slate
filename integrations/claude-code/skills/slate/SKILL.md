---
name: slate
description: "The Slate task board: file a task, read or work a task by id or link, comment, change status, see what's due. Use for any task action in any chat session (Claude Code, Claude Desktop, Hermes Agent, other MCP apps), and when a message holds a Slate item id or board link."
version: 2.0.0
license: see the Slate repository
platforms: [windows, linux, macos]
metadata:
  hermes:
    tags: [slate, tasks, work-loop, mcp]
---

# Using Slate

Slate is a task board built for agents first. You use it through the `slate_*` MCP tools
(server `slate`). Every change you make is an event with your account as the actor, so each app
that talks to Slate has its own account and key: never borrow another app's or agent's token,
and never use the owner's.

- Board: your deployment's address, for example `https://slate.example.com` (REST `/api/v1`,
  MCP `/mcp`). Copy this skill and put your real address here.
- If the board is private (a tailnet or LAN only), apps on that network reach it; cloud chats
  cannot. In a chat that can't reach it, say the task belongs in Slate and give the person the
  text to file.
- This file is the Slate guide for any agent app. Its source is the Slate repo,
  `integrations/claude-code/skills/slate/`: copy that folder into your app's skills folder (on
  Windows, `integrations/claude-code/skills/install.ps1` does the copying).

## Calling the tools

Each tool takes `{"action": "...", "params": {...}}` (single-action tools such as
`slate_today` take only `params`). The action's fields go inside `params`; put one at the top
level and it is refused. The exception is `session_ref` (your session or chat id), which goes at
the top level beside `action`, never inside `params`:
`{"action": "create", "params": {...}, "session_ref": "<id>"}`. Add it on writes so people
can trace them. Results are JSON; errors come back as plain-English tool errors.

**If the `slate_*` tools are missing**, use REST with your own app's token:
[references/rest-fallback.md](references/rest-fallback.md). Say that you used the API because
the connector was absent.

## IDs and links

- An item id is 12 letters and digits, like `Ab12Cd34Ef56`. Lists, sections and accounts have
  the same kind of id.
- A board link is `https://slate.example.com/#/list/<list_id>?task=<item_id>`
  (`#/everything?task=<item_id>` works without the list). Give this link whenever you name a
  task, with its list name: an item reported without its list reads as made up.
- **A bare item id or link from the person you work for is the instruction.** Open it (`slate_item get`) and
  do what it says. Don't ask whether they want it handled.
- People and agents are account handles (`alex`, `my-agent`, ...). Never a name, email
  or Hermes profile name. `slate_agents` lists who exists.

## The everyday actions

| To | Call |
|---|---|
| File a task | `slate_item` `create` `{list_id, title, body_md, due_at, priority, tags, assignees}` |
| Read a task | `slate_item` `get` `{id}` (comments, subtasks and last events included) |
| Find tasks | `slate_item` `list` `{list_id, assignee, tag, status, q, due_before, fields}` |
| What's due | `slate_today` `{}`: overdue, due today, waiting on you |
| Comment | `slate_comment` `add` `{id, body_md}`; `@handle` notifies them; `assignee` makes it an Ask |
| Change status | `slate_item` `set_status` `{id, status}` (a status name of that item's list, or its id) |
| Change fields | `slate_item` `update` `{id, ...only what changes}`; `null` clears |
| Say what you're doing | `slate_item` `report` `{id, state: working\|blocked\|review\|done, note}` |
| Put a file on the card | `slate_item` `attach` `{id, filename, content_base64, content_type}` (25 MB max) |
| Undo your own create | `slate_item` `delete` `{id}` within 10 minutes; after that, `archive` |
| Several writes at once | `slate_batch` `{ops: [...]}` (all or nothing; `$ref.id` reuses a result) |

**Columns are the list owner's words.** Never invent a status to show your state: `report`
puts a live badge on the card (working, blocked, review) beside the columns. `report` `done`
clears the badge and completes the task.

### Working a task by id

1. `slate_item get` and read `body_md`, `acceptance_md` and the newest comments (if a person
   handed it over, the newest comment holds the instructions).
2. `slate_item report` `{id, state: "working", claim: true, note}`. A 409 means another agent
   holds it: stop and say so.
3. Do the work. Post progress as `report` notes or comments, with links to what you made.
   **Media you made goes on the card**: images, PDFs, review sheets and short clips each
   through `slate_item attach`; the comment then names the files. The board shows them as
   thumbnails people can open on a phone. Never paste a file into text as a base64 `data:`
   image (Slate saves it as an attachment anyway, but the text is noise), and never give a
   local path as the only pointer: nobody can open it from the board. Over 25 MB (most video),
   attach a still or contact sheet and link the full file somewhere people can open it.
4. Finish with `report` `done` only after verified acceptance and evidence on the card (or
   `review` if acceptance remains, or `blocked` with `waiting_on: "<handle>"` and the exact
   blocker in the note). Name the workspace owner only when the next action is genuinely theirs.

Agents with a runner use `slate_work` `next` / `heartbeat` / `finish` instead; see
"Agents with a runner" below.

## Inter-agent work: task first, messages second

Task comments never resolve Asks. Finish each runner-owned turn through `slate_work finish`:
the server consumes only the Ask assignments present at take for dispatch, not visibility.
Review/blocked/release and assigned-task done leave open Asks for explicit resolution;
asked-only agent done explicitly resolves only its taken Asks without completing the task.
Never bulk-resolve another account's or newly arrived Asks. New/reassigned Asks get a new
turn. A plain comment/report is not a work finish; runners own that final call.

The steps below are a working practice for agents, **not enforcement code or an exactly-once
guarantee**. Before handing actionable work to another agent:

1. Search relevant open and recent tasks by source ID, target and symptom. Reuse the same
   outstanding task; do not create another card per retry. Read completed work before deciding
   whether a genuinely new task is needed.
2. Assign the **existing account of the agent that will do the work** (verify with
   `slate_agents`), not the workspace owner by default or a guessed name. Record the requester,
   the inputs, the source task and where the result should go; set explicit `acceptance_md`.
   If that account or its connector is missing, record that gap rather than borrowing
   credentials or inventing a handle.
3. If the originating task is blocked on the new one, link them: `slate_item` `link`
   `{id: <source task>, item_id: <new task>, kind: "blocked_by"}`. The link does not set
   `waiting_on`: use `report` `blocked` with `waiting_on: "<handle>"` on the source task
   separately, naming whoever must act. Name the workspace owner only for a decision or action
   that is genuinely theirs. Read back both cards (ownership, acceptance, dependency and wait
   state) before notifying anyone.
4. Use `slate_item` `run` `{id}` only for work that should start immediately. Assignment, due
   dates, Asks and Run now can each wake a runner, so don't also start the same work another
   way. If you notify the agent outside Slate as well, send only the card link or id; the
   recipient checks the current claim before acting. **Queued != working != verified done.**
5. Post completion evidence (changes, tests/results, artifact paths/hashes and remaining gaps)
   on the task before completing it, and say so on the originating task. Whoever owns that
   task checks the result, clears only the resolved wait/dependency and resumes the original
   step. Inspect claims, run history and prior effects before retrying an ambiguous delivery;
   recover accepted work rather than dispatching twice.

Use plain evidence comments when no reply is wanted; addressing a comment as an Ask or posing
a question can wake the assignee.

## Filing a task

One create is exactly one item. Subtasks only when asked (`slate_item split`, or `parent_id`).

**Where it goes:** read the lists first (`slate_list list`) and file into the one the request
names or clearly belongs to; when nothing fits, use the default list your copy of this skill
names, or ask. Copy this skill and add a table of your own lists (name, id, what goes there) so
agents stop guessing. Some lists hold entities rather than actions (one card per customer or
project, the column is its stage): before filing into a list you haven't used, read its statuses and a few
cards (`slate_list list`, `slate_item list`) to learn which it is. A task about an entity goes
in an action list, tagged, not as a new entity card.

When a task serves a record elsewhere (a project file, a note), put the task's link in that
record too.

**Fields:**

- **Title:** short and action-first ("Set up the nightly backup script").
- **Body (`body_md`):** a handoff a fresh session could execute six weeks later without asking:
  why, the steps, every input inline (full URLs, paths, ids), the deliverable, what done looks
  like. A quick reminder stays short. Real newlines, not `\n` text. Slate stores links, not
  documents: long write-ups go in a page, a file or a note, linked.
- **Due (`due_at`):** "today/now/ASAP" → today; "tomorrow/soon" → tomorrow; "this week/by
  Friday" → Friday; "next week" → next Monday; a named date → that date; a small task with no
  signal → today; a big one with no signal → ask. A reminder about a future event is due when
  someone must act, not on the event day. `YYYY-MM-DD` means 17:00 in the workspace timezone;
  send a time with offset (`2026-10-27T09:00:00-05:00`) when the hour matters. Slate returns
  UTC; show local time.
- **Priority:** "urgent/critical/ASAP" → `urgent`; "important" → `high`; default `normal`;
  "whenever/no rush" → `low`.
- **Tags:** one topic tag. Use an existing tag (`slate_tag list`); an unknown name creates a
  new tag. Unsure which → ask.
- **Assignees:** leave empty for a person's own to-dos. For a task an agent should run, assign
  that agent's existing handle; the body is then its entire kickoff. Assign work to whoever can
  actually do it; only genuinely human-only actions or approvals wait on a person.
- **Recurring:** `slate_recurrence` `create` `{list_id, title_template, rrule, dtstart,
  assignees}` with an RFC 5545 rule (`FREQ=WEEKLY;BYDAY=FR`,
  `FREQ=MONTHLY;BYDAY=WE;BYSETPOS=2,4`). Leave `missed_policy` at its default, `spawn_always`
  ("still create the next one"), unless asked for `skip_if_open` or `roll_forward` on that
  rule. Assignees live on the rule and are copied onto each new card; change who runs it with
  `update` `{id, assignees}`. Read back `next_occurrences` and say what it shows.

**Then read it back** (`slate_item get`: list, due date in local time, tags, assignee) and
confirm in one or two lines with the board link. No closing question when the filing is
complete.

**Enriching** an existing task ("add the times and locations"): `get` it, merge into `body_md`,
`update` only the changed fields. Leave due date, assignees, tags and status alone unless asked.

## Agents with a runner

Agents that Slate wakes through a runner (`wake: push`) run the work loop:

1. `slate_work` `{"action": "next", "params": {"wait": 20}}` takes your best free task with a
   15-minute lease and a live badge. `item: null` means nothing is free.
2. Retain the take's private `lease.run_id` (chat: `chat.lease.run_id`). Send it as `run_id`
   on heartbeat/finish, and on any direct report during that execution. Session_ref is only
   a trace label, not proof. A same-account caller cannot adopt a run without its proof.
3. `slate_work heartbeat` `{item_id,run_id,note,progress}`. Definitive 401/403/409 means stop
   and reconcile any unknown remote run; lease expiry is not proof it stopped.
4. `slate_work finish` `{item_id,run_id,outcome:done|review|later|blocked|release,reply}`. Retry the
   identical finish if uncertain; after its receipt take next separately (no combined next).
   Do not start a second work loop or finish independently inside a resident runner's child.

**The card is the conversation.** Answer on the card the way you'd text a colleague, a few
lines; put anything long in a file or page and link it; attach media you made to the card
(`slate_item attach`, as in "Working a task by id"); end with the `SLATE:` line. Your answer
goes in `finish` as `reply` (one comment by you), and your own session for the task as
`thread: {ref, url}`. When you need a person (a sign-in, a decision, an approval), finish
`blocked` with `waiting_on: <their handle>` and the exact question or command in `reply`: the
card says *Waiting on you* to them and their phone gets a push. Their answer in the card's
thread is your next turn: `next` returns it as `reply` (with `thread`), so continue the same
session. Preserve stable author ID, kind and role; a guest is also kind=person and is not owner
permission. Native command approvals use the runtime's control, never card keywords.
Keep native approval observation alive; do not close the stream to hand it off.

**Finish only when the work is finished.** `done` and `review` both mean the work is complete
(`review`: complete, a person should check it). `later` means you did what you can now, you
already have any go you need, and the rest must happen at the task's due time: set (or keep)
a due time still to come, stay assigned, and end with `SLATE: later`. Nobody is waited on, the
card shows no badge, and the due time runs you again in the same session. Never finish
`blocked` on a person who already said go; that puts a false *Waiting on you* on their card. If you start background work (subagents,
background processes, other sessions), wait for it to report back inside your turn; never end
the turn while it is still running. An answer with no `SLATE:` line is not treated as
finished: the runner asks you again, and then pauses the card as *Waiting on* a person.

`slate_checkin` `{since}` reads news that concerns you (mentions, replies, Asks, reminders).
Chat apps without a runner get work when someone pastes an id or a **Copy brief** prompt, and
they end a pasted brief with its last line `SLATE: done`, `SLATE: review`, `SLATE: later` or
`SLATE: blocked waiting_on <handle>`.

## Pitfalls

- `limit` above 1000 is an error; page with `offset`. `fields` is a list, not a comma string.
- Don't complete a recurring occurrence twice: read its status and due date first.
- A connection error means this machine can't reach the board (VPN or tailnet down, server
  down); retry once, then say so.
- 401: your token is wrong or revoked. Report it; don't hunt for another token.

Full reference: `docs/agents/` in the Slate repo (`reference/mcp.md`, `reference/rest.md`).
