"""Disposable browser acceptance driver: real local CLI, no model/provider calls."""

import json
import os
import subprocess
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo / "clients/python"))
import slate_connect  # noqa: E402

body = json.loads(sys.stdin.read())
root = Path(body["root"])
cli = repo / "clients/python/slate_connect.py"


def invoke(*args):
    result = subprocess.run(
        [sys.executable, str(cli), "--root", str(root / "connections"), *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        env={k: v for k, v in os.environ.items() if not k.startswith("SLATE_")},
    )
    if result.returncode:
        raise RuntimeError("Local CLI acceptance step failed: " + args[0] + "\n" + result.stderr)
    return result.stdout


if body["action"] == "connect":
    slate_connect._private_dir(root)
    probe = root / "harmless.py"
    probe.write_text(
        "import json,sys\np=json.load(sys.stdin)\n"
        "if 'nonce' in p: print(json.dumps({'ok':True,'nonce':p['nonce']}))\n"
        "else:\n assert p['item']['title']=='Harmless connection acceptance'\n"
        " print(json.dumps({'outcome':'done',"
        "'reply':'Harmless executor round trip completed.'}))\n",
        encoding="utf-8",
    )
    codefile = root / "pairing-code"
    codefile.write_text(body["code"], encoding="utf-8")
    if os.name != "nt":
        codefile.chmod(0o600)
    command = f'"{sys.executable}" "{probe}"'
    try:
        invoke(
            "connect",
            "--url",
            body["url"],
            "--name",
            "browser-proof",
            "--code-file",
            str(codefile),
            "--adapter",
            "custom",
            "--exec",
            command,
            "--probe-command",
            command,
            "--cwd",
            str(root),
        )
    finally:
        codefile.unlink()
    plain = json.loads(invoke("doctor", "browser-proof"))
    assert plain["state"] == "connected", plain
    tested = json.loads(invoke("doctor", "browser-proof", "--test"))
    assert tested["state"] == "ready", tested
    print(json.dumps({"connected": True, "probe": "passed"}))
elif body["action"] == "run":
    invoke("run", "browser-proof", "--once", "--item", body["item_id"])
    print(json.dumps({"run": "finished"}))
else:
    raise ValueError("Unknown acceptance action")
