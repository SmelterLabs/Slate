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
