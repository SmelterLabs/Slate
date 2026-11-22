# MCP server

`src/slate/mcp_tools.py` (the tool table) and `src/slate/mcp_server.py` (the server, both
transports). The agent-facing page is `docs/agents/mcp.md`; the generated action-by-action
reference is `docs/agents/reference/mcp.md`.

## MCP server

Modules: `src/slate/mcp_tools.py` (the tool table: params models, actions, schemas, descriptions)
and `src/slate/mcp_server.py` (backends, the MCP `Server`, markdown resources, the `/mcp` mount, the
`slate-mcp` entry point). Built on the SDK's **low-level `Server`** (`mcp` 2.3, the "v2" API:
handlers are `on_*` constructor arguments) rather than `MCPServer`, because we hand-write the input
schemas, set annotations per tool and build `isError` results ourselves. SDK docs:
<https://py.sdk.modelcontextprotocol.io/>.

**Tools.** One per resource, in `mcp_tools.TOOLS` order (`tests/test_mcp.py` `TOOLS` is the list
of record). There is no `slate_today` tool: overdue and due today are `slate_work next` and
`slate_item list`. `slate://today` still renders, from `TODAY_SPEC`, which is not in `TOOLS`.
A connection lists the core unless told otherwise: `slate_work`, `slate_item`, `slate_comment`,
`slate_search`, `slate_list` (`mcp_tools.CORE`). `slate-mcp --tools all` or `SLATE_MCP_TOOLS=all`
lists every tool. `--tools core,slate_doc,slate_chat` adds the named tools. The flag wins over
the environment variable. HTTP (`install`) reads `SLATE_MCP_TOOLS` only. An unknown name fails
at startup. A call to a tool that exists but is not listed is one line naming `--tools all` /
`SLATE_MCP_TOOLS=all`, not `unknown tool`.

Arguments are `{action, params, session_ref?}`. A single-action tool (`slate_checkin`,
`slate_agents`, `slate_batch`, `slate_events`) takes no `action`. `params` is validated against
that action's Pydantic model (`extra="forbid"`), mostly the REST request models from
`api/schemas.py` plus an `id`.

**Input schema.** MCP requires `type: object` at the root, so the schema is not a bare `oneOf`.
The root is an object whose properties are `action`, `params`, and `session_ref` when any action
writes. `oneOf` sits beside that: each branch is one action (`action` has `const`), and its
`params` carries only that action's fields. `$defs` stay on the root, because a `#/$defs` ref
resolves against the document. Pydantic `title` noise is stripped. A client that ignores `oneOf`
sees an open `params` object rather than every action's fields folded together. Unknown top-level
arguments are refused with a hint to use `params`.

**Defaults.** Multi-action branches omit `default` on direct `params` properties. A client that
fills defaults in would otherwise reset an update or send another action's fields. Defaults stay
on the Pydantic models, on single-action schemas, and inside nested objects and `$defs`.
Explicit irrelevant params still fail. `tests/test_mcp.py` checks each branch, the examples, and
that an update of one field leaves the others.

**One action, two bindings.** Each `Action` has `scope` (read/write/admin), `params`, `local` and
`rest`:

| Mode | Backend | Calls | Actor |
|---|---|---|---|
| HTTP `/mcp` | `LocalBackend` | `local(Local(session, principal, actor), params)`: the REST handler itself, run in-process with an `Auth` built from the caller (see "An action is the REST handler plus an id" below), its answer rendered with the route's `response_model` | token's account, `source=mcp` |
| stdio `slate-mcp` | `RestBackend` | `rest(params)` → one `Rest(method, path, query, body)` request to `SLATE_URL/api/v1/...` | token's account, `source=api` (it is a REST call) |

Both return the same JSON; `compact()` then drops null fields and it goes out as one minified text
block. `ToolError` and `ServiceError` become `isError` results carrying the plain-English message;
REST errors use the response `detail` (FastAPI validation lists flattened to `field: msg`). Anything
else is a protocol error (`-32603`), i.e. a bug.

**An action is the REST handler plus an id.** `mcp_tools.py` does not mirror the REST
layer by hand: a hand-written mirror drifts, and a field added to a REST model is then silently
dropped over MCP. An action is `bind(items_api.update_item, "doc line", params)`: `bind` reads the
handler's route once and derives everything else from it: the scope (the `require(...)`
dependency's), the method and path, and both bindings. The only thing written per action is the
one-line `doc` agents read.

Its params are the REST request model made strict plus the route's path id,
`with_id(ItemPatch, "id", "Item id")`, or for a GET, `query_model(items_api.query_items)`: a model
built from the handler's query parameters with their types, bounds, defaults and descriptions
(`**overrides` add or replace fields, e.g. `fields` as a list). Left out, params default to
`strict(body model)`, else the route's query parameters, else none; a route with a path id needs
them named. Path ids, query parameters and the body are read from the params the same way for both
bindings: from the params field of the same name, or as the binds say (a field name such as
`key="channel"`, or a function of the params such as `fields=_joined("fields")`); a path id that no
field names (`item_id` in `/items/{item_id}`) is the params' `id`.

