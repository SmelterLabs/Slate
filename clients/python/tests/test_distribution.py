"""Opt-in runner distribution checks; no providers or real credentials are used."""

import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / "clients/python"


def checked(*args, cwd, env=None):
    result = subprocess.run(
        args, cwd=cwd, env=env, text=True, capture_output=True, timeout=180, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_runner_wheel_and_sdist_are_self_contained(tmp_path):
    dist = tmp_path / "dist"
    checked("uv", "build", "--out-dir", str(dist), cwd=CLIENT)
    wheel = next(dist.glob("*.whl"))
    canonical = {
        "hermes/run_task.py": ROOT / "integrations/hermes/run_task.py",
        "hermes/run_task_cli.py": ROOT / "integrations/hermes/run_task_cli.py",
        "runner-example/run_task.py": ROOT / "integrations/runner-example/run_task.py",
        "run_one_example.py": CLIENT / "run_one_example.py",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        for resource, source in canonical.items():
            member = "slate_runner_assets/" + resource
            assert member in names, f"missing installed resource: {member}"
            assert archive.read(member) == source.read_bytes()
        assert "slate_runner_assets/runner.example.toml" in names
    extracted = tmp_path / "source"
    with tarfile.open(next(dist.glob("*.tar.gz"))) as archive:
        archive.extractall(extracted, filter="data")
    source = next(extracted.iterdir())
    assert not (source / ".git").exists()
    rebuilt = tmp_path / "rebuilt"
    checked("uv", "build", "--wheel", "--out-dir", str(rebuilt), cwd=source)
    with zipfile.ZipFile(next(rebuilt.glob("*.whl"))) as archive:
        for resource, original in canonical.items():
            assert archive.read("slate_runner_assets/" + resource) == original.read_bytes()
    venv = tmp_path / "venv"
    checked("uv", "venv", "--python", sys.executable, str(venv), cwd=tmp_path)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    checked("uv", "pip", "install", "--python", str(python), str(wheel), cwd=tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SLATE_")}
    env.pop("PYTHONPATH", None)
    checked(str(python), "-m", "slate_runner", "--help", cwd=tmp_path, env=env)
    output = tmp_path / "starter"
    checked(
        str(python), "-m", "slate_runner_assets", "--output", str(output), cwd=tmp_path, env=env
    )
    for resource, original in canonical.items():
        assert (output / resource).read_bytes() == original.read_bytes()
    for adapter in ["hermes/run_task.py", "hermes/run_task_cli.py", "runner-example/run_task.py"]:
        checked(
            str(python),
            "-c",
            "import runpy; runpy.run_path(" + repr(str(output / adapter)) + ")",
            cwd=tmp_path,
            env=env,
        )
    # Export must never overwrite a user's edited config or state.
    config = output / "runner.example.toml"
    config.write_text("keep my config")
    result = subprocess.run(
        [str(python), "-m", "slate_runner_assets", "--output", str(output)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert config.read_text() == "keep my config"
    env["SLATE_ITEM_URL"] = "http://127.0.0.1:8000/item/smoke"
    sample = subprocess.run(
        [str(python), str(output / "run_one_example.py")],
        cwd=tmp_path,
        env=env,
        input=json.dumps({"item": {"title": "Package smoke test"}}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert sample.returncode == 0, sample.stderr
    assert json.loads(sample.stdout.splitlines()[-1])["outcome"] == "done"
