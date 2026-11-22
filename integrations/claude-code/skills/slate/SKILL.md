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

By default the server lists five tools: `slate_work`, `slate_item`, `slate_comment`, `slate_search`, `slate_list`. That is the loop below. Other tools (`slate_doc`, `slate_chat`, and the rest) appear when the server is started with `--tools all` or `SLATE_MCP_TOOLS=all`, or named (`--tools core,slate_doc`). A tool that exists but is not listed returns a one-line error naming that flag. The catalogue is `docs/agents/`.

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
| `review` | Complete, and a person should check it. Say what to check in `reply`. |
| `later` | You did what you can. The rest waits for a due time still to come. You stay assigned. Nobody is waited on. |
| `ask` | You need a person. `question` is one line. `options` is 2 to 4 short answers when the choice is simple. `waiting_on` is their handle. |
| `answered` | You only replied. `reply` is required. Nothing else changes. |
| `failed` | The machine broke. `note` is what broke, in the machine's words. Nobody is asked. |
| `blocked` | You wait on an agent (`waiting_on`) or on nobody. |
| `release` | You give the card up. |

`status` on finish is a column name or id of that list, and only when the outcome's own column is wrong. Do not invent a column to show progress.

The same endings are a last line when someone pastes your answer back as a comment: `SLATE: done`, `SLATE: review`, `SLATE: later`, `SLATE: answered`, or `SLATE: ask alex: one line? [Yes | No]`.

## Ask

Asking a person is `finish` with outcome `ask`, not a status you invent. The question is one line. They answer on the card. Your next `next` brings that reply.

## File a card

Read the list first (`slate_list list`). Its note (`note_md`) says what belongs there and how a card there is finished. File only after you have read it.

`slate_item` `create` with `list_id` and `title` files one card. Set `body_md`, `due_at`, `priority`, `tags`, and `assignees` when you know them. Assignees are handles. `slate_item update` changes fields: send only what changes, and `null` clears. `status` on update is a column name or id.

Then `slate_item get` and confirm the list, the due date, the tags, and the assignee in one or two lines, with the board link.

## Everything else

Search is `slate_search`. Comments, docs, chat, reminders, recurrence, accounts, and batch calls are in `docs/agents/` (`reference/mcp.md`, `quickstart.md`).
