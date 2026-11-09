# Slate over REST when the MCP tools are absent

`POST /api/v1/batch` takes the same tool, action and params as the MCP tools, so one small
script covers every action.

## Your token

Use **your own app's** Slate token, from wherever that app keeps it: the app's `.env`
(`SLATE_TOKEN`), its MCP config, or your secrets manager. Never another agent's token and
never the owner's admin token: every write is recorded as the token's account, so a borrowed
token files work under someone else's name. If your app has no token, say so and stop; making
one is an admin step (`docs/agents/chat-apps.md`).

## Recipe

1. Read the token inside the script. Never echo it, pass it as a tool argument or put it on a
   command line; check that it is present by its length only.
2. Write the request body to a JSON file and POST it from a script (Python `urllib.request` is
   enough) with headers `Authorization: Bearer <token>`, `Content-Type: application/json` and
   `X-Slate-Session-Ref: <your session id>`. Write the response to a file and print only the
   fields you need.
3. Body: `{"ops": [{"ref": "t", "tool": "item", "action": "create", "params": {...}},
   {"tool": "item", "action": "get", "params": {"id": "$t.id"}}]}`. Put a create and its
   read-back in one batch; the `get` result is your check.
4. Unfamiliar list: `{"tool": "list", "action": "list", "params": {}}` gives each list's
   statuses and default status; `{"tool": "tag", "action": "list", "params": {}}` the tags.

## Quirks

- `fields` must be a JSON list (`["id","title","status","due_at"]`); a comma string is a 422.
- An error nests the op: `{"detail": "op 0: ... Nothing was written.", "failed_op": 0}`. A
  batch is all or nothing, so fix it and send the whole batch again.
- A batch's write results show each task as the whole batch left it, not as each op did.
