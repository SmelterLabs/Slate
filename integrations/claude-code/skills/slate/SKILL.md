---
name: slate
description: "The Slate task board: file a task, read or work a task by id or link, comment, change status, see what's due. Use for any task action, and when a message holds a Slate item id or board link."
version: 3.0.0
license: see the Slate repository
platforms: [windows, linux, macos]
---

# Using Slate

Slate is a task board. You reach it through the `slate_*` MCP tools (server `slate`). Every change is an event on your own account: use your own token, never someone else's.

A call is `{"action": "...", "params": {...}}`. A tool with one action takes only `params`. Put that action's fields inside `params`. On a write, `session_ref` (your session id) sits beside `action`, so people can trace the events.

By default the server lists five tools: `slate_work`, `slate_item`, `slate_comment`, `slate_search`, `slate_list`. That is the loop below. Set `SLATE_MCP_TOOLS=all` or `SLATE_MCP_TOOLS=core,slate_doc` on the server to list more tools. With stdio, `slate-mcp --tools all` or `--tools core,slate_doc` overrides the environment. A tool that exists but is not listed returns a one-line error naming that flag. The catalogue is `docs/agents/`.

Example board: `https://slate.example.com` (REST `/api/v1`, MCP `/mcp`). An item id looks like `Ab12Cd34Ef56`. A board link is `https://slate.example.com/#/list/<list_id>?task=<item_id>`. Give that link, with the list name, whenever you name a task. People and agents are handles (`alex`, `my-agent`).

If the `slate_*` tools are missing, use REST: [references/rest-fallback.md](references/rest-fallback.md).

## Start

Start here: `slate_work`. One create is exactly one item. Subtasks only when asked, with `slate_item split`.

## Take

A resident runner takes work with `slate_work next`. For cards only, pass `kinds: ["task"]`. In that loop `item: null` means no card is free. Keep `lease.run_id` and send it as `run_id` on heartbeat and finish. Stop on a 409. Runners handling mentions also inspect `chat` and `doc` and use the corresponding turn actions in the MCP reference.

In an interactive session without a runner, pick a card and call `slate_item report` with its `id`, `state: "working"` and `claim: true`. This assigns the card and shows that you are working, without creating a lease. Update the badge with `report` as you work and use `report` to complete it or clear the badge. Use `slate_comment add` for replies.

## Work

`slate_work` `heartbeat` with `item_id`, `run_id`, and a short `note` or `progress`, before the lease runs out. Read `messages` (Asks since you took it) and send `seen` with the ids you have read. Do the work. Comment with `slate_comment add`.

## End

Finish with `slate_work` `finish`: `item_id`, `run_id`, `outcome`, and `reply` (your comment). Take the next task with `next` again, in its own call. Retry the identical finish if you are not sure it arrived. After the receipt, take next separately.

| outcome | when |
|---|---|
| `done` | The work is complete. |
| `review` | Finished work for a person to inspect. Say what to check in `status`. Use ask for questions. |
| `later` | You did what you can. The rest waits for a due time still to come. You stay assigned. Nobody is waited on. |
| `ask` | A person must answer or decide something, even after the work is finished. Send one to four `questions` with one-line `question` and optional `options`. For one question, the fields can go directly on the finish. `waiting_on` is their handle. |
| `answered` | You only replied. Send `reply`. The badge clears, and the server settles the questions the turn took. |
| `failed` | The machine broke. Send `note` in the machine's words. Slate retries twice and alerts the card's people if the third run fails. |
| `blocked` | You wait on an agent (`waiting_on`) or on nobody. |
| `release` | You give the card up. |

`status` on finish is the card's standing line, up to 140 characters. Say what happened and whose move it is. Review requires a status. For `ask`, the question headline becomes the standing line. On `slate_item update`, `status` is the list column instead.

When someone pastes your answer back as a comment, put the closing line or lines last. Examples include `SLATE: done: Sent the draft.`, `SLATE: review: Check the totals.`, `SLATE: later: Waiting for the due date.`, `SLATE: answered: Sent the details.`, or `SLATE: ask alex: Send the draft? [Yes | No]`.

## Ask

Use `finish` with outcome `ask` for every decision or answer you need from a person. For a pasted reply, give each question its own trailing ask line, up to four for the same person. Keep each question understandable on its own. Questions only in the reply prose are not shown as questions. They answer on the card. A reply from the person asked becomes a turn you can take.

## File a card

Read the list first (`slate_list list`). Its note (`note_md`) says what belongs there and how a card there is finished. File only after you have read it.

`slate_item create` files one card. Send `title` and the list ID you chose. If you send neither a list nor a parent, Slate uses the configured active inbox list when your token can edit it. Set `body_md`, `due_at`, `priority`, `tags`, and `assignees` when you know them. Assignees are handles. `slate_item update` changes fields: send only what changes, and `null` clears. `status` on update is a column name or id.

Then `slate_item get` and confirm the list, the due date, the tags, and the assignee in one or two lines, with the board link.

## Everything else

Use REST `GET /projects` for project next steps and `GET /today/sweep` for your cards more than 14 days overdue, excluding Later, Parked, and reference cards. Both paths are under `/api/v1`. Use `slate_item update` with `parked_until` (a future workspace date) and `parked_note` to schedule a return reminder.

Search is `slate_search`. Comments, docs, chat, reminders, recurrence, accounts, and batch calls are in `docs/agents/` (`reference/mcp.md`, `quickstart.md`).
