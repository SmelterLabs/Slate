"""Exercise the built server, not an editable checkout or the caller's working directory."""

import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd, env=None):
    return subprocess.run(
        args, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=180
    )


def checked(*args, cwd, env=None):
    result = run(*args, cwd=cwd, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture(scope="module")
def installed_server(tmp_path_factory):
    outside = tmp_path_factory.mktemp("installed-server")
    dist = outside / "dist"
    checked("uv", "build", "--out-dir", str(dist), cwd=ROOT)
    wheel = next(dist.glob("*.whl"))
    venv = outside / "venv"
    checked("uv", "venv", "--python", sys.executable, str(venv), cwd=outside)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # The wheel's dependencies resolve to the versions uv.lock pins, never whatever the index
    # has today: the floors in pyproject.toml are only lower bounds.
    constraints = outside / "constraints.txt"
    checked(
        "uv",
        "export",
        "--frozen",
        "--no-dev",
        "--no-emit-project",
        "--no-hashes",
        "--output-file",
        str(constraints),
        cwd=ROOT,
    )
    checked(
        "uv",
        "pip",
        "install",
        "--python",
        str(python),
        "-c",
        str(constraints),
        str(wheel),
        cwd=outside,
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("SLATE_")}
    env.pop("PYTHONPATH", None)
    env.update(
        SLATE_DATABASE_PATH=str(outside / "data" / "slate.db"),
        SLATE_SCHEDULER_ENABLED="false",
        SLATE_AUTOMATIONS_ENABLED="false",
        SLATE_PUSH_ENABLED="false",
        SLATE_SECRET_KEY="ab" * 32,
    )
    return outside, wheel, python, env


def _pins(text: str) -> dict[str, str]:
    pins = {}
    for line in text.splitlines():
        line = line.split(";")[0].split("#")[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[name.strip().lower().replace("_", "-")] = version.strip()
    return pins


def test_dependency_floors_are_tested_and_capped():
    """Every runtime dependency names a floor no newer than the version uv.lock pins (the one
    the tests run) and an upper bound, except tzdata, whose new data is always wanted."""
    import tomllib

    from packaging.requirements import Requirement
    from packaging.version import Version

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {p["name"]: Version(p["version"]) for p in lock["package"] if "version" in p}
    for line in project["project"]["dependencies"]:
        req = Requirement(line)
        floors = [Version(s.version) for s in req.specifier if s.operator == ">="]
        caps = [s for s in req.specifier if s.operator == "<"]
        assert floors, f"{line}: no floor"
        assert floors[0] <= locked[req.name.lower()], f"{line}: floor newer than uv.lock"
        assert caps or req.name == "tzdata", f"{line}: no upper bound"


def test_installed_wheel_has_the_locked_versions(installed_server):
    outside, _, python, _ = installed_server
    locked = _pins((outside / "constraints.txt").read_text(encoding="utf-8"))
    installed = _pins(checked("uv", "pip", "freeze", "--python", str(python), cwd=outside))
    installed.pop("slate", None)
    assert installed, "nothing installed"
    assert {name: locked.get(name) for name in installed} == installed


def test_installed_server_migrates_outside_checkout(installed_server):
    outside, wheel, python, env = installed_server
    location = checked(
        str(python), "-c", "import slate; print(slate.__file__)", cwd=outside, env=env
    ).strip()
    assert str(outside) in location
    assert str(ROOT) not in location
    for _ in range(2):  # A second startup must preserve the same database.
        checked(str(python), "-m", "slate.cli", "migrate", cwd=outside, env=env)
    from alembic.script import ScriptDirectory

    expected_head = ScriptDirectory(str(ROOT / "alembic")).get_current_head()
    with sqlite3.connect(env["SLATE_DATABASE_PATH"]) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            expected_head,
        )
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT count(*) FROM accounts").fetchone() == (0,)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "slate/migrations/env.py" in names
        expected = len(list((ROOT / "alembic" / "versions").glob("*.py")))
        assert (
            len(
                [
                    n
                    for n in names
                    if n.startswith("slate/migrations/versions/") and n.endswith(".py")
                ]
            )
            == expected
        )
        # The wheel is API/MCP only; Docker builds and supplies the actual board.
        assert "slate/static/index.html" not in names
    output = checked(
        str(python),
        "-c",
        "from fastapi.testclient import TestClient; from slate.app import app; "
        "c=TestClient(app); "
        "r=c.post('/api/v1/bootstrap',json={'handle':'owner','display_name':'Owner'}); "
        "assert r.status_code==201,r.text; "
        "token=r.json()['token']; "
        "assert c.get('/api/v1/me',headers={'Authorization':'Bearer '+token}).status_code==200; "
        "assert c.get('/').status_code==404; "
        "print('bootstrap=201 me=200 board=404 (API-only wheel)')",
        cwd=outside,
        env=env,
    )
    assert "bootstrap=201" in output


def test_server_sdist_is_minimal(installed_server):
    import tarfile

    outside, _, _, _ = installed_server
    with tarfile.open(next((outside / "dist").glob("*.tar.gz"))) as archive:
        members = [Path(member.name).parts[1:] for member in archive.getmembers()]
    allowed = {
        ".gitignore",  # Hatch includes VCS ignore rules in sdists automatically.
        "src",
        "alembic",
        "alembic.ini",
        "README.md",
        "LICENSE",  # Hatch includes the license file in sdists automatically.
        "pyproject.toml",
        "uv.lock",
        "PKG-INFO",
    }
    assert all(parts[0] in allowed for parts in members if parts)
    assert not any("static" in parts or "__pycache__" in parts for parts in members)


def test_archive_build_metadata_and_git_identity(tmp_path, request):
    import shutil

    script = ROOT / "scripts" / "build_metadata.py"
    assert script.is_file(), "archive-compatible metadata writer is missing"
    archive = tmp_path / "archive"
    (archive / "src" / "slate").mkdir(parents=True)
    shutil.copyfile(ROOT / "src/slate/build.py", archive / "src/slate/build.py")
    (archive / "src/slate/build.json").write_text('{"rev": "999", "commit": "stale"}')
    output = tmp_path / "build.json"
    checked(
        sys.executable, str(script), "--source", str(archive), "--output", str(output), cwd=tmp_path
    )
    assert json.loads(output.read_text()) == {"rev": "dev", "commit": None}
    checked(
        sys.executable,
        str(script),
        "--source",
        str(archive),
        "--output",
        str(output),
        "--rev",
        "42",
        "--commit",
        "abc1234",
        cwd=tmp_path,
    )
    assert json.loads(output.read_text()) == {"rev": "42", "commit": "abc1234"}
    request.getfixturevalue("git_checkout")  # the rest reads this checkout's own identity
    checked(
        sys.executable, str(script), "--source", str(ROOT), "--output", str(output), cwd=tmp_path
    )
    expected_commit = checked("git", "rev-parse", "--short", "HEAD", cwd=ROOT).strip()
    expected_rev = checked("git", "rev-list", "--count", "HEAD", cwd=ROOT).strip()
    if checked("git", "rev-parse", "--is-shallow-repository", cwd=ROOT).strip() == "true":
        expected_rev = "dev"
    assert json.loads(output.read_text()) == {"rev": expected_rev, "commit": expected_commit}


def test_portable_compose_and_archive_docker_contract():
    import yaml

    compose_path = ROOT / "compose.yaml"
    assert compose_path.is_file(), "neutral Compose setup is missing"
    compose = yaml.safe_load(compose_path.read_text())
    assert compose["name"] != "slate"  # no collision with an existing `slate` project
    service = compose["services"]["slate"]
    assert service["ports"] == ["127.0.0.1:${SLATE_PORT:-8000}:8000"]
    assert service["volumes"] == ["slate-data:/data"]
    assert "slate-data" in compose["volumes"]
    assert service["environment"]["SLATE_TIMEZONE"] == "${SLATE_TIMEZONE:-UTC}"
    assert "SLATE_SECRET_KEY:?" in service["environment"]["SLATE_SECRET_KEY"]
    assert service["environment"]["SLATE_WEBHOOK_PRIVATE_TARGETS"] == "false"
    docker = (ROOT / "Dockerfile").read_text()
    assert "COPY .git" not in docker
    assert "scripts/build_metadata.py" in docker
    assert "COPY --from=web /web/dist" in docker
    assert "else mkdir -p dist" not in docker  # missing board must fail the build
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in ignored and ".env.*" in ignored and ".worktrees" in ignored
