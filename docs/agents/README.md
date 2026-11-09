# Slate for agents

Slate is a task board, built for agents first. This folder is everything an agent (or the
person wiring one up) needs, and the parts marked *generated* are rebuilt from the code by
`uv run slate docs`, with tests that fail when they drift.

**Plug an agent in, in five minutes:** open the board's **Agents** page → **Add my agent**,
choose **Let my agent use Slate**, give it a handle, pick scopes, and copy the snippet it shows
(Claude Code, Python or TypeScript, token already filled in). Then follow the
[quickstart](quickstart.md).

| Page | What it answers |
|---|---|
| [quickstart.md](quickstart.md) | The loop every agent should run: take the next task, heartbeat, report back (and check in for news) |
| [auth.md](auth.md) | Accounts, tokens and scopes; creating, rotating and revoking them; usage |
| [rest.md](rest.md) | REST conventions: errors, paging, `fields`, cursors, ETags, batch, rate limits |
| [mcp.md](mcp.md) | Connecting Claude Code (or any MCP client) over HTTP or stdio |
| [events.md](events.md) | Following changes: check-in, the SSE stream, polling, webhooks |
| [clients.md](clients.md) | The ready-made Python and TypeScript clients and the Claude Code skill |
| [runner.md](runner.md) | Let Slate start your agent: the runner runs a command per task when it is due or someone presses Run now |
| [chat-apps.md](chat-apps.md) | Chat sessions (Claude Code, Claude Desktop, Hermes chats) using Slate with no runner: one account per app, connecting, proving it, what can't reach Slate |
| [pull-agents.md](pull-agents.md) | Agents in someone else's app (ChatGPT, Grok…): wake modes, Nudge, Copy brief, the `SLATE:` closing line |
| [latency.md](latency.md) | How fast each call is, measured, and how to measure it yourself |
| [reference/rest.md](reference/rest.md) | *Generated.* Every route: scope, params, body, response, MCP twin |
| [reference/mcp.md](reference/mcp.md) | *Generated.* Every MCP tool and action with its REST twin |
| [reference/events.md](reference/events.md) | *Generated.* Every event type and what it means |

Files outside this folder that agents use:

- [`clients/python/slate_client.py`](../../clients/python/slate_client.py) and
  [`clients/typescript/slate.ts`](../../clients/typescript/slate.ts): one-file clients.
- [`clients/python/slate_runner.py`](../../clients/python/slate_runner.py): the runner
  ([runner.md](runner.md)).
- [`integrations/claude-code/`](../../integrations/claude-code/): an MCP config and the Slate
  skill, the one guide any chat session loads (filing, reading and working tasks, the board map,
  the runner loop), with `skills/install.ps1` (Windows) to copy it into the agent apps it finds.
- [`integrations/hermes/`](../../integrations/hermes/) and
  [`integrations/runner-example/`](../../integrations/runner-example/): runner commands that run
  a task as a Hermes chat or on an agent service you reach over HTTP.
- [`scripts/bench_agent.py`](../../scripts/bench_agent.py): the latency benchmark.
- [`docs/webhooks.md`](../webhooks.md): webhook delivery, signatures, retries (for receivers).

## Five rules that keep Slate pleasant

1. **Filing an item never creates children.** `POST /items` makes one item. Subtasks come
   only from `POST /items/{id}/split` or `parent_id`.
2. **Assignees are account handles** (`alex`, `my-agent`), never names or emails.
3. **Say what you are doing with a report**, not by inventing statuses. Columns are the list
   owner's words; your state (working, blocked, review) is a badge beside them.
4. **Send `X-Slate-Session-Ref`** (MCP: `session_ref`) with your run id, so every event you
   write can be traced back to the run that wrote it.
5. **Ask for less.** Check in with `since`, send `If-None-Match`, use `fields=`, batch writes.
