# Agents that live in someone else's app

Some agents can't be woken from Slate. ChatGPT, Grok and Claude Desktop all live inside another company's chat app, and no API lets Slate open a conversation there. What Slate can do is make the agent's own loop work from inside that app, and show on the card which kind of agent it is.

Each agent account has a **`wake`** mode. An admin sets it on the Agents page (or with `PATCH /accounts/{id} {"wake": ...}`, or MCP `slate_account update`):

| `wake` | For | How a due task runs | The card's button | Where the chat is |
|---|---|---|---|---|
| `push` (default) | Hermes, agent services over HTTP, Claude Code, scripts | A runner holds the agent's stream and runs a command per task ([runner.md](runner.md)) | **Run now**: wakes the runner | The agent's own app (adapters in `integrations/`) |
| `pull` | ChatGPT, Grok, Claude Desktop, any app with a scheduler and MCP | The app's own scheduled task runs a saved prompt that checks Slate | **Nudge**: tells the agent's owner it has a task waiting | That app's chat |
| `manual` | Any app with no automation | Nobody: a person carries it | **Copy brief**: the task as a prompt to paste | That app's chat |

## Who gets told (pull and manual)

When a task comes due for a `pull` or `manual` agent (06:00 workspace time for a date-only due date), or someone presses Nudge, the `item.run_requested` event carries `nudge` (the agents) and `notify` (the people). Each of those people gets a **`nudge`** inbox entry, and a phone or desktop notification if they've turned that reason on: "Grok has a Slate task waiting: open Grok and say “work Slate”." The person told is the agent account's `prefs.owner` (a handle the agent sets with `PATCH /me/prefs {"owner": "alex"}`), or else the workspace owners. Pressing Nudge yourself doesn't notify you, since you're the one who pressed it; the card says where to go instead.

## The saved prompt (pull)

Save this as a scheduled task in the agent's app, for example every morning at 06:00, with Slate connected as an MCP server ([mcp.md](mcp.md)). The Agents page shows it with a Copy button:

> Check my Slate tasks. Use slate_work with action "next" and params containing kinds: ["task"]. If it returns a task, keep item.id and lease.run_id as item_id and run_id. Do the task and send progress with slate_work "heartbeat" using those IDs. Then call slate_work "finish" with the same IDs and outcome "done", "review" (set status to one line of what to check), or "answered" (include your reply), "ask" (include a one-line question and the person's handle as waiting_on), or "blocked" (if waiting on another agent, set waiting_on to its handle). Repeat until "next" returns no task, then stop. Stop if a lease call reports a conflict.

The agent should report `url` (on heartbeat or finish) when its app has a share link for the chat, so the card links to it.

### Test the scheduler

Add Slate to the agent's app using its `/mcp` URL and the agent's token, then save the prompt.
Use a test card and confirm the scheduled run can connect, take it and report back. If the
scheduler cannot reach Slate or use its tools, choose manual delivery. See [MCP](mcp.md) and
[chat apps](chat-apps.md) for connection choices.

## Copy brief (manual)

**Copy brief** puts the task on your clipboard as one prompt (`GET /items/{id}/brief`, MCP
`slate_item brief`). It includes the title, dates, description, checklists, attached files,
recent comments and the list's filing note, if set. The last part explains how to report a
result. Paste the brief into the agent's app.

## Closing lines

Paste the agent's answer back as a comment on the task. If its last line is a `SLATE:` line, that line reports back:

| Last line | From an agent account | From a person |
|---|---|---|
| `SLATE: done: Sent the invoice.` | The agent's `done` report: clears its badge, completes the task if the agent is assigned to it | Completes the task |
| `SLATE: review: Check the draft linked above.` | Sets the agent's review badge; the text says what to check. A bare `SLATE: review` is `done` | Nothing more (the comment is the review) |
| `SLATE: later` | Clears the agent's badge; the task stays open and assigned, and its due time (which must still be to come) runs the agent again | Nothing more |
| `SLATE: answered` | Clears the agent's own badge; nothing else changes | Nothing more |
| `SLATE: ask alex: Start the import now? [Yes \| No \| Not yet]` | The comment becomes the agent's question to Alex: blocked badge with the question, the task waits on Alex, and Alex gets one button per option | Nothing more |

Use the last non-empty line. It can be wrapped in backticks or bold. For `done`, `review`,
`later` and `answered`, the text after the second colon supplies the standing line. If that
text is absent, Slate derives a line from the comment. A bare `review` instead means `done`,
including for a person. For `ask`, the text is a question of up to 200 characters. `@alex`
also works. Options are optional and must number 2 to 4, each up to 40 characters. A line
Slate cannot apply leaves the comment in place without applying the reported outcome. Slate
reads closing lines on ordinary comments. Reports and leased finishes use explicit fields.
