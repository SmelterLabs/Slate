"""The ready-made Python client (clients/python/slate_client.py) against the real app."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from conftest import API

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "clients" / "python"))

from slate_client import Conflict, NotFound, RateLimited, Slate  # noqa: E402

from slate.ratelimit import limiter  # noqa: E402
from slate.settings import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    limiter.reset()
    yield
    limiter.reset()


def _token(headers: dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


@pytest.fixture
def agent(client, make_account):
    _, h = make_account("agent")
    return Slate("http://testserver", _token(h), session_ref="run-1", client=client)


def _assign(client, alex, todo, title="Renew passport", who=("agent",)):
    r = client.post(
        f"{API}/items",
        json={"list_id": todo["id"], "title": title, "assignees": list(who)},
        headers=alex,
    )
    return r.json()


def test_me_and_checkin_cursor(client, alex, todo, agent):
    assert agent.me()["account"]["handle"] == "agent"
    item = _assign(client, alex, todo)
    first = agent.checkin()
    assert [i["id"] for i in first.queue] == [item["id"]]
    assert [n["why"] for n in first.news] == ["assigned"]
    assert agent.since == first.next_since
    again = agent.checkin()
    assert again.news == [] and [i["id"] for i in again.queue] == [item["id"]]


def test_unchanged_reads_come_back_as_304_from_the_cache(client, alex, todo, agent):
    _assign(client, alex, todo)
    agent.checkin()  # cold start
    agent.checkin()  # from the cursor
    agent.checkin()  # same cursor, nothing changed
    assert agent.last_response.status_code == 304
    assert agent.checkin().queue  # served from the cache


def test_the_whole_loop_claim_progress_comment_done(client, alex, todo, agent):
    item = _assign(client, alex, todo)
    claimed = agent.claim(item["id"], note="Picking this up")
    assert claimed["agent"]["state"] == "working"
    agent.progress(item["id"], "Halfway", 50)
    agent.comment(item["id"], "Result: https://example.com/pr/1")
    finished = agent.done(item["id"], note="Shipped")
    assert finished["status"]["kind"] == "done" and finished.get("agent") is None
    assert agent.checkin().queue == []
    events = client.get(f"{API}/events?item_id={item['id']}", headers=alex).json()["events"]
    assert {e["session_ref"] for e in events if e["type"] == "item.reported"} == {"run-1"}


def test_errors_map_to_exceptions(client, alex, todo, agent, make_account):
    item = _assign(client, alex, todo, who=())
    _, hh = make_account("agent2")
    other = Slate("http://testserver", _token(hh), client=client)
    other.claim(item["id"])
    with pytest.raises(Conflict) as exc:
        agent.claim(item["id"])
    assert exc.value.status == 409 and "agent2" in exc.value.detail
    with pytest.raises(NotFound):
        agent.item("Zz0Zz0Zz0Zz0")


def test_batch_with_refs(todo, agent):
    out = agent.batch(
        [
            {"ref": "t", "tool": "item", "action": "create",
             "params": {"list_id": todo["id"], "title": "One"}},
            {"tool": "comment", "action": "add", "params": {"id": "$t.id", "body_md": "hi"}},
        ]
    )  # fmt: skip
    assert out["results"][0]["result"]["title"] == "One"


def test_429_waits_retry_after_then_gives_up(agent, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_reads_per_minute", 1)
    slept: list[float] = []
    monkeypatch.setattr("slate_client.time.sleep", slept.append)
    agent.max_retries = 2
    agent.request("GET", "/me", cache=False)
    with pytest.raises(RateLimited):
        agent.request("GET", "/me", cache=False)
    assert len(slept) == 2 and all(s >= 1 for s in slept)


def test_run_forever_once_handles_reports_and_skips_failures(client, alex, todo, agent):
    good = _assign(client, alex, todo, "Good one")
    bad = _assign(client, alex, todo, "Bad one")
    seen: list[str] = []

    def handle(item):
        seen.append(item["title"])
        if item["id"] == bad["id"]:
            raise RuntimeError("no network")
        return "Done, see https://example.com"

    agent.run_forever(handle, once=True)
    assert sorted(seen) == ["Bad one", "Good one"]
    g = client.get(f"{API}/items/{good['id']}", headers=alex).json()
    b = client.get(f"{API}/items/{bad['id']}", headers=alex).json()
    assert g["status"]["kind"] == "done"
    assert b["agent"]["state"] == "blocked" and "no network" in b["agent"]["note"]


def test_work_loop_next_heartbeat_finish(client, alex, todo, agent):
    first = _assign(client, alex, todo, "First")
    second = _assign(client, alex, todo, "Second")
    task = agent.next()
    assert task["item"]["id"] == first["id"]
    assert (
        agent.heartbeat(first["id"], progress=10, run_id=task["lease"]["run_id"])["item_id"]
        == first["id"]
    )
    assert [r["item_id"] for r in agent.held()] == [first["id"]]
    out = agent.finish(first["id"], "done", "Shipped", run_id=task["lease"]["run_id"])
    assert out["item"]["status"]["kind"] == "done"
    assert out["next"] is None
    assert agent.next()["item"]["id"] == second["id"]


def test_work_forever_does_every_free_task_once(client, alex, todo, agent):
    a = _assign(client, alex, todo, "A")
    b = _assign(client, alex, todo, "B")
    seen = []

    def handle(task):
        seen.append(task["item"]["id"])
        if task["item"]["id"] == b["id"]:
            raise RuntimeError("no network")
        return "ok"

    agent.work_forever(handle, wait=0, once=True)
    assert sorted(seen) == sorted([a["id"], b["id"]])
    done = client.get(f"{API}/items/{a['id']}", headers=alex).json()
    failed = client.get(f"{API}/items/{b['id']}", headers=alex).json()
    assert done["status"]["kind"] == "done"
    assert failed["agent"]["state"] == "blocked" and "no network" in failed["agent"]["note"]


# --- slate_runner.py: the generic runner -----------------------------------------------------

from slate_runner import Runner  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "clients" / "python" / "run_one_example.py"


def _runner(client, h, tmp_path, command=None, **kw):
    command = command or f'"{sys.executable}" "{EXAMPLE}"'
    return Runner(
        "http://testserver",
        _token(h),
        command,
        lease=60,
        state_path=tmp_path / "runner.json",
        client=client,
        log=lambda msg: None,
        **kw,
    )


def _events(client, alex, item_id):
    return client.get(f"{API}/items/{item_id}/events", headers=alex).json()


def test_runner_once_runs_the_example_and_finishes_done(client, alex, todo, make_account, tmp_path):
    _, h = make_account("agent")
    item = _assign(client, alex, todo, title="Order printer ink")
    runner = _runner(client, h, tmp_path)

    assert runner.once() == 0
    view = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert view["completed_at"] is not None or view["agent"] is None
    reports = [
        e["after"] for e in _events(client, alex, item["id"]) if e["type"] == "item.reported"
    ]
    # The progress line reached the card with its url before the result finished the task.
    assert any(
        (r.get("url") or "").endswith(f"?task={item['id']}") and r["note"] == "Looking at it"
        for r in reports
    )
    assert reports[-1]["state"] == "done"
    assert reports[-1]["note"] == "Did: Order printer ink"
    assert runner.once() == 2  # nothing left


def test_runner_a_failing_command_finishes_blocked_waiting_on_a_person(
    client, alex, todo, make_account, tmp_path, monkeypatch
):
    _, h = make_account("agent")
    item = _assign(client, alex, todo, title="Print labels", who=("agent", "alex"))
    monkeypatch.setenv("RUN_ONE_FAIL", "1")
    runner = _runner(client, h, tmp_path)

    assert runner.once() == 1
    view = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert view["agent"]["state"] == "blocked"
    assert "could not reach the printer" in view["agent"]["note"]
    me = client.get(f"{API}/me", headers=alex).json()
    assert view["waiting_on_account_id"] == me["account"]["id"]


def test_runner_a_refused_finish_is_retained_without_unsafe_fallback(
    client, alex, todo, make_account, tmp_path
):
    """A finish Slate refuses (an older runner sending a removed field) must not vanish into
    a local log: the runner resends the bare outcome and comments the refusal on the card."""
    _, h = make_account("agent")
    item = _assign(client, alex, todo, title="Order printer ink")
    runner = _runner(client, h, tmp_path)
    real = runner._result
    runner._result = lambda *a: {**real(*a), "minutes": 3}  # a field the API does not take

    assert runner.once() == 3
    saved = json.loads(runner.state_path.read_text())["runs"][item["id"]]
    assert saved["state"] == "delivery_failed" and saved["result"]["minutes"] == 3
    view = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert view["completed_at"] is None
    assert client.get(f"{API}/items/{item['id']}/comments", headers=alex).json() == []


def test_runner_plain_output_exit_zero_is_not_terminal_evidence(
    client, alex, todo, make_account, tmp_path
):
    _, h = make_account("agent")
    item = _assign(client, alex, todo, title="Say hi")
    cmd = f'"{sys.executable}" -c "print(\'hello from the agent\')"'
    assert _runner(client, h, tmp_path, cmd).once(item["id"]) == 1
    reports = [
        e["after"] for e in _events(client, alex, item["id"]) if e["type"] == "item.reported"
    ]
    assert reports[-1]["state"] == "blocked"
    assert "without an explicit terminal result" in reports[-1]["note"]


def test_runner_passes_the_agents_reply_and_thread_to_the_card(
    client, alex, todo, make_account, tmp_path
):
    """Card conversation: a progress line's `thread` and the result's `reply` reach Slate, and
    the next run gets the answer on the card and the same thread on stdin."""
    agent, h = make_account("agent")
    item = _assign(client, alex, todo, title="Sign in to the mail server")
    script = tmp_path / "turn.py"
    script.write_text(
        "import json, sys\n"
        "task = json.load(sys.stdin)\n"
        "open(sys.argv[1], 'a').write(json.dumps(task.get('reply')) + '\\n')\n"
        "print(json.dumps({'note': 'Opened a session', 'thread': {'ref': 'sess-9'}}))\n"
        "out = {'outcome': 'blocked', 'waiting_on': 'alex', 'reply': 'Sign in here: x'}\n"
        "if task.get('reply'):\n"
        "    out = {'outcome': 'done', 'reply': 'Verified', 'thread': task['thread']}\n"
        "print(json.dumps(out))\n"
    )
    seen = tmp_path / "seen.txt"
    runner = _runner(client, h, tmp_path, f'"{sys.executable}" "{script}" "{seen}"')
    assert runner.once(item["id"]) == 1
    view = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert view["agent_threads"] == [{"account_id": agent["id"], "ref": "sess-9", "url": None}]
    comments = client.get(f"{API}/items/{item['id']}/comments", headers=alex).json()
    assert [c["body_md"] for c in comments] == ["Sign in here: x"]

    r = client.post(f"{API}/items/{item['id']}/comments", json={"body_md": "done"}, headers=alex)
    assert r.status_code == 201
    assert runner.once(item["id"]) == 0
    lines = seen.read_text().splitlines()
    assert json.loads(lines[0]) is None and json.loads(lines[1])["body_md"] == "done"
    view = client.get(f"{API}/items/{item['id']}", headers=alex).json()
    assert view["status"]["kind"] == "done"


def test_runner_hint_for_a_task_it_cannot_take_spawns_nothing(
    client, alex, todo, make_account, tmp_path
):
    _, h = make_account("agent")
    not_mine = _assign(client, alex, todo, title="Someone else's", who=())
    spawned = tmp_path / "spawned"
    cmd = f"\"{sys.executable}\" -c \"open(r'{spawned}', 'w').write('x')\""
    runner = _runner(client, h, tmp_path, cmd)
    assert runner.take(not_mine["id"]) is None
    assert runner.once(not_mine["id"]) == 2
    assert not spawned.exists()


def test_runner_keeps_the_stream_cursor(client, alex, todo, make_account, tmp_path):
    _, h = make_account("agent")
    runner = _runner(client, h, tmp_path)
    assert runner.cursor() is None
    runner.on_message({"event": "news", "id": "0001Ab2Cd3Ef", "data": {}})
    assert runner.cursor() == "0001Ab2Cd3Ef"
    assert _runner(client, h, tmp_path).cursor() == "0001Ab2Cd3Ef"  # survives a restart


def _mention(client, h, key, body, **extra):
    r = client.post(f"{API}/channels/{key}/messages", json={"body_md": body, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


ANSWER = """
import json, os, sys
turn = json.load(sys.stdin)
assert turn["item"] is None and turn["lease"] is None
chat = turn["chat"]
assert chat["channel"] == os.environ["SLATE_CHANNEL"]
assert (chat["thread_id"] or "") == os.environ["SLATE_THREAD_ID"]
tail = "/#/chat/" + chat["channel"] + ("?thread=" + chat["thread_id"] if chat["thread_id"] else "")
assert os.environ["SLATE_CHAT_URL"].endswith(tail)
print(json.dumps({"note": "Reading"}))
print(json.dumps({"outcome": "done", "reply": "Runner here: " + chat["message"]["body_md"]}))
"""


def test_runner_answers_a_chat_mention_in_the_channel(client, alex, todo, make_account, tmp_path):
    """A chat mention is a turn the runner takes like a task; the command gets `chat` on stdin
    and its result's `reply` is posted in the channel."""
    agent, h = make_account("agent")
    key = f"list:{todo['id']}"
    _mention(client, alex, key, "@agent how many are open?")
    script = tmp_path / "answer.py"
    script.write_text(ANSWER)
    runner = _runner(client, h, tmp_path, f'"{sys.executable}" "{script}"')

    assert runner.once() == 0
    page = client.get(f"{API}/channels/{key}/messages", headers=alex).json()
    assert [m["author_id"] for m in page][-1] == agent["id"]
    assert page[-1]["body_md"] == "Runner here: @agent how many are open?"
    assert page[-1]["parent_id"] is None
    turns = client.get(f"{API}/events", params={"type": "message.agent_turn"}, headers=alex)
    assert [e["after"]["state"] for e in turns.json()["events"]] == ["started", "done"]
    assert runner.once() == 2  # answered: nothing left