The `rest` binding (stdio mode) fills the route's path, sends the query parameters that are set and
the params that are body fields; `send=` picks which: `given` (not null, the default), `set` (what
the caller sent, the default for PATCH, so null clears) or `all`. The `local` binding is
`via(handler)`: it fills `Auth` from the caller's principal and actor, the `Session`, the body model
from the params (the REST base of a `with_id` model, or the overlapping fields of a narrow model
such as `slate_comment edit`), and every other parameter as above; it renders the answer with the route's
`response_model`, turns an `HTTPException` into a tool error, answers `empty(params)` for a 204,
and under a batch with `views: "final"` re-marks `ItemOut` answers as `Later`. Which route each
action calls is in the generated `docs/agents/reference/mcp.md`.

**`tests/test_mcp_parity.py` is the guard:** it runs every action's `rest` binding on traceable
sample params and fails when a REST query parameter or body field is never sent, when the action
sends a key the route rejects, or when an MCP param reaches nothing; deliberate differences (the
narrow actions, and `report`'s `claim`, which stays on REST) sit in its small allowlist with a
reason each. It also fails
when an action's scope is not its route's: the MCP door checks `Action.scope` itself, since REST's
`require` never runs there. A few actions pass their own `local=` to `bind`, because their local
path is not one REST handler call: `slate_batch`, `slate_checkin` (long-poll), `slate_work next`
and `finish` and `slate_account me` (the REST route also renews a cookie). The
two-route choices (`slate_share who`/`private`, `slate_automation runs`) are written out as
`Action(doc, scope, params, rest=..., local=...)`. The REST `Query(description=...)` and
request-model descriptions are what agents see in the tool schemas, so describe a parameter
there, once.

**Auth over HTTP.** `BearerGate` (ASGI) wraps the session manager: a missing or bad `Authorization:
Bearer` gets 401 `{"detail"}` with `WWW-Authenticate: Bearer` before MCP parses anything. Each tool
call then re-resolves the token from `ctx.request.headers` and checks the action's scope. The SDK
hands low-level handlers the Starlette request on both protocol eras; a contextvar set in middleware
does *not* reach handlers on the 2025 handshake path, so do not rely on one. `X-Slate-Session-Ref`
works as on REST; the `session_ref` argument wins. The SDK's OAuth `TokenVerifier` was not used: it
assumes an external authorization server and would publish OAuth discovery metadata Slate does not
have.

**Transport settings** (`install()`): stateless (no server-held MCP sessions; every POST stands
alone) and JSON responses (no SSE on `/mcp`; Slate never sends server-initiated requests).
DNS-rebinding protection is off: by default the SDK accepts only `Host: localhost/127.0.0.1`, which
would 421 every request that arrives through a reverse proxy under the server's real host name,
and a rebinding page cannot supply a bearer token anyway.

**Lifespan gotcha.** A mounted MCP app's own lifespan never runs, so `app.py`'s lifespan enters
`app.state.mcp_manager.run()`. Each `create_app()` builds a new manager (`run()` works once per
instance). The `client` test fixture does not run lifespans, so plain REST tests are unaffected; MCP
tests enter `app.state.mcp_manager.run()` themselves.

**stdio.** `slate-mcp` (`mcp_server.main`) reads `SLATE_URL` and `SLATE_TOKEN`, exits with a message
if either is missing, and serves over stdio with an `httpx.AsyncClient`. `--tools` selects the
list (default `SLATE_MCP_TOOLS`, else the core). It never touches the
database, so it runs on any machine that can reach Slate.

**Resources.** `slate://today` and the template `slate://item/{id}`, `text/markdown`, rendered from
the same JSON the today and item reads return (times shown in the workspace timezone). Today is
not a listed tool.

**Testing.** `tests/test_mcp.py`: the SDK `Client` over `streamable_http_client` with an
`httpx2.AsyncClient(transport=httpx2.ASGITransport(app))` (the SDK's client uses **httpx2**, not
httpx). The stdio side runs `Client(build_server(lambda _: RestBackend(...)))` in memory over
`httpx.MockTransport`, once with canned responses and once bridged into the real app through
`TestClient`, which checks every REST path and body.

**How to add a tool or action.** (1) Write or reuse the service function (see "How to add an
endpoint"). (2) In `mcp_tools.py` add `bind(the_api.handler, "what it does")` to the tool's
action dict, with `with_id(TheIn, "id", "Thing id")` as its params when the route has a path id
(or a `P` model of ids, or `query_model(the_api.list_things)`); `tests/test_mcp_parity.py` then
checks the params and scope against the route. A new resource gets a
`ToolSpec` in `TOOLS` with a two-sentence `about`, an `example`, and honest
`read_only`/`destructive`. (3) Call it in `tests/test_mcp.py`, both in the in-process test and in
the stdio bridge test.

**Annotations are per tool, not per action.** `readOnlyHint` (`read_only=True` in `TOOLS`):
`slate_checkin`, `slate_agents`, `slate_health`, `slate_events`, `slate_audit`,
`slate_search`. `destructiveHint: true`: `slate_item` (archive, trash, purge), `slate_comment`,
`slate_chat`, `slate_checklist`, `slate_view`, `slate_automation`, `slate_list` (archive, removes
statuses), `slate_section` (archive), `slate_tag` (merge, delete), `slate_doc`, `slate_batch`,
`slate_export` (empty_trash). Every other tool sets `destructiveHint: false` explicitly (the spec
defaults it to true for tools that are not read-only). `openWorldHint: false` everywhere.
