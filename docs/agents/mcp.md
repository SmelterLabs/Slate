# MCP: Slate as tools

Slate is an MCP server with one tool per resource (`slate_item`, `slate_comment`,
`slate_work`, ...). A fresh connection lists five tools: `slate_work`, `slate_item`,
`slate_comment`, `slate_search`, `slate_list`. That is enough to take work, file a card,
comment, and search. Every other tool is listed when the server is started with
`--tools all` (or `SLATE_MCP_TOOLS=all`), or named: `--tools core,slate_doc,slate_chat`.
The HTTP server reads `SLATE_MCP_TOOLS` only (`slate serve` has no `--tools` flag). A call
to a tool that exists but is not listed is a one-line error naming that flag. A name that
is not a tool at all is `unknown tool`. Every tool called inside `slate_batch` must also be
listed for that connection.

`slate_today` is not a tool. Overdue and due today are `slate_work next` and
`slate_item list`. The `slate://today` resource is still there. `slate_item` has no
`assign`, `tag`, `set_status`, or `comment` action: update the item, or add a comment with
`slate_comment`. `slate_work finish` does not take a next task; call `next` again.
`plan`, `chat_clear`, and `doc_clear` are not agent tools (the board still has
`GET /agents/plan` and the clear routes).

Every tool and action, with the REST call it matches, is in
[reference/mcp.md](reference/mcp.md) (generated). The short loop is
[the skill](../../integrations/claude-code/skills/slate/SKILL.md).

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

- `action` picks the operation. A tool's schema is a `oneOf` of those actions under a
  root `type: object` (clients require an object at the root). Each branch lists only
  that action's fields. A tool with one action (`slate_checkin`, `slate_agents`,
  `slate_batch`) takes only `params`.
- `params` holds that action's fields; unknown fields are an error that names them.
- `session_ref` (optional, write tools) is recorded on every event the call writes.
- Results are JSON text with null fields dropped. Errors are tool results with
  `isError: true` and a plain-English message (not found, missing scope, rate limited...).
- Tool annotations mark read-only tools, so clients can auto-approve them.

## Resources

`slate://today` and `slate://item/{id}` render as Markdown for clients that read resources.
