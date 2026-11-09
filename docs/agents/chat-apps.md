# Chat apps: Slate in a conversation, without a runner

Often an agent on Slate is a chat session someone is talking to: Claude Code or
Claude Desktop in a project folder, a Hermes chat, any app that speaks MCP. It files tasks,
reads one by id, comments and changes status while the person watches. It is never woken by
Slate, so it needs no runner ([runner.md](runner.md)) and no scheduler.

## One account per app

Give each chat app its own account and token, so the event trail says which app did what and
one app's token can be revoked without touching the others.

| Setting | Value |
|---|---|
| `kind` | `agent` |
| `handle` | the app or project, lower case (`my-agent`) |
| `wake` | `manual`: nobody wakes it; the card's button is **Copy brief**, a prompt to paste into the chat ([pull-agents.md](pull-agents.md)) |
| token scopes | `read` and `write` |
| token label | the app and machine (`laptop`) |
| `prefs.owner` | the person who uses the app, so a nudge reaches them |

With the admin token (or the board's **Agents → Add my agent**, then set Wake to manual):

```http
POST /accounts            {"handle": "my-agent", "display_name": "My Agent", "kind": "agent", "wake": "manual"}
POST /accounts/{id}/tokens {"scopes": ["read", "write"], "label": "laptop"}
```

Then, with the new token, `PATCH /me/prefs {"owner": "alex"}` and `GET /me` to prove it.

## Connect the app

- **Claude Code / Claude Desktop (Code tab), one project only:** from the project folder run
  `claude mcp add --transport http --scope local slate https://<slate>/mcp --header
  "Authorization: Bearer <token>" --header "X-Slate-Session-Ref: <handle>"`. Local scope keeps the
  token in that user's `.claude.json` for that folder only, never in the repo, so another project
  can carry its own key. A folder that runs under a second config root (`CLAUDE_CONFIG_DIR`) needs
  the same command run with that variable set. `claude mcp get slate` should say Connected.
- **Hermes:** an `mcp_servers.slate` block in the profile's `config.yaml` with the token from the
  profile's `.env`; the gateway loads it on restart.
- **Anything else with MCP over HTTP:** URL `https://<slate>/mcp`, header
  `Authorization: Bearer <token>` ([mcp.md](mcp.md)).

Then give the app the Slate skill, `integrations/claude-code/skills/slate/`: the one guide for
filing, reading and working tasks in a chat. Copy that folder into the app's skills folder (for
Claude Code, `~/.claude/skills/slate/`). On Windows, `pwsh
integrations/claude-code/skills/install.ps1` does the copying: it takes the skill from a git ref
(`-Ref`, default `origin/main`) and installs it into the skills folders it finds on the machine
for Claude Code, Codex, Gemini CLI, Cursor and Hermes Agent, moving any older copy to a backup
folder first. `-RemoteHost <host>` (repeatable) also installs it over ssh on a Linux or macOS
host, `-BoardUrl` names your board in the pointer it writes for Codex, and `-WhatIf` shows what
it would do without doing it.

## Proving it

From a session in that app: `slate_account me` (the handle is the app's), `slate_item create` a
throwaway item, `get` it (its `created_by` is the app's account), then `delete` it (allowed for
your own create within 10 minutes).

## What can't reach Slate

Slate usually listens on a private network (a VPN such as Tailscale, or a LAN). Apps that run on a vendor's cloud, such as
claude.ai on the web, ChatGPT or Grok in a browser, cannot reach it, and Slate is not meant to be
exposed to the internet to make them. The same goes for a "custom connector" added in such an
app's settings, because the vendor's servers make that connection. What works is an app running
on a machine on the network: Claude Code, Hermes, or a desktop app that starts a local MCP server
(`slate-mcp` over stdio, [mcp.md](mcp.md)). Otherwise paste the task text into a connected app.