def test_runner_answers_a_thread_mention_in_the_thread_from_its_hint(
    client, alex, todo, make_account, tmp_path
):
    _, h = make_account("agent")
    key = f"list:{todo['id']}"
    root = _mention(client, alex, key, "Weekend plans")
    ask = _mention(client, alex, key, "@agent anything due?", parent_id=root["id"])
    script = tmp_path / "answer.py"
    script.write_text(ANSWER)
    runner = _runner(client, h, tmp_path, f'"{sys.executable}" "{script}"')

    # The stream's hint carries the channel and thread; the runner takes exactly that turn.
    hint = {"channel": key, "thread_id": root["id"], "message_id": ask["id"], "queued": False}
    runner.on_message({"event": "work", "id": "0001Ab2Cd3Ef", "data": hint})
    with runner._state_lock:
        runner._state_lock.wait_for(lambda: runner._workers == 0, timeout=20)
    thread = client.get(f"{API}/messages/{root['id']}/thread", headers=alex).json()
    assert thread[-1]["body_md"] == "Runner here: @agent anything due?"
    assert thread[-1]["parent_id"] == root["id"]


def test_runner_a_failed_chat_run_posts_nothing_and_releases_the_turn(
    client, alex, todo, make_account, tmp_path
):
    _, h = make_account("agent")
    key = f"list:{todo['id']}"
    _mention(client, alex, key, "@agent ping")
    cmd = f'"{sys.executable}" -c "import sys; print(\'no runtime\', file=sys.stderr); sys.exit(3)"'
    assert _runner(client, h, tmp_path, cmd).once() == 1
    page = client.get(f"{API}/channels/{key}/messages", headers=alex).json()
    assert len(page) == 1  # the collaborator's chat got no stray output
    turns = client.get(f"{API}/events", params={"type": "message.agent_turn"}, headers=alex)
    last = turns.json()["events"][-1]["after"]
    assert last["state"] == "released" and "no runtime" in last["note"]


