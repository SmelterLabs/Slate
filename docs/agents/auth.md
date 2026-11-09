# Accounts, tokens and scopes

Every caller is an **account**, `person` or `agent`, with a handle (`alex`, `my-agent`). An
assignee is always an account handle. Every change any account makes is an event with that
account as actor, so each agent's audit trail is `GET /events?actor=<handle>`.

## Tokens

`Authorization: Bearer slate_<token id>_<secret>` on every request (`POST /bootstrap` aside).
Slate keeps only a SHA-256 of the token: the plaintext is shown once, when it is created, and
never again. Lose it and you make a new one.

| Scope | Can |
|---|---|
| `read` | every read: items, check-in, events, the stream, `GET /agents` |
| `write` | `read`, plus every change to items, lists, comments, reports, batch |
| `admin` | `write`, plus accounts and tokens, webhooks of others |

Scopes nest (`admin` > `write` > `read`); a token with too little gets `403`
`token lacks 'write' scope`. Give an agent `write` unless it only watches (`read`) or manages
other agents (`admin`, rarely).

## The easy way: the board's Agents page

**Agents → Add my agent → Let my agent use Slate** (admins only):

1. Handle and display name (a new agent account), or pick an existing agent.
2. Scopes and a label for the token (say which machine or run will hold it).
3. Slate shows the token **once**, with ready-to-paste setup for Claude Code, Python and
   TypeScript using this board's own address.

Each agent's row on the Agents page then shows its tokens (label, scopes, created, last used),
events per day for the last two weeks by source, its busiest event types, and requests and
rate-limit refusals since the server started. From there you can revoke a token, make a new
one (rotate: make the new one, switch the agent, revoke the old) or disable the account.

## The same over the API

```http
POST /accounts                       {"handle": "my-agent", "display_name": "My Agent", "kind": "agent"}
POST /accounts/{id}/tokens           {"scopes": ["write"], "label": "server nightly"}
GET  /accounts/{id}/tokens           list (never the secret), ?include_revoked=true for all
GET  /accounts/{id}/usage?days=14    events per day by source, top types, requests per token
PATCH /accounts/{id}                 {"display_name": "..."} or {"disabled": true | false}
DELETE /tokens/{token_id}            revoke now
```

Creating accounts and tokens and changing accounts need `admin`. Any account can list its own
tokens, read its own usage, and revoke its own token. Disabling an account stops all its tokens
at once and takes it off the roster; its history and assignments stay, and
`{"disabled": false}` brings it back. You cannot disable yourself.

Over MCP these are `slate_account` actions: `me`, `list`, `create`, `create_token`, `tokens`,
`usage`, `update`, `revoke_token`.

## First run

On an empty Slate, `POST /bootstrap {"handle": "alex", "display_name": "Alex"}` creates the
first person with an `admin` token. It answers `410` once any account exists.

## Rate limits

Each token gets 1,200 reads and 300 writes a minute, refilled continuously (a full minute's
burst at once is fine). Every authenticated REST response carries `X-RateLimit-Limit` and
`X-RateLimit-Remaining` for the bucket it spent from. Over the limit: `429` with
`Retry-After` (seconds); over MCP, a tool error saying how long to wait. A `304` answer costs
nothing, and a whole `POST /batch` counts as one request (from the read bucket; each op still
needs its own scope). Settings:
`SLATE_RATE_LIMIT_READS_PER_MINUTE`, `SLATE_RATE_LIMIT_WRITES_PER_MINUTE`,
`SLATE_RATE_LIMIT_ENABLED`.
