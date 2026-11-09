# Agent kit: onboarding, connections, generated reference, clients, integrations

How an agent gets an account, a token and the guide, and what ships beside the server
(clients, the runner package, the Slate skill, the optional adapters under `integrations/`).
The server side of an agent's work is `docs/agents-work-loop.md`; `TECHNICAL-GUIDE.md` has the
architecture, the Decisions log and the index.

## Agent kit: onboarding, usage, generated reference, clients

- **Docs** live in `docs/agents/` (README, quickstart, auth, rest, mcp, events, clients, latency).
  `docs/agents/reference/{rest,mcp,events}.md` are **generated** by `uv run slate docs`
  (`slate/docgen.py`) from the routers (scope from the `require()` closure, ETag from
  `fastlane.CACHEABLE`), `mcp_tools.TOOLS` (each action's REST binding, called with sample params,
  is matched back to a route) and every `noun.verb` string literal under `src/slate` (each must have
  a line in `docgen.EVENT_DOCS`). `tests/test_agent_docs.py` fails when the reference is stale, an
  MCP action maps to no route or a different scope, an event type is undocumented (or documented but
  gone), or a hand-written page names a route that does not exist. **Any PR that adds or changes a
  route, an MCP action, a docstring or an event type must run `uv run slate docs` and commit the
  result.** `slate.api.MODULES` is the router mount order the reference follows.
- **Account admin API:** `GET /accounts/{id}/tokens` (admin or self; never the hash), `PATCH
  /accounts/{id}` (`display_name`, `disabled`; events `account.updated|disabled|enabled`; you cannot
  disable yourself), `GET /accounts/{id}/usage?days=` (events per local day by source, top types,
  live tokens with `last_used_at` plus in-process request counts, `throttled`, the limits). MCP
  `slate_account` has `tokens`, `usage`, `update`, `revoke_token`. Token lists and usage are not
  in `CACHEABLE` (bookkeeping columns change without events).
- **Board:** Agents page → **Add my agent** (`components/agents/ConnectAgent.svelte`: account +
  token + snippets from `lib/agentkit.ts`, which builds them from `location.origin`) and **Manage**
  per agent (`components/agents/AgentManage.svelte`: usage chart, tokens, revoke, disable).
  Admin-only actions are hidden for non-admins; anyone sees their own tokens.
- **Clients:** `clients/python/slate_client.py` (httpx; tested in `tests/test_clients.py` by passing
  the `TestClient` as the client) and `clients/typescript/slate.ts` (fetch; type-checked by the
  board's `svelte-check` via `web/tsconfig.json` and driven against a real server in
  `web/e2e/agent-kit.spec.ts`). Both: ETag cache, check-in cursor, `429` retry, the work loop
  (`next`, `heartbeat`, `finish`, `held`, `work_forever`/`workForever` with a heartbeat thread or
  timer), the agent stream (`agent_stream`/`agentStream`), and `run_forever`/`runForever`.
- **Claude Code:** `integrations/claude-code/mcp.json` and `skills/slate/SKILL.md`.
- **The Slate skill is the one guide for every agent app** (Claude Code, Claude Desktop, Codex,
  Gemini CLI, Cursor, Hermes Agent profiles, any app that loads skills):
  `integrations/claude-code/skills/slate/` (`SKILL.md` + `references/rest-fallback.md`). Its
  frontmatter carries both Claude Code and Hermes Agent keys. Write your own board map (which list
  is for what) into your copy; the repo copy stays generic. Copy the folder into each app's skills
  folder, or on Windows run the installer beside it (`integrations/claude-code/skills/install.ps1`;
  `docs/agents/chat-apps.md` says what it does).
  - **Not covered:** claude.ai account skills (what cloud and claude.ai sessions load) have no
    API; add the skill there by hand. Cloud sessions that cannot reach your server know Slate but
    cannot call it.
  - **Knowing is not connecting:** an app that has the guide but no Slate account or connector
    follows the guide's rule ("no token: say so and stop"). Connecting one is the chat-apps recipe
    (`docs/agents/chat-apps.md`).
- **Chat apps get one account each** (`docs/agents/chat-apps.md`): `kind agent`, `wake manual`, a
  `read,write` token, `prefs.owner`. Claude Code connects per project with `claude mcp add --scope
  local` (the token lives in that user's `.claude.json`, so each project folder can carry its own
  key; a second config root needs the same add under `CLAUDE_CONFIG_DIR`). Cloud-hosted chats and
  vendor-brokered "custom connectors" cannot reach a server on a private network; that is by
  design.
- **Benchmarks:** `scripts/bench_agent.py` (the loop, batch, MCP, SSE delivery; `--markdown` prints
  the table in `docs/agents/latency.md`) and `scripts/bench_api.py` (both turn rate limits off for
  seeding).
- **Path spelling:** `POST /recurrences/{id}/run-now` (kebab like every other path).

## Agent connections and portable installation

The user flow is in [Connect your own agent](connect-your-agent.md). The private connection
service (`connection_models.py`, `service/connections.py`, `api/connections.py`) separates
account identity from local executor setup. Pairings are short-lived, hashed and single-use;
issued tokens are bound to a connection and selected lists. Connection events carry no
credentials, local paths or raw diagnostics. A bounded health event keeps SSE-only panels
fresh; an explicit `probe_passed` records verification, while subsequent `ready` beats keep
the connection live without repeatedly invoking a model. Failed checks invalidate the test.

The portable root Compose is independent of `deploy/server`. Server wheels include migration
resources; the complete board still comes from Docker or a separately built frontend. The
runner packages canonical adapters/examples and the `slate-connect` command. REST and local MCP
share self-metadata serialization (prefs, allowed URL schemes and workspace timezone). New
recurrence rules default to the configured timezone; stored rules are not rewritten.

## Optional section-chat exec adapter

The optional Hermes Agent gateway adapter in `integrations/hermes/run_task.py` dispatches
non-card `chat` stdin payloads using the `ChatTurnOut` contract. Its card path is separate;
`src/` and the generic runner need no adapter-specific knowledge. See
`integrations/hermes/README.md` for setup and the full contract.

Chat sessions persist as atomic session-ID-only files under
`<HERMES_STATE>.chat-sessions/`, keyed by Slate origin, gateway/profile, authenticated
agent account, channel and thread. A lease/account mismatch stops before the gateway call.
The generic runner owns chat claim/heartbeat/finish and remains the only reply publisher.
Only a nonempty final `SLATE: done` yields `done` plus `reply`; incomplete/failed/approval
turns release with no public reply. Chat turns do not touch card state or approval sidecars.

Message author kinds come from the access-filtered read roster, not the message body.
A guest person may ask bounded project questions without gaining owner/admin authority;
an agent's words remain context. Owner authority requires a roster-confirmed person matching
`HERMES_CHAT_OWNER` (account ID preferred) or the agent's configured `prefs.owner`.
The roster omits roles, so this pin must be verified by the operator, not guessed from
`kind=person`. Prompt labels do not replace per-profile tool/credential isolation.
Do not stop a shared runner while any adapter/remote turn is active.