DOC_ANSWER = """
import json, os, sys
turn = json.load(sys.stdin)
assert turn["item"] is None and turn["chat"] is None
doc = turn["doc"]
assert doc["doc_id"] == os.environ["SLATE_DOC_ID"]
assert (doc["thread_id"] or "") == os.environ["SLATE_THREAD_ID"]
tail = "/#/docs/" + doc["doc_id"] + ("?comment=" + doc["thread_id"] if doc["thread_id"] else "")
assert os.environ["SLATE_DOC_URL"].endswith(tail)
print(json.dumps({"note": "Reading the page"}))
reply = "On " + doc["title"] + ": " + doc["mention"]["excerpt"]
print(json.dumps({"outcome": "done", "reply": reply}))
"""


def _page(client, h, **body):
    r = client.post(f"{API}/docs", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_runner_answers_a_page_mention_with_a_new_thread(
    client, alex, todo, make_account, tmp_path
):
    """docs/agents-work-loop.md: a page mention is a `doc` turn on stdin, and the result's
    `reply` becomes the agent's comment (a new discussion thread for a body mention)."""
    agent, h = make_account("agent")
    page = _page(client, alex, title="Roof plan", body_md="Ask @agent for a price.")
    script = tmp_path / "answer.py"
    script.write_text(DOC_ANSWER)
    runner = _runner(client, h, tmp_path, f'"{sys.executable}" "{script}"')
    assert "doc" in runner.work_kinds  # on by default

    assert runner.once() == 0
    comments = client.get(f"{API}/docs/{page['id']}/comments", headers=alex).json()
    assert [(c["author_id"], c["parent_id"]) for c in comments] == [(agent["id"], None)]
    assert comments[0]["body_md"] == "On Roof plan: Ask @agent for a price."
    turns = client.get(f"{API}/events", params={"type": "doc.agent_turn"}, headers=alex)
    assert [e["after"]["state"] for e in turns.json()["events"]] == ["started", "done"]
    assert runner.once() == 2


def test_runner_answers_a_doc_thread_mention_from_its_hint(
    client, alex, todo, make_account, tmp_path
):
    _, h = make_account("agent")
    page = _page(client, alex, title="Spec", body_md="Table 14 ft.")
    r = client.post(
        f"{API}/docs/{page['id']}/comments", json={"body_md": "@agent right?"}, headers=alex
    )
    root = r.json()
    script = tmp_path / "answer.py"
    script.write_text(DOC_ANSWER)
    runner = _runner(client, h, tmp_path, f'"{sys.executable}" "{script}"')
    hint = {"doc_id": page["id"], "thread_id": root["id"], "comment_id": root["id"]}
    runner.on_message({"event": "work", "id": "0001Ab2Cd3Ef", "data": hint})
    with runner._state_lock:
        runner._state_lock.wait_for(lambda: runner._workers == 0, timeout=20)
    comments = client.get(f"{API}/docs/{page['id']}/comments", headers=alex).json()
    assert comments[-1]["parent_id"] == root["id"]
    assert comments[-1]["body_md"] == "On Spec: @agent right?"


def test_runner_a_failed_doc_run_posts_nothing(client, alex, todo, make_account, tmp_path):
    _, h = make_account("agent")
    page = _page(client, alex, title="P", body_md="@agent ping")
    cmd = f'"{sys.executable}" -c "import sys; print(\'no runtime\', file=sys.stderr); sys.exit(3)"'
    assert _runner(client, h, tmp_path, cmd).once() == 1
    assert client.get(f"{API}/docs/{page['id']}/comments", headers=alex).json() == []
    turns = client.get(f"{API}/events", params={"type": "doc.agent_turn"}, headers=alex)
    last = turns.json()["events"][-1]["after"]
    assert last["state"] == "released" and "no runtime" in last["note"]


def test_runner_without_doc_kind_leaves_page_mentions_alone(
    client, alex, todo, make_account, tmp_path
):
    _, h = make_account("agent")
    page = _page(client, alex, title="P", body_md="@agent ping")
    runner = _runner(client, h, tmp_path, env={"SLATE_WORK_KINDS": "task,chat"})
    assert runner.take() is None
    assert runner.take(f"doc:{page['id']}|") is None


def test_runner_drops_doc_kind_against_an_older_server(make_account, client, tmp_path):
    from slate_runner import SlateHTTPError

    _, h = make_account("agent")
    runner = _runner(client, h, tmp_path)
    sent = []

    def old_server(method, path, body=None):
        sent.append(list(body["kinds"]))
        if "doc" in body["kinds"]:
            raise SlateHTTPError(422, "kinds: input should be 'task' or 'chat'")
        return {"item": None, "lease": None, "chat": None}

    runner.call = old_server
    assert runner.take() is None
    assert sent == [["task", "chat", "doc"], ["task", "chat"]]
    assert runner.work_kinds == ["task", "chat"]


# --- slate_runner.py --config: many agents in one process ------------------------------------


def test_runner_config_reads_agents_env_files_and_tokens(tmp_path, monkeypatch):
    from slate_runner import load_config

    monkeypatch.setenv("FROM_PROCESS", "p")
    (tmp_path / "shared.env").write_text('# gateway\nKEY="k1"\nTOKEN_A=ta\n')
    (tmp_path / "b.env").write_text("KEY=k2\n")
    (tmp_path / "b.token").write_text("tb\n")
    (tmp_path / "runner.toml").write_text(
        'url = "https://slate.example"\n'
        'env_file = "shared.env"\n'
        'exec = "python run.py"\n'
        '[[agent]]\nname = "a"\ntoken_env = "TOKEN_A"\n'
        'env = { API = "http://h/p/a", AUTH = "${KEY}-${FROM_PROCESS}" }\n'
        '[[agent]]\nname = "b"\ntoken_file = "b.token"\nenv_file = "b.env"\n'
        'exec = "python other.py ${KEY}"\nparallel = 2\nstate = "elsewhere.json"\n'
    )
    a, b = load_config(tmp_path / "runner.toml")
    assert (a["name"], a["token"], a["url"], a["command"]) == (
        "a",
        "ta",
        "https://slate.example",
        "python run.py",
    )
    assert a["env"]["API"] == "http://h/p/a" and a["env"]["AUTH"] == "k1-p"
    assert a["state"] == tmp_path / "a-state.json" and a["cwd"] == tmp_path
    assert (b["token"], b["command"], b["parallel"]) == ("tb", "python other.py k2", 2)
    assert b["state"] == tmp_path / "elsewhere.json"


def test_runner_config_refuses_a_missing_token_or_a_repeated_name(tmp_path):
    import pytest
    from slate_runner import load_config

    cfg = tmp_path / "runner.toml"
    cfg.write_text('url = "u"\nexec = "x"\n[[agent]]\nname = "a"\ntoken_env = "NOPE_NOT_SET"\n')
    with pytest.raises(ValueError, match="no token for a"):
        load_config(cfg)
    cfg.write_text(
        'url = "u"\nexec = "x"\n[[agent]]\nname = "a"\ntoken_file = "t"\n'
        '[[agent]]\nname = "a"\ntoken_file = "t"\n'
    )
    (tmp_path / "t").write_text("tok")
    with pytest.raises(ValueError, match="unique name"):
        load_config(cfg)


# --- live streams give up on a silent connection -----------------------------------------------


def _timeouts_seen(call):
    """Run `call(client)` against a transport that records each request's read timeout."""
    import httpx

    seen: list = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, text="", headers={"content-type": "text/event-stream"})

    call(httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


def test_streams_have_a_finite_read_timeout(tmp_path):
    """The server pings every 15 s, so a stream that hears nothing for a minute is dead. A stream
    read with no timeout waits forever on a connection that dropped without a close, and the
    runner then never hears new work until someone restarts it."""
    import slate_runner

    def runner(c):
        r = Runner("http://x", "t", "true", client=c, state_path=tmp_path / "s.json")
        list(r.stream())

    def client_events(c):
        list(Slate("http://x", "t", client=c).stream())

    def client_agent(c):
        list(Slate("http://x", "t", client=c).agent_stream())

    for call in (runner, client_events, client_agent):
        (read,) = _timeouts_seen(call)
        assert read is not None and read <= slate_runner.STREAM_READ_TIMEOUT
