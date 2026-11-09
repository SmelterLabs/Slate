"""Static checks on the server deploy pack (deploy/server). Nothing here needs a server."""

from __future__ import annotations

import configparser
import json
import re
import shutil
import subprocess
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
    assert ".backup(dst)" in text  # sqlite3 online backup API, never cp of a WAL database
    assert "cp " not in text
    assert "date +%F" in text  # dated on the host, passed in as argv
    assert "-mtime +30" in text and "/opt/slate/data/backups" in text


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
