# Slate -> push webhook

Optional adapter: one account's Slate notifications become alerts on a push-notification
broker of your choice (anything that accepts an authenticated JSON `POST`). Slate supplies
the loudness. The bridge relays levels 2 through 4 at that severity and skips levels 0 and 1.
An older server's entries without a loudness use severity 2. The broker decides how to
present each level. The bridge uses only the Python standard library and Slate's public REST API.

Each alert is `POST <--broker><--alert-path>` (default path `/v1/alert`) with
`Authorization: Bearer <the key in --key-file>` and the body
`{"severity": 2-4, "title": "Slate: ...", "body": "...", "click_url": "<board>/#/inbox?task=<id>"}`.
If your broker wants another shape, change `Bridge.publish`.

Reasons sent (`--reasons`, default): mention, reply, assigned, waiting, due, unblocked, nudge,
reminder, health, access, failed (no chat, no daily digest). A waiting alert's body is the
question, then the card title. An escalation adds “Still waiting” to the original heading
and keeps its body when the original entry is still available. A failed alert is titled `Slate: <who> could not run`. Titles
start `Slate:` so a broker rule can cap or mute them. A tap opens the card in the Slate inbox.

The token is an ordinary `read` token of the account whose inbox it relays (never an admin
one): it reads `/inbox` and `GET /agents?kind=all` for the names
in the titles. A name it cannot find reads "Someone"; the alert still goes.

```sh
python integrations/push/bridge.py --once --token-file ~/.slate-push.token \
    --broker https://push.example.com --key-file ~/.push-key \
    --board-url https://slate.example.com
uv run pytest -q integrations/push
```

On a server: `deploy/server/slate-push.service` (systemd user unit). The first run records
the cursor and sends nothing from the backlog.
