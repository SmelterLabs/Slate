"""Work hints for the timer and the button: item.due and item.run_requested wake an agent's
runner on its stream; due_soon and overdue stay news; another agent's task wakes nobody else."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from slate.service import due
from test_agent_stream import _agent, _item, _setup
from test_api_events import _bearer, _open, live  # noqa: F401 (live is a fixture)

API = "/api/v1"


class Reader:
    """Reads the stream in the background (a timed-out read would close the SSE iterator)."""

    def __init__(self, stream) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.task = asyncio.create_task(self._run(stream))

    async def _run(self, stream) -> None:
        while True:
            msg = await stream.event(timeout=3600)
            await self.queue.put(msg)

    async def drain(
        self, quiet: float = 0.5, expect: int = 0, patience: float = 10.0, until=None
    ) -> list:
        """Every event message until the stream is quiet for `quiet` seconds.

        With `expect`, first wait up to `patience` seconds for that many messages: a busy
        server (CI at full load) can take longer than the quiet window to deliver the first
        one, and a drain that gives up early reads as "nothing arrived". With `until` (a test
        on the messages so far), wait the same way until it holds: one event can bring more
        than one message, so a count can be met before a later event's messages arrive.
        """
        got = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + patience
        while len(got) < expect or (until is not None and not until(got)):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                got.append(await asyncio.wait_for(self.queue.get(), remaining))
            except TimeoutError:
                break
        while True:
            try:
                got.append(await asyncio.wait_for(self.queue.get(), quiet))
            except TimeoutError:
                return got


async def _reader(client, stack, tok: str) -> Reader:
    stream = await _open(client, stack, f"{API}/checkin/stream", headers=_bearer(tok))
    await stream.ready()
    reader = Reader(stream)
    stack.callback(reader.task.cancel)
    return reader


def _hints(msgs) -> list[tuple[str, str]]:
    return [(m.data["item_id"], m.data["type"]) for m in msgs if m.event == "work"]


async def test_due_and_run_requested_are_work_hints(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        stream = await _reader(client, stack, tok)

        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        late = await _item(
            client, live.admin_token, todo["id"], "Late", assignees=["agent"], due_at=past
        )
        assert [i for i, _ in _hints(await stream.drain(expect=1))] == [late["id"]]

        # The scheduler reaches its due time: the run stage and item.due both hint.
        with live.Session() as s:
            due.emit_due_events(s, now=datetime.now(UTC))
        # Each of the two events brings news and a hint: wait for both hints, not two messages.
        msgs = await stream.drain(until=lambda got: len(_hints(got)) >= 2)
        hints = _hints(msgs)
        assert sorted(t for _, t in hints) == ["item.due", "item.run_requested"]
        assert {i for i, _ in hints} == {late["id"]}

        # An hour later: overdue is news, not a hint.
        with live.Session() as s:
            due.emit_due_events(s, now=datetime.now(UTC) + timedelta(hours=1))
        msgs = await stream.drain(expect=1)
        assert [m.data["type"] for m in msgs] == ["item.overdue"]
        assert _hints(msgs) == []


async def test_due_soon_is_news_and_run_now_hints_the_pressed_task(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        stream = await _reader(client, stack, tok)

        soon = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        later = await _item(
            client, live.admin_token, todo["id"], "Later", assignees=["agent"], due_at=soon
        )
        msgs = await stream.drain(expect=1)
        assert [(m.event, m.data.get("why")) for m in msgs] == [("news", "assigned")]

        with live.Session() as s:
            due.emit_due_events(s, now=datetime.now(UTC))
        msgs = await stream.drain(expect=1)
        assert [m.data["type"] for m in msgs] == ["item.due_soon"]
        assert _hints(msgs) == []

        r = await client.post(f"{API}/items/{later['id']}/run", headers=_bearer(live.admin_token))
        assert r.status_code == 202, r.text
        assert _hints(await stream.drain(expect=1)) == [(later["id"], "item.run_requested")]


async def test_another_agents_run_wakes_nobody_else(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        await _agent(client, live.admin_token, "agent2")
        stream = await _reader(client, stack, tok)

        theirs = await _item(client, live.admin_token, todo["id"], "B's", assignees=["agent2"])
        r = await client.post(f"{API}/items/{theirs['id']}/run", headers=_bearer(live.admin_token))
        assert r.status_code == 202, r.text
        assert await stream.drain() == []


async def test_runner_on_the_live_stream_runs_the_pressed_task(live, tmp_path):  # noqa: F811
    """The whole path: Run now -> stream hint -> slate_runner takes that task -> the command's
    result finishes it. The runner works on a real socket (it holds the stream)."""
    import sys
    import threading
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients" / "python"))
    from slate_runner import Runner

    example = Path(__file__).resolve().parent.parent / "clients" / "python" / "run_one_example.py"
    async with httpx.AsyncClient(base_url=live.url) as client:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        later = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        item = await _item(
            client,
            live.admin_token,
            todo["id"],
            "Weekly summary",
            assignees=["agent"],
            due_at=later,
        )
        runner = Runner(
            live.url,
            tok,
            f'"{sys.executable}" "{example}"',
            lease=60,
            state_path=tmp_path / "state.json",
            log=lambda msg: None,
        )
        thread = threading.Thread(target=runner.forever, daemon=True)
        thread.start()
        try:
            await asyncio.sleep(0.5)  # the start-up sweep finds nothing: the task is not due
            r = await client.get(f"{API}/items/{item['id']}", headers=_bearer(live.admin_token))
            assert r.json()["agent"] is None

            r = await client.post(
                f"{API}/items/{item['id']}/run", headers=_bearer(live.admin_token)
            )
            assert r.status_code == 202
            for _ in range(100):
                view = (
                    await client.get(f"{API}/items/{item['id']}", headers=_bearer(live.admin_token))
                ).json()
                if view["completed_at"]:
                    break
                await asyncio.sleep(0.1)
            assert view["completed_at"], view
            assert (tmp_path / "state.json").exists()
        finally:
            runner.stopping.set()
            # Wake the stream so the runner thread sees the stop and ends before the server.
            await client.post(
                f"{API}/items/{item['id']}/comments",
                json={"body_md": "bye"},
                headers=_bearer(live.admin_token),
            )
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive()


@pytest.mark.parametrize("parallel, already_due", [(1, False), (4, False), (4, True)])
async def test_four_due_tasks_drain_automatically_after_a_busy_agent(
    live,  # noqa: F811
    tmp_path,
    parallel,
    already_due,
):
    """Four scheduled tasks use all slots and drain after finish or block without another
    Run now click. Nothing starts early; wakes and startup both cover a due backlog."""
    import json
    import sys
    import threading
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients" / "python"))
    from slate_runner import Runner

    gate = tmp_path / "release"
    runs = tmp_path / "runs.jsonl"
    script = tmp_path / "queued.py"
    script.write_text(
        "import json, pathlib, sys, time\n"
        "task = json.load(sys.stdin)\n"
        "item = task['item']\n"
        "with open(sys.argv[2], 'a') as f: f.write(json.dumps(item['id']) + '\\n')\n"
        "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(0.05)\n"
        "out = {'outcome': 'done', 'reply': 'Verified by isolated queue test'}\n"
        "if item['title'] == 'Queue 0':\n"
        "    out.update(outcome='blocked', waiting_on='alex', reply='Test needs a person')\n"
        "print(json.dumps(out))\n"
    )
    async with httpx.AsyncClient(base_url=live.url) as client:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        when = datetime.now(UTC) + timedelta(seconds=2)
        items = [
            await _item(
                client,
                live.admin_token,
                todo["id"],
                f"Queue {n}",
                assignees=["agent"],
                due_at=when.isoformat(),
            )
            for n in range(4)
        ]
        if already_due:
            await asyncio.sleep(max(0, (when - datetime.now(UTC)).total_seconds()) + 0.05)
            with live.Session() as s:
                due.emit_due_events(s, now=datetime.now(UTC))
        runner = Runner(
            live.url,
            tok,
            f'"{sys.executable}" "{script}" "{gate}" "{runs}"',
            lease=60,
            parallel=parallel,
            state_path=tmp_path / "state.json",
            log=lambda msg: None,
        )
        thread = threading.Thread(target=runner.forever, daemon=True)
        thread.start()
        try:
            if not already_due:
                await asyncio.sleep(0.3)
                assert not runs.exists(), "future tasks must not start early"
                await asyncio.sleep(max(0, (when - datetime.now(UTC)).total_seconds()) + 0.05)
                with live.Session() as s:
                    due.emit_due_events(s, now=datetime.now(UTC))
            for _ in range(100):
                if runs.exists() and len(runs.read_text().splitlines()) >= parallel:
                    break
                await asyncio.sleep(0.1)
            assert len(runner.running) == parallel
            assert len(runs.read_text().splitlines()) == parallel
            gate.touch()
            for _ in range(150):
                views = [
                    (
                        await client.get(
                            f"{API}/items/{item['id']}",
                            headers=_bearer(live.admin_token),
                        )
                    ).json()
                    for item in items
                ]
                settled = sum(
                    bool(v.get("completed_at")) or (v.get("agent") or {}).get("state") == "blocked"
                    for v in views
                )
                if settled == 4:
                    break
                await asyncio.sleep(0.1)
            assert settled == 4, views
            assert views[0]["agent"]["state"] == "blocked"
            assert all(v["completed_at"] for v in views[1:])
            seen = [json.loads(line) for line in runs.read_text().splitlines()]
            assert len(seen) == 4 and set(seen) == {item["id"] for item in items}
        finally:
            gate.touch()
            runner.stopping.set()
            await client.post(
                f"{API}/items/{items[0]['id']}/comments",
                json={"body_md": "test cleanup"},
                headers=_bearer(live.admin_token),
            )
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive()


async def test_an_ask_wakes_the_asked_agent(live):  # noqa: F811
    async with httpx.AsyncClient(base_url=live.url) as client, AsyncExitStack() as stack:
        todo = await _setup(client, live.admin_token)
        _, tok = await _agent(client, live.admin_token, "agent")
        stream = await _reader(client, stack, tok)

        item = await _item(client, live.admin_token, todo["id"], "Not Agent's task")
        assert await stream.drain() == []
        r = await client.post(
            f"{API}/items/{item['id']}/comments",
            json={"body_md": "Can you check this?", "assignee": "agent"},
            headers=_bearer(live.admin_token),
        )
        assert r.status_code == 201, r.text
        msgs = await stream.drain(expect=2)
        assert [m.data.get("why") for m in msgs if m.event == "news"] == ["asked"]
        assert _hints(msgs) == [(item["id"], "comment.assigned")]


async def test_one_runner_process_serves_every_agent_in_its_config(live, tmp_path):  # noqa: F811
    """One runner, many agents: a config with two agents serves both streams in one process,
    and each agent's command runs with that agent's own environment."""
    import sys
    import threading
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients" / "python"))
    from slate_runner import load_config, serve

    script = tmp_path / "who.py"
    script.write_text(
        "import json, os, sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'outcome': 'done', 'reply': 'ran as ' + os.environ['WHO']}))\n"
    )
    async with httpx.AsyncClient(base_url=live.url) as client:
        todo = await _setup(client, live.admin_token)
        _, agent = await _agent(client, live.admin_token, "agent")
        _, agent2 = await _agent(client, live.admin_token, "agent2")
        (tmp_path / "agent2.token").write_text(agent2 + "\n")
        (tmp_path / "secrets.env").write_text(f"# test\nAGENT_TOKEN={agent}\nWHO_AGENT=Agent\n")
        exe = sys.executable.replace("\\", "/")
        (tmp_path / "runner.toml").write_text(
            f'url = "{live.url}"\n'
            'env_file = "secrets.env"\n'
            f'exec = "\\"{exe}\\" who.py"\n'
            "lease = 60\n"
            '[[agent]]\nname = "agent"\ntoken_env = "AGENT_TOKEN"\n'
            'env = { WHO = "${WHO_AGENT}" }\n'
            '[[agent]]\nname = "agent2"\ntoken_file = "agent2.token"\n'
            'env = { WHO = "Agent2" }\n'
        )
        agents = load_config(tmp_path / "runner.toml")
        later = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        items = {
            who: await _item(
                client, live.admin_token, todo["id"], f"For {who}", assignees=[who], due_at=later
            )
            for who in ("agent", "agent2")
        }
        stop = threading.Event()
        thread = threading.Thread(target=serve, args=(agents, stop), daemon=True)
        thread.start()
        try:
            await asyncio.sleep(0.5)
            for item in items.values():
                r = await client.post(
                    f"{API}/items/{item['id']}/run", headers=_bearer(live.admin_token)
                )
                assert r.status_code == 202
            for who, item in items.items():
                for _ in range(100):
                    view = (
                        await client.get(
                            f"{API}/items/{item['id']}", headers=_bearer(live.admin_token)
                        )
                    ).json()
                    if view["completed_at"]:
                        break
                    await asyncio.sleep(0.1)
                assert view["completed_at"], (who, view)
                comments = (
                    await client.get(
                        f"{API}/items/{item['id']}/comments", headers=_bearer(live.admin_token)
                    )
                ).json()
                bodies = [c["body_md"] for c in comments]
                assert f"ran as {who.capitalize()}" in bodies, bodies
            assert (tmp_path / "agent-state.json").exists()
            assert (tmp_path / "agent2-state.json").exists()
        finally:
            stop.set()
            await asyncio.to_thread(thread.join, 40)
            assert not thread.is_alive()
