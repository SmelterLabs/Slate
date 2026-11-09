# Clients and the Claude Code kit

Ready-made clients and an installable runner. The clients retain only the execution proofs
returned by their own takes; heartbeat and finish include them automatically. A fenced finish
cannot combine taking the next task. Use the same release as the server. Raw MCP callers
retain/pass `run_id` explicitly.

## Python: `clients/python/slate_client.py`

Needs `httpx`. Tested against the real app in `tests/test_clients.py`.

```python
import os

from slate_client import Slate

slate = Slate("https://slate.example.com", os.environ["SLATE_TOKEN"], session_ref="nightly-42")

task = slate.next(wait=30)  # your best free task, taken with a lease (item None: none came)
while task["item"]:
    item = task["item"]
    slate.heartbeat(item["id"], note="Halfway", progress=50)
    slate.finish(item["id"], "done", "Result: https://...")
    task = slate.next(wait=30)

slate.work_forever(handle)  # or let it run the loop; see its docstring
```

| Method | Call |
|---|---|
| `me()` | `GET /me` |
| `next(wait=0, **opts)` | `POST /work/next`: take your best free task |
| `heartbeat(id, **kw)` | `POST /work/{id}/heartbeat` (`Conflict`: you lost it) |
| `finish(id, outcome, note, **kw)` | `POST /work/{id}/finish` (then call `next` separately) |
| `held()` | `GET /work` |
| `checkin(limit=50)` | `GET /checkin?since=` (follows `has_more`, keeps `since`) |
| `agents()` | `GET /agents` |
| `items(**filters)`, `item(id)` | `GET /items`, `GET /items/{id}` |
| `create(list_id, title, **fields)` | `POST /items` (one item, never children) |
| `update(id, **changes)` | `PATCH /items/{id}` |
| `comment(id, body_md)` | `POST /items/{id}/comments` |
| `report(id, state, note, **kw)` | `POST /items/{id}/report` |
| `claim`, `progress`, `block`, `review`, `done` | `report` with that state (`claim=True` for claim) |
| `batch(ops, atomic=True, views="final")` | `POST /batch` |
| `agent_stream(since, pool=False)` | `GET /checkin/stream`: `news` and `work` messages |
| `events(since, **filters)`, `stream(since, **filters)` | `GET /events`, the SSE stream |
| `work_forever(handle, wait=30, lease_seconds=900, once=False)` | the work loop, heartbeats included |
| `run_forever(handle, interval=30, once=False)` | the work loop with a plain `handle(item)`; `on_news` sees each check-in news entry |

It keeps one keep-alive connection, sends `X-Slate-Session-Ref`, caches `ETag`s (a `304`
returns the remembered body), retries `429` after `Retry-After`, and raises `NotFound`,
`Conflict`, `Forbidden`, `Unauthorized`, `Invalid`, `RateLimited` (all `SlateError` with
`.status` and `.detail`). Resume a cursor across runs with `Slate(..., since=saved)`.

## The runner: `clients/python/slate_runner.py`

Runs a command per task, started by Slate's stream: when a task is assigned, comes due or
someone presses Run now. See [runner.md](runner.md).

## TypeScript: `clients/typescript/slate.ts`

Needs only `fetch` (Node 18+, Deno, Bun, browsers). Same methods in camelCase
(`workForever`, `agentStream` and `stream` as async generators), errors as `SlateError` with
`status`. Tested
against a real server in `web/e2e/agent-kit.spec.ts` and type-checked with the board.

```ts
import { Slate, SlateError } from './slate';

const slate = new Slate('https://slate.example.com', process.env.SLATE_TOKEN!, { sessionRef: 'nightly-42' });
await slate.workForever(async ({ item, context }) => {
  // do the work: the task is yours (lease kept by a heartbeat) until this returns
  return 'Done: https://...';
});
```

## Claude Code: `integrations/claude-code/`

- `mcp.json`: the MCP server entry (HTTP, token from `SLATE_TOKEN`). Use it as a project's
  `.mcp.json`, or run the `claude mcp add` line in [mcp.md](mcp.md).
- `skills/slate/SKILL.md`: the one Slate guide for any chat session (Claude Code, Claude
  Desktop, Hermes): filing a task and where it goes, reading or working a task by id, comments,
  status, ids and links, and the runner loop (`slate_work` next, heartbeat, finish). `references/`
  holds the REST fallback. Copy the `slate` folder into your agent app's skills folder, or on
  Windows run `skills/install.ps1` ([chat-apps.md](chat-apps.md#connect-the-app) says what it
  does).

The board's **Agents → Add my agent** dialog prints the MCP and client snippets with a fresh
token and the board's address filled in.
