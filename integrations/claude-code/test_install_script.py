"""install.ps1 (the skill installer) never stages files where another user could swap them,
never touches the network or the disk under -WhatIf, and always removes its stage folder."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent / "skills"
SCRIPT = (HERE / "install.ps1").read_text(encoding="utf-8")
REMOTE = (HERE / "install-remote.sh").read_text(encoding="utf-8")


def _code(text: str) -> str:
    """The script without its comments."""
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_remote_files_go_into_a_private_folder_made_on_the_host():
    code = _code(SCRIPT)
    assert "/tmp/" not in code, "a fixed /tmp name can be pre-created or swapped by another user"
    assert re.search(r"ssh\s+\$h\s+['\"]?mktemp -d", code)


def test_the_stage_folder_is_removed_even_when_a_step_fails():
    code = _code(SCRIPT)
    assert re.search(r"\}\s*finally\s*\{[^}]*Delete\(\$stage", code, re.S)


def test_whatif_does_not_fetch():
    code = _code(SCRIPT)
    fetch = next(line for line in code.splitlines() if "git -C $repo fetch" in line)
    assert "ShouldProcess" in fetch


def test_old_copies_are_kept_outside_the_temp_folder():
    code = _code(SCRIPT)
    backup = next(line for line in code.splitlines() if "slate-skill-backup-" in line)
    assert "GetTempPath" not in backup and "$HOME" in backup


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is not installed")
def test_install_ps1_parses():
    cmd = (
        "$e = $null; [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{HERE / 'install.ps1'}', [ref]$null, [ref]$e) | Out-Null; "
        "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }"
    )
    out = subprocess.run(["pwsh", "-NoProfile", "-Command", cmd], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


# The bash on PATH; Windows' own System32 bash is WSL's launcher, not a shell.
BASH = shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
def test_install_remote_sh_parses():
    out = subprocess.run([BASH, "-n"], input=REMOTE, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "set -euo pipefail" in REMOTE
