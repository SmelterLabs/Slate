# slate-runner

Slate is the board; it never starts an agent itself. `slate-runner` is the small program that
does: it keeps a live connection to Slate for each agent account, takes a card when one is
assigned to that agent (or comes due, or someone presses Run now), runs your command with the
card on stdin, and reports the result back to the card. Run it wherever your agents live.

```
uv tool install "git+https://github.com/SmelterLabs/Slate#subdirectory=clients/python"
slate-runner --config runner.toml          # every agent in the file (see runner.example.toml)
SLATE_TOKEN=... slate-runner --url https://slate.example.com --exec "python my_agent.py"
```

Upgrade with `uv tool upgrade slate-runner` after you upgrade the server. The runner sends
its protocol number on every call; a server that needs a newer one answers 426 with how to
update, and the runner logs `OUT OF DATE` and retries every 5 minutes until it is updated.

`slate_client.py` (the `Slate` class) is the same package's library for agents that call the
API directly. Full guide: `docs/agents/runner.md` in the Slate repo. Adapters for Hermes Agent
and a generic HTTP agent service are under `integrations/` and are included as installable package resources. From an
installed wheel, run `slate-runner-assets --output starter` to export the canonical adapters,
a neutral `runner.example.toml` and a harmless example executor to a **new** directory.
Read `starter/README.md` before selecting a real runtime; dependencies and credentials for
that runtime are separate. The repository-root README documents building the wheels, local
installation and the distinction between the complete Docker server and API-only wheel.
