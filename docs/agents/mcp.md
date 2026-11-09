# MCP: Slate as tools

Slate is an MCP server with one tool per resource (`slate_item`, `slate_comment`,
`slate_checkin`, ...). Every tool and action, with the REST call it matches, is in
[reference/mcp.md](reference/mcp.md) (generated).

## Connect Claude Code (HTTP, recommended)

The server is part of Slate itself, at `/mcp` (streamable HTTP, stateless, JSON responses).
Make a token on the board's Agents page ([auth.md](auth.md)), then either:

```sh
claude mcp add --transport http --scope user slate \
  https://slate.example.com/mcp \
  --header "Authorization: Bearer $SLATE_TOKEN"
```

or put [`integrations/claude-code/mcp.json`](../../integrations/claude-code/mcp.json) in the
project as `.mcp.json` and export `SLATE_TOKEN` (and optionally `SLATE_SESSION_REF`, which
lands on every event the session writes):

```json
{
  "mcpServers": {
    "slate": {
      "type": "http",
      "url": "https://slate.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${SLATE_TOKEN}",
        "X-Slate-Session-Ref": "${SLATE_SESSION_REF:-claude-code}"
      }
    }
  }
}
```

Then copy [`integrations/claude-code/skills/slate/`](../../integrations/claude-code/skills/slate/)
to `~/.claude/skills/slate/` (or the project's `.claude/skills/`): the skill teaches Claude the
loop in [quickstart.md](quickstart.md). `/mcp` in Claude Code should list `slate` as
connected, with the `slate_*` tools.

The board's **Agents → Add my agent** dialog prints both snippets with the token and this
board's address filled in.

## Other MCP clients

Any client that speaks streamable HTTP and can send a header works: URL
`https://<slate>/mcp`, header `Authorization: Bearer <token>`. A missing or bad token is a
plain `401` before MCP starts.

## stdio (`slate-mcp`)

For clients that only run local commands, the `slate-mcp` console script (in the Slate
package) speaks MCP over stdio and forwards each call to the REST API:

```sh
SLATE_URL=https://slate.example.com SLATE_TOKEN=slate_... \
  uvx --from git+https://github.com/SmelterLabs/Slate slate-mcp
```

Same tools, same results. Writes made this way are recorded with source `api` (they arrive
over REST); over HTTP they are `mcp`.

## How a call looks

```json
{"name": "slate_item",
 "arguments": {"action": "report",
               "params": {"id": "Ab12Cd34Ef56", "state": "working", "note": "On it"},
               "session_ref": "run-42"}}
```

- `action` picks the operation; single-action tools (`slate_today`, `slate_checkin`,
  `slate_agents`, `slate_batch`) take only `params`.
- `params` holds that action's fields; unknown fields are an error that names them.
- `session_ref` (optional, write tools) is recorded on every event the call writes.
- Results are JSON text with null fields dropped. Errors are tool results with
  `isError: true` and a plain-English message (not found, missing scope, rate limited...).
- Tool annotations mark read-only tools, so clients can auto-approve them.

## Resources

`slate://today` and `slate://item/{id}` render as Markdown for clients that read resources.
