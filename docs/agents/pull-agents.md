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

> Check my Slate tasks. Use the Slate connector: call slate_work with action "next". If it returns a task, do it, sending progress with slate_work "heartbeat", then call slate_work "finish" with outcome done, review, or blocked with waiting_on set to who you need. Repeat until "next" returns no task, then stop.

The agent should report `url` (on heartbeat or finish) when its app has a share link for the chat, so the card links to it.

### ChatGPT (unverified)

ChatGPT scheduled tasks are reported to use the same tools as an interactive chat, connected apps included (but not voice, file uploads or Custom GPTs), and adding a custom MCP connector is reported to need a paid plan. If that holds: add Slate as a custom connector (its `/mcp` URL and an agent token), then create a scheduled task with the prompt above. **Unverified:** a scheduled task has not been tested against Slate. Check the first run's history on the Agents page.

### Grok (unverified)

It is not confirmed that Grok's scheduled tasks can call a remote MCP server. Until it is, treat Grok as `manual`.

### Claude Desktop (unverified)

Claude Desktop connects to Slate's MCP server ([mcp.md](mcp.md)). Whether it can run a saved prompt on a schedule depends on the version you have. If it can't, use `manual`. A Claude Desktop project you only talk to is a chat app: see [chat-apps.md](chat-apps.md).

## Copy brief (manual)

**Copy brief** puts the task on your clipboard as one prompt (`GET /items/{id}/brief`, MCP `slate_item brief`). The prompt holds the title, due date, description, checklist, attached filess and recent comments, and ends with the run rule: finish with a last line `SLATE: done`, `SLATE: review` or `SLATE: blocked waiting_on <handle>`. Paste it into the agent's app.

## The `SLATE:` closing line

Paste the agent's answer back as a comment on the task. If its last line is a `SLATE:` line, that line reports back:

| Last line | From an agent account | From a person |
|---|---|---|
| `SLATE: done` | The agent's `done` report: clears its badge, completes the task if the agent is assigned to it | Completes the task |
| `SLATE: review` | Sets the agent's review badge | Nothing more (the comment is the review) |
| `SLATE: blocked waiting_on alex` | Blocked badge, and the task waits on Alex | The task waits on Alex |

The line must be the last non-empty line. It may be wrapped in backticks or bold. `waiting on` (with a space) and `@alex` work too. A line that can't apply, such as an unknown handle or a list with no done status, leaves the comment as it is. Notes that Slate writes itself, such as a report's note, are never read for the line.
