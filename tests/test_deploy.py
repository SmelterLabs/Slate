"""Static checks on the server deploy pack (deploy/server). Nothing here needs a server."""

from __future__ import annotations

import configparser
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from slate.settings import Settings

PACK = Path(__file__).resolve().parents[1] / "deploy" / "server"
SCRIPTS = ["deploy.sh", "rollback.sh", "backup.sh"]
COMPOSE = "/usr/bin/docker compose -p slate"


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str  # systemd keys are case-sensitive
    parser.read_string((PACK / name).read_text(encoding="utf-8"))
    return parser


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_parses_with_bash_n(script):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    # Fed on stdin so a Windows bash never has to translate the path.
    source = (PACK / script).read_text(encoding="utf-8")
    result = subprocess.run([bash, "-n"], input=source, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_is_strict_and_lf(script):
    raw = (PACK / script).read_bytes()
    assert b"\r\n" not in raw  # CRLF breaks bash on the server
    text = raw.decode()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    assert text.rstrip().endswith('main "$@"')  # parsed fully before git checkout rewrites it


def test_slate_service_unit():
    unit = _unit("slate.service")
    svc = unit["Service"]
    assert svc["Type"] == "oneshot"
    assert svc["RemainAfterExit"] == "yes"
    assert svc["WorkingDirectory"] == "/opt/slate/repo/deploy/server"
    assert svc["ExecStart"] == f"{COMPOSE} up -d --build --remove-orphans"
    assert svc["ExecReload"] == svc["ExecStart"]
    assert svc["ExecStop"] == f"{COMPOSE} down"
    assert unit["Install"]["WantedBy"] == "default.target"
    # A bare `$(` would be eaten by systemd's variable expansion.
    assert "$(" not in svc["ExecStartPre"].replace("$$(", "")


def test_slate_service_runs_the_container_as_the_service_user():
    env = _unit("slate.service")["Service"]["Environment"].split()
    assert {"SLATE_UID=%U", "SLATE_GID=%G"} <= set(env)


def _compose() -> dict:
    import yaml

    return yaml.safe_load((PACK / "compose.yml").read_text(encoding="utf-8"))


def test_compose_runs_unprivileged():
    svc = _compose()["services"]["slate"]
    assert svc["user"] == "${SLATE_UID:-10001}:${SLATE_GID:-10001}"
    assert svc["cap_drop"] == ["ALL"]
    assert svc["security_opt"] == ["no-new-privileges:true"]


def test_dockerfile_runs_as_a_user_without_uv():
    text = (PACK.parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert ":latest" not in text
    assert re.search(r"COPY --from=ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+ /uv /bin/uv", text)
    runtime = text.rsplit("\nFROM ", 1)[1]  # the last stage is what runs
    body = re.sub(r"UV_\w+|\.venv|uvicorn", "", runtime.split("\n", 1)[1])
    assert "uv" not in body  # no uv or uvx in the image that runs
    users = re.findall(r"^USER (\S+)", runtime, re.M)
    assert users and users[-1].split(":")[0] not in ("0", "root")
    assert "-o 10001 -g 10001 -m 0700 /data" in runtime


def test_data_and_backups_are_private_to_the_service_user():
    deploy = (PACK / "deploy.sh").read_text(encoding="utf-8")
    assert 'chmod 700 "$home/data" "$home/backups"' in deploy
    # Files an older, root container left in data/ are handed to the service user once.
    assert '! -user "$(id -u)"' in deploy and "--user 0:0" in deploy
    runbook = (PACK / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "install -d -m 0750 -o slate -g slate /opt/slate" in runbook


def test_forwarded_for_comes_from_the_proxy_not_the_client():
    """A client-supplied X-Forwarded-For entry never chooses the address the limiters key on,
    with a proxy that appends the real client (nginx's $proxy_add_x_forwarded_for)."""
    import asyncio

    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    trusted = _compose()["services"]["slate"]["environment"]["FORWARDED_ALLOW_IPS"]
    assert trusted != "*"
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["client"][0])

    async def run():
        mw = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
        # A proxy over the Docker bridge (either of Docker's default pools) or loopback.
        for peer in ("172.18.0.1", "192.168.64.1", "127.0.0.1"):
            for forged in ("198.51.100.1", "198.51.100.2", "10.0.0.9"):
                xff = f"{forged}, 203.0.113.7".encode()
                scope = {
                    "type": "http",
                    "client": (peer, 5000),
                    "headers": [(b"x-forwarded-for", xff), (b"x-forwarded-proto", b"https")],
                }
                await mw(scope, None, None)

    asyncio.run(run())
    assert seen == ["203.0.113.7"] * 6


def test_backup_units():
    svc = _unit("slate-backup.service")["Service"]
    assert svc["Type"] == "oneshot"
    assert svc["ExecStart"] == "/bin/bash /opt/slate/repo/deploy/server/backup.sh"
    timer = _unit("slate-backup.timer")
    assert timer["Timer"]["OnCalendar"] == "*-*-* 03:30:00"
    assert timer["Timer"]["Persistent"] == "true"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_backup_uses_online_backup_api_and_prunes_30_days():
    text = (PACK / "backup.sh").read_text(encoding="utf-8")
    # sqlite3 online backup API inside the container, never cp of a WAL database
    assert ".backup(dst)" in (PACK / "backup.py").read_text(encoding="utf-8")
    assert "cp " not in text
    assert "date +%F" in text  # dated on the host, passed in as argv
    assert "-mtime +30" in text and "/opt/slate/backups" in text


# --- the scripts, run for real against stand-ins for docker, systemctl and curl -------------

FAKE_DOCKER = """#!/usr/bin/env bash
# `docker exec -i slate python - ARGS` runs backup.py here; `docker inspect` says FAKE_RUNNING.
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  exec) shift; [[ "$1" == -i ]] && shift; shift; shift; exec "$FAKE_PYTHON" "$@" ;;
  inspect) echo "${FAKE_RUNNING:-true}" ;;
  *) exit 0 ;;
esac
"""
FAKE_TOOL = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$FAKE_LOG"
"""
FAKE_PYTHON3 = """#!/usr/bin/env bash
exec "$FAKE_PYTHON" "$@"
"""
FAKE_CURL = """#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
printf '{"ok":true,"rev":"7","commit":"%s"}' "$FAKE_COMMIT"
"""


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    return bash


def _home(tmp_path: Path) -> Path:
    """A SLATE_HOME with data/ and a bin/ of stand-ins first on PATH."""
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    fakes = tmp_path / "bin"
    fakes.mkdir()
    for name, body in [
        ("docker", FAKE_DOCKER),
        ("systemctl", FAKE_TOOL),
        ("curl", FAKE_CURL),
        ("python3", FAKE_PYTHON3),
    ]:
        (fakes / name).write_text(body, encoding="utf-8", newline="\n")
        (fakes / name).chmod(0o755)
    return home


def _run(tmp_path: Path, script: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    path = os.pathsep.join([str(tmp_path / "bin"), os.environ.get("PATH", "")])
    full = {
        **os.environ,
        "PATH": path,
        "SLATE_HOME": home.as_posix(),
        "SLATE_DATABASE_PATH": str(home / "data" / "slate.db"),
        "FAKE_PYTHON": Path(sys.executable).as_posix(),
        "FAKE_LOG": (tmp_path / "calls.log").as_posix(),
        **env,
    }
    return subprocess.run(
        [_bash(), (PACK / script).as_posix(), *args],
        env=full,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _slate_db(path: Path, revision: str = "b4f8d2e6a1c9") -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    con.execute("CREATE TABLE items (id TEXT PRIMARY KEY, title TEXT)")
    con.executemany("INSERT INTO items VALUES (?, ?)", [(str(i), f"task {i}") for i in range(50)])
    con.commit()
    con.close()


def _status(home: Path) -> dict:
    return json.loads((home / "data" / "backup-status.json").read_text(encoding="utf-8"))


def test_backup_refuses_a_missing_database_and_says_so(tmp_path):
    home = _home(tmp_path)
    result = _run(tmp_path, "backup.sh", "slate-2026-11-13.db")
    assert result.returncode != 0
    assert "no database at" in result.stderr
    assert not (home / "data" / "slate.db").exists()  # no stray empty database was created
    assert not list((home / "backups").iterdir())
    status = _status(home)
    assert status["ok"] is False and "no database at" in status["detail"]


def test_backup_refuses_a_database_that_is_not_slate(tmp_path):
    home = _home(tmp_path)
    sqlite3.connect(home / "data" / "slate.db").close()  # exists, empty: integrity "ok"
    result = _run(tmp_path, "backup.sh", "slate-2026-11-13.db")
    assert result.returncode != 0
    assert "not a migrated Slate database" in result.stderr
    assert not list((home / "backups").iterdir())
    assert _status(home)["ok"] is False


def test_backup_lands_outside_the_data_volume_and_prunes(tmp_path):
    home = _home(tmp_path)
    _slate_db(home / "data" / "slate.db")
    backups = home / "backups"
    backups.mkdir()
    old = backups / "slate-2026-10-01.db"
    old.write_bytes(b"old")
    month_ago = time.time() - 40 * 86400
    os.utime(old, (month_ago, month_ago))
    keep = backups / "notes.txt"  # not a backup: never pruned
    keep.write_text("x")
    os.utime(keep, (month_ago, month_ago))

    result = _run(tmp_path, "backup.sh", "slate-2026-11-13.db")
    assert result.returncode == 0, result.stderr
    made = backups / "slate-2026-11-13.db"
    con = sqlite3.connect(made)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT count(*) FROM items").fetchone()[0] == 50
    con.close()
    assert not old.exists() and keep.exists()
    assert sorted(p.name for p in backups.iterdir()) == ["notes.txt", "slate-2026-11-13.db"]
    assert not (home / "data" / "backups").exists()
    status = _status(home)
    assert status["ok"] is True and status["last_ok_at"] == status["at"]
    if os.name == "posix":
        assert stat.S_IMODE(backups.stat().st_mode) == 0o700
        assert stat.S_IMODE(made.stat().st_mode) == 0o600


def test_a_failed_backup_keeps_the_last_good_time(tmp_path):
    home = _home(tmp_path)
    _slate_db(home / "data" / "slate.db")
    assert _run(tmp_path, "backup.sh", "slate-2026-11-12.db").returncode == 0
    good = _status(home)["last_ok_at"]
    (home / "data" / "slate.db").unlink()
    assert _run(tmp_path, "backup.sh", "slate-2026-11-13.db").returncode != 0
    status = _status(home)
    assert status["ok"] is False and status["last_ok_at"] == good


def test_scripts_always_name_the_compose_project():
    for path in PACK.iterdir():
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"docker compose\s", line) and not line.lstrip().startswith("#"):
                assert "-p slate" in line, f"{path.name}: {line}"


def test_env_example_covers_every_setting():
    lines = (PACK / "env.example").read_text(encoding="utf-8").splitlines()
    keys = {line.split("=", 1)[0] for line in lines if line and not line.startswith("#")}
    expected = {f"SLATE_{name.upper()}" for name in Settings.model_fields}
    assert expected <= keys, expected - keys
    assert "SLATE_SECRET_KEY" in keys
    # Each setting has a comment line directly above it.
    for i, line in enumerate(lines):
        if line and not line.startswith("#"):
            assert lines[i - 1].startswith("#"), line


def test_compose_config_validates():
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker not available")
    result = subprocess.run(
        [docker, "compose", "-p", "slate", "-f", str(PACK / "compose.yml")]
        + ["config", "--no-env-resolution", "--format", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "unknown" in result.stderr.lower():
        pytest.skip(f"docker compose too old: {result.stderr.strip()}")
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["name"] == "slate"
    svc = config["services"]["slate"]
    assert svc["container_name"] == "slate"
    assert svc["restart"] == "unless-stopped"
    assert svc["init"] is True
    assert svc["stop_grace_period"] in ("20s", 20_000_000_000)
    [port] = svc["ports"]
    assert (port["host_ip"], str(port["published"]), port["target"]) == ("127.0.0.1", "18448", 8000)
    [vol] = svc["volumes"]
    assert (vol["source"], vol["target"]) == ("/opt/slate/data", "/data")
    assert svc["env_file"][0]["path"].replace("\\", "/").endswith("/opt/slate/.env")
    assert svc["logging"] == {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "5"},
    }
    assert "/healthz" in " ".join(svc["healthcheck"]["test"])


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=alex", "-c", "user.email=alex@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _checkout(tmp_path: Path) -> tuple[Path, str, str]:
    """$SLATE_HOME/repo cloned from an origin with two releases: v1 knows migration aaa…, v2
    adds bbb…. The checkout is at v2. Returns (repo, v1 sha, v2 sha)."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    src = tmp_path / "src"
    (src / "alembic" / "versions").mkdir(parents=True)
    shutil.copytree(PACK, src / "deploy" / "server")  # deploy.sh installs units from the checkout
    _git("init", "-q", "-b", "main", cwd=src)
    (src / "alembic" / "versions" / "a.py").write_text('revision = "aaaaaaaaaaaa"\n')
    _git("add", ".", cwd=src)
    _git("commit", "-q", "-m", "one", cwd=src)
    _git("tag", "v1", cwd=src)
    (src / "alembic" / "versions" / "b.py").write_text('revision = "bbbbbbbbbbbb"\n')
    _git("add", ".", cwd=src)
    _git("commit", "-q", "-m", "two", cwd=src)
    _git("tag", "v2", cwd=src)
    repo = tmp_path / "home" / "repo"
    _git("clone", "-q", src.as_posix(), repo.as_posix(), cwd=tmp_path)
    _git("checkout", "-q", "--detach", "v2", cwd=repo)
    return repo, _git("rev-parse", "v1", cwd=repo), _git("rev-parse", "v2", cwd=repo)


def _calls(tmp_path: Path) -> str:
    log = tmp_path / "calls.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_rollback_past_a_migration_stops_before_changing_anything(tmp_path):
    home = _home(tmp_path)
    repo, v1, v2 = _checkout(tmp_path)
    _slate_db(home / "data" / "slate.db", revision="bbbbbbbbbbbb")
    result = _run(tmp_path, "rollback.sh", "v1", FAKE_COMMIT=v1[:7])
    assert result.returncode == 1
    assert "bbbbbbbbbbbb" in result.stderr and "Rollback past a migration" in result.stderr
    assert _git("rev-parse", "HEAD", cwd=repo) == v2  # not checked out
    assert "systemctl" not in _calls(tmp_path)
    assert not (home / "backups").exists()


def test_rollback_backs_up_then_waits_for_the_old_commit(tmp_path):
    home = _home(tmp_path)
    repo, v1, _ = _checkout(tmp_path)
    _slate_db(home / "data" / "slate.db", revision="aaaaaaaaaaaa")
    result = _run(tmp_path, "rollback.sh", "v1", FAKE_COMMIT=v1[:7])
    assert result.returncode == 0, result.stderr
    assert _git("rev-parse", "HEAD", cwd=repo) == v1
    [backup] = (home / "backups").glob("pre-rollback-*.db")
    con = sqlite3.connect(backup)
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "aaaaaaaaaaaa"
    con.close()
    calls = _calls(tmp_path)
    assert "systemctl --user reload slate.service" in calls
    assert calls.index("docker exec") < calls.index("systemctl --user reload")


def test_rollback_is_not_done_while_another_commit_answers(tmp_path):
    home = _home(tmp_path)
    _, v1, v2 = _checkout(tmp_path)
    _slate_db(home / "data" / "slate.db", revision="aaaaaaaaaaaa")
    result = _run(tmp_path, "rollback.sh", "v1", FAKE_COMMIT=v2[:7], SLATE_WAIT_SECONDS="2")
    assert result.returncode == 1
    assert f"serves {v2[:7]}, not {v1[:12]}" in result.stderr


def test_deploy_is_not_live_until_the_new_commit_answers(tmp_path):
    home = _home(tmp_path)
    repo, v1, v2 = _checkout(tmp_path)
    _git("checkout", "-q", "--detach", "v1", cwd=repo)
    (home / ".env").write_text("SLATE_SECRET_KEY=x\n")
    _slate_db(home / "data" / "slate.db")
    env = {"HOME": (tmp_path / "user").as_posix(), "SLATE_WAIT_SECONDS": "2"}
    stale = _run(tmp_path, "deploy.sh", "v2", FAKE_COMMIT=v1[:7], **env)
    assert stale.returncode == 1 and f"serves {v1[:7]}, not {v2[:12]}" in stale.stderr
    live = _run(tmp_path, "deploy.sh", "v2", FAKE_COMMIT=v2[:7], **env)
    assert live.returncode == 0, live.stderr
    assert "is live" in live.stdout
    assert list((home / "backups").glob("pre-deploy-*.db"))  # taken before the restart
