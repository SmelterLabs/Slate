"""Approval persistence races, using only temporary files and isolated adapter imports."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest


def adapter():
    spec = importlib.util.spec_from_file_location(
        "approval_state_adapter", Path(__file__).with_name("run_task.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("clear_a,clear_b", [(False, False), (True, False), (True, True)])
def test_two_adapters_with_stale_snapshots_preserve_both_changes(
    tmp_path, monkeypatch, clear_a, clear_b
):
    path = tmp_path / "approvals.json"
    env = {"HERMES_STATE": str(path)}
    old_a = {"id": "old-a", "command": "command a"}
    old_b = {"id": "old-b", "command": "command b"}
    path.write_text(json.dumps({"a": old_a, "b": old_b}))
    a, b = adapter(), adapter()
    new_a = None if clear_a else {"id": "new-a", "command": "new command a"}
    new_b = None if clear_b else {"id": "new-b", "command": "new command b"}
    both_read = Barrier(2)
    read_text = Path.read_text

    def stale_read(self, *args, **kwargs):
        text = read_text(self, *args, **kwargs)
        if self == path:
            # Both whole-map writers read the same snapshot before either saves.
            both_read.wait(timeout=5)
        return text

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", stale_read)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(a.save_pending, env, "a", new_a)
            second = pool.submit(b.save_pending, env, "b", new_b)
            first.result(timeout=10)
            second.result(timeout=10)

    restarted = adapter()
    assert restarted.load_pending(env, "a") == new_a
    assert restarted.load_pending(env, "b") == new_b


def test_legacy_pending_survives_and_clears_do_not_resurrect_it(tmp_path):
    module = adapter()
    path = tmp_path / "approvals.json"
    env = {"HERMES_STATE": str(path)}
    legacy = {"a": {"id": "legacy-a"}, "b": {"id": "legacy-b"}}
    original = json.dumps(legacy)
    path.write_text(original)
    assert module.load_pending(env, "a") == legacy["a"]
    assert module.load_pending(env, "b") == legacy["b"]
    assert module.load_pending(env, "missing") is None
    module.save_pending(env, "a", None)
    module.save_pending(env, "c", {"id": "new-c"})
    restarted = adapter()
    assert restarted.load_pending(env, "a") is None
    assert restarted.load_pending(env, "b") == legacy["b"]
    assert restarted.load_pending(env, "c") == {"id": "new-c"}
    assert path.read_text() == original  # no risky whole-map migration
    module.save_pending(env, "a", {"id": "replacement-a"})
    assert restarted.load_pending(env, "a") == {"id": "replacement-a"}
    module.save_pending(env, "a", None)
    assert restarted.load_pending(env, "a") is None


@pytest.mark.parametrize("legacy_text", [None, "not json", "[]", "null"])
def test_new_state_does_not_require_a_valid_legacy_file(tmp_path, legacy_text):
    module = adapter()
    path = tmp_path / "approvals.json"
    env = {"HERMES_STATE": str(path)}
    if legacy_text is not None:
        path.write_text(legacy_text)
        with pytest.raises(module.Stopped, match="state"):
            module.load_pending(env, "a")
    else:
        assert module.load_pending(env, "a") is None
    module.save_pending(env, "a", {"id": "new-a"})
    assert module.load_pending(env, "a") == {"id": "new-a"}
    module.save_pending(env, "a", None)
    assert module.load_pending(env, "a") is None


def test_atomic_publish_and_failed_replace_keep_previous_approval(tmp_path, monkeypatch):
    module = adapter()
    env = {"HERMES_STATE": str(tmp_path / "approvals.json")}
    old = {"id": "old"}
    module.save_pending(env, "a", old)
    replace = Path.replace
    staged = []

    def inspect_replace(self, target):
        staged.append(self)
        assert module.load_pending(env, "a") == old
        assert json.loads(self.read_text(encoding="utf-8")) == {"id": "new"}
        return replace(self, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", inspect_replace)
        module.save_pending(env, "a", {"id": "new"})
    assert module.load_pending(env, "a") == {"id": "new"}
    assert all(not path.exists() for path in staged)

    def failed_replace(self, target):
        staged.append(self)
        raise OSError("test write failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", failed_replace)
        with pytest.raises(OSError, match="write failure"):
            module.save_pending(env, "a", None)
    assert module.load_pending(env, "a") == {"id": "new"}
    assert all(not path.exists() for path in staged)


def test_item_ids_remain_case_sensitive_and_cannot_escape_state_directory(tmp_path):
    module = adapter()
    env = {"HERMES_STATE": str(tmp_path / "approvals.json")}
    ids = ["A", "a", "../escape", "folder/card"]
    for item_id in ids:
        module.save_pending(env, item_id, {"id": item_id})
    for item_id in ids:
        assert module.load_pending(env, item_id) == {"id": item_id}
    assert not (tmp_path / "escape").exists()


_PROCESS_WRITER = """
import runpy
import sys

adapter = runpy.run_path(sys.argv[1])
env = {"HERMES_STATE": sys.argv[2]}
item_id = sys.argv[3]
sys.stdin.readline()
for iteration in range(30):
    pending = {"id": item_id, "iteration": iteration}
    adapter["save_pending"](env, item_id, pending)
    assert adapter["load_pending"](env, item_id) == pending
    adapter["save_pending"](env, item_id, None)
    assert adapter["load_pending"](env, item_id) is None
adapter["save_pending"](env, item_id, None if item_id in ("a", "c") else {"id": item_id})
"""


def test_four_processes_can_write_and_clear_independent_approvals(tmp_path):
    path = tmp_path / "approvals.json"
    env = {"HERMES_STATE": str(path)}
    legacy = {key: {"id": f"legacy-{key}"} for key in ("a", "b", "c", "d", "untouched")}
    path.write_text(json.dumps(legacy))
    children = []
    try:
        for key in ("a", "b", "c", "d"):
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        _PROCESS_WRITER,
                        str(Path(__file__).with_name("run_task.py")),
                        str(path),
                        key,
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        for child in children:
            child.stdin.write("start\n")
            child.stdin.flush()
        for child in children:
            stdout, stderr = child.communicate(timeout=30)
            assert child.returncode == 0, (stdout, stderr)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate()
    module = adapter()
    assert module.load_pending(env, "a") is None
    assert module.load_pending(env, "b") == {"id": "b"}
    assert module.load_pending(env, "c") is None
    assert module.load_pending(env, "d") == {"id": "d"}
    assert module.load_pending(env, "untouched") == legacy["untouched"]
    assert json.loads(path.read_text()) == legacy
