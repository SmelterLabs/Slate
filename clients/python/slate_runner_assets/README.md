# Local runner starter assets

These files are exported from the installed `slate-runner` distribution. The adapters
are the repository's canonical sources, included at build time without a maintained copy.

1. Install the matching runner wheel into a Python 3.11+ virtual environment.
2. Activate it, then run `slate-runner-assets --output starter` (destination must be new).
3. Copy `runner.example.toml` to `runner.toml`, set your local server URL, and set
   `SLATE_TOKEN` in the process environment to a scoped **agent** token. Do not use an
   owner/admin token. Keep this directory private: cursors and runtime state are stored here.
4. Run `slate-runner --config starter/runner.toml`. The example only emits progress and a
   done result; it does not execute AI or prove provider readiness. Use a disposable list.
5. Stop the runner before changing the executor. Inspect adapter headers and the source
   repository's integration docs before selecting a real runtime. The runtime must already
   be installed and configured; this distribution does not install or log into it.

`hermes/run_task.py` requires `HERMES_API_URL` and (when required by that server)
`HERMES_API_KEY`. `hermes/run_task_cli.py` requires the separately installed CLI on PATH
(or `HERMES_CLI`) and an explicitly selected `HERMES_PROFILE`; keep its sibling
`run_task.py`. Set `HERMES_ASK` explicitly to your owner handle, not a deployment default.
`runner-example/run_task.py` is a worked example against a made-up HTTP agent service: adapt
it to your service and set its `RUNNER_*` environment (see its header) before running. No tokens or runtime credentials ship here.

The `httpx` dependency is installed with the runner. Adapter imports and a synthetic
example invocation are packaging checks, not remote stop/approval/chat acceptance.
Do not run a second resident runner for the same account while one owns live work.
Export to a new directory for upgrades and review diffs before replacing configured files.
