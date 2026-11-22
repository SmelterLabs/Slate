# Slate -> push webhook

This optional adapter relays one account's released alerts to a broker that accepts an
authenticated JSON `POST`. The bridge reads `GET /inbox/alerts` with an ordinary Slate
`read` token. Slate applies quiet hours and groups alerts from an agent turn before releasing
them. The bridge sends levels 2 through 4 at the supplied severity. The broker decides how
to present each level.

Each alert is `POST <--broker><--alert-path>` (default path `/v1/alert`) with
`Authorization: Bearer <the key in --key-file>` and the body
`{"severity": 2-4, "title": "Slate: ...", "body": "...", "click_url": "<board>/#/inbox?task=<id>"}`.
If your broker wants another shape, change `Bridge.publish`.

Use `--reasons` to choose which released reasons to relay. The defaults are mention, reply,
assigned, waiting, due, unblocked, nudge, reminder, health, access, failed, and stalled. Slate provides
the wording for each alert, including escalations and quiet-hours summaries. The bridge
prefixes titles with `Slate: ` and links to the card when the alert names one.

```sh
python integrations/push/bridge.py --once --token-file ~/.slate-push.token \
    --broker https://push.example.com --key-file ~/.push-key \
    --board-url https://slate.example.com
uv run pytest -q integrations/push
```

The example systemd user unit is `deploy/server/slate-push.service`. The first run saves the
current release cursor from `X-Slate-Cursor` and starts from there. An older bridge state file
with a timestamp and event IDs bootstraps the same way. After a broker failure, the next pass
retries from the last cursor saved. The Slate server must provide `/inbox/alerts`.
