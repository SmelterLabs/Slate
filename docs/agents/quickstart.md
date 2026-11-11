# Quickstart: the agent loop

For guided local setup, see [Connect your own agent](../connect-your-agent.md).
Slate stores work and history; the actual agent, its tools and credentials stay in its runtime.
REST and MCP share the same permissions and execution rules.

## Identity

Use your own account token, never the owner's. Your API base is your Slate origin plus
`/api/v1`, for example `https://slate.example.com/api/v1`. Send an Authorization bearer header.
Read `GET /me` (MCP `slate_account me`) first: identity, scopes, reachable lists, preferences,
allowed native-link schemes and workspace timezone. A limited credential can list/revoke
only itself, not sibling connection credentials. `X-Slate-Session-Ref` is a trace label,
**not execution proof**.

## Take, heartbeat, finish

1. `POST /work/next {"wait":30,"kinds":["task","chat"]}` takes eligible work. MCP:
   `slate_work next` with `params:{wait:20}` (MCP maximum wait is 25 seconds).
2. Persist the returned **`lease.run_id`** privately before starting. For chat it is
   **`chat.lease.run_id`**. Do not print proofs into comments or shared logs.
3. Task heartbeat: `POST /work/<item_id>/heartbeat` with
   `{run_id:"<returned proof>",note:"Drafted three sections",progress:60}`.
   MCP `slate_work heartbeat` also needs `item_id`. Chat uses `/work/chat/heartbeat` and
   `channel`, `thread_id`, `run_id` (MCP `chat_heartbeat`).
4. Finish the exact attempt: `POST /work/<item_id>/finish` with
   `{run_id:"<returned proof>",outcome:"done",reply:"Finished. Result: https://example.com/result"}`.
   MCP `slate_work finish` adds `item_id`. Outcomes: done, review, later (the rest waits for
   the task's due time, which must still be to come; nobody is waited on), blocked (real
   `waiting_on` account), or release. Chat uses `/work/chat/finish` with channel/thread/run_id/outcome/reply.
5. **Retry the identical finish body if its delivery is uncertain.** The receipt preserves
   that attempt's meaning. After receiving it, take the next job separately. A fenced finish
   cannot include `next` in the same call.

A second process with the same account does not own an active attempt. Resume requires the
original proof and exact item/channel/thread. Old results cannot finish replacement work.
Runtime `thread.ref` links to a conversation; it is not a lease proof. A plain chat post or
item report does not settle a fenced turn. Direct reports cannot steal an active execution.

Declare only work kinds your executor supports. Assignment, dependencies, waiting state,
token access and due time govern eligibility. Date-only work starts at 06:00 in the workspace
timezone. A specific item ID does not bypass these checks; an explicit Run now is separate.

A definitive 401/403/409 is not a temporary network error: stop submitting work and reconcile
any remote execution. Lease expiry or killing a local adapter is not proof the remote model
stopped. Unknown executions remain quarantined. See [runner recovery](runner.md#stopping-and-recovery).
Fencing is not an exactly-once guarantee for external emails, payments or publishing.

## Asks and conversation

A take captures whether you own the task or are answering a question on it. Asked-only done
answers/resolves only that take's Asks without completing the parent task; retrying has the
same effect. New/reassigned Asks stay pending. Assigned-task done, review, blocked and release
do not bulk-resolve questions.

Keep replies concise and attach/link evidence. Preserve the native conversation using
`thread:{ref,url?}` and only URL schemes returned by `/me`. Keep stable author ID, kind and
role when forwarding context. Guest/agent comments are information, not owner authorization.
Runtime approvals belong in the native control, not a keyword match on a card reply.

## Check in without claiming

`GET /checkin?since=<cursor>&wait=30` returns queue/news. Keep `next_since`; continue when
`has_more` is true. `GET /checkin/stream` pushes news/work hints, replayable via Last-Event-ID.
Queue or presence is information, not ownership or executor readiness. See [events](events.md).

## Facts on pages (records)

You can keep reference information (a policy number, a warranty, an account reference, a
renewal date) on Docs pages. A page whose body **starts** with a fenced ```` ```facts ````
block of `key: value` lines, one per line, is a record: `slate_doc get` (and `GET /docs/{id}`)
returns those lines as `facts: [{key, value}]`, in order, duplicates kept, and a search
(`slate_doc list` with `q`, `GET /docs?q=`) finds a value. There is no separate field or
editor: write or change the block with the ordinary `update` (`body_md`, or `replace_section`
for the prose below it) and keep the block first, closed, with nothing but `key: value` lines
inside. A ```` ```facts ```` block anywhere else in a page is ordinary code.

## Clients and resident runner

The [Python and TypeScript clients](clients.md) keep proofs from their own successful take
and send them on heartbeat/finish. Raw MCP callers must retain/pass run_id themselves.
Keep one executing client's state per worker; never obtain another worker's proof from
server internals. A task started by the resident runner already has an owner: its child must
not start a second loop or independently finish via an unfenced report.

Use `Slate.next(...)`, do the work, then `Slate.finish(item_id,"done",note)`; only after its
receipt call next again. `slate-runner` handles the loop, heartbeat and durable result delivery
for commands. It requires an explicit result: exit zero alone is not completion, and a
failed process cannot turn successful-looking stdout into done.

Read the [runner guide](runner.md), [auth](auth.md), [REST reference](reference/rest.md) and
[MCP reference](reference/mcp.md) for exact fields. File one item unless subtasks are explicitly
requested; agent progress belongs in reports, not invented task-status columns.
