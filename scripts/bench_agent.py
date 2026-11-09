"""Latency of the agent loop, against a real server on a throwaway database.

    uv run python scripts/bench_agent.py [--items 2000] [--runs 200] [--markdown]

Starts `uvicorn slate.app:app` on 127.0.0.1:8766 with a temp SQLite file, seeds `--items`
items across five lists (a fifth of them assigned to the agent), then times what an agent
does all day, each call `--runs` times over one keep-alive connection:

- check-in (fresh, from a cursor, and the `304` when nothing changed), roster, a narrow list;
- the loop's writes: claim, progress report, comment, done, and the whole loop end to end;
- the same loop as one `POST /batch`;
- MCP over HTTP (`tools/call` on `/mcp`) for check-in and a report;
- change-to-stream: how long after a write is sent its event arrives on an open SSE stream.

Prints p50/p95/p99 per call (`--markdown` for the table in docs/agents/latency.md). Nothing
touches your real database.
"""

from __future__ import annotations

import argparse
import os
import platform
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"


def start_server(tmp: str) -> subprocess.Popen:
    env = dict(
        os.environ,
        SLATE_DATABASE_PATH=f"{tmp}/bench.db",
        SLATE_SCHEDULER_ENABLED="false",
        SLATE_RATE_LIMIT_ENABLED="false",
    )
    subprocess.run(["uv", "run", "slate", "migrate"], cwd=ROOT, env=env, check=True)
    # The venv's uvicorn directly: under `uv run`, terminate() would leave it running.
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "slate.app:app", "--port", str(PORT)]
        + ["--log-level", "warning"],
        cwd=ROOT,
        env=env,
    )
    for _ in range(150):
        try:
            httpx.get(f"{BASE}/healthz")
            return server
        except httpx.TransportError:
            time.sleep(0.2)
    server.terminate()
    raise SystemExit("server did not start")


class Bench:
    def __init__(self, runs: int) -> None:
        self.runs = runs
        self.rows: list[tuple[str, list[float], int]] = []

    def time(self, name: str, fn: Callable[[], httpx.Response | None], runs: int | None = None):
        xs = []
        size = 0
        for _ in range(runs or self.runs):
            t = time.perf_counter()
            r = fn()
            xs.append((time.perf_counter() - t) * 1000)
            if r is not None:
                assert r.status_code < 400, f"{name}: {r.status_code} {r.text[:200]}"
                size = len(r.content)
        self.add(name, xs, size)

    def add(self, name: str, xs: list[float], size: int = 0) -> None:
        xs = sorted(xs)
        self.rows.append((name, xs, size))
        print(
            f"{name:44s} p50 {pct(xs, 0.5):7.2f}  p95 {pct(xs, 0.95):7.2f}  "
            f"p99 {pct(xs, 0.99):7.2f} ms  {size:>7} B",
            file=sys.stderr,
        )

    def markdown(self) -> str:
        out = ["| Call | p50 ms | p95 ms | p99 ms | Bytes |", "|---|---:|---:|---:|---:|"]
        for name, xs, size in self.rows:
            out.append(
                f"| {name} | {pct(xs, 0.5):.1f} | {pct(xs, 0.95):.1f} | {pct(xs, 0.99):.1f} "
                f"| {size or '—'} |"
            )
        return "\n".join(out)


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    if q == 0.5:
        return statistics.median(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def mcp_call(c: httpx.Client, name: str, arguments: dict) -> httpx.Response:
    r = c.post(
        f"{BASE}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": arguments}},
        headers={"Accept": "application/json, text/event-stream"},
    )  # fmt: skip
    body = r.json()
    assert "error" not in body and not body["result"].get("isError"), body
    return r


def main(n: int, runs: int, markdown: bool) -> None:
    tmp = tempfile.mkdtemp()
    server = start_server(tmp)
    try:
        owner = httpx.Client(base_url=f"{BASE}/api/v1")
        boot = owner.post("/bootstrap", json={"handle": "alex", "display_name": "Alex"}).json()
        owner.headers["Authorization"] = f"Bearer {boot['token']}"
        acct = owner.post(
            "/accounts", json={"handle": "my-agent", "display_name": "My Agent", "kind": "agent"}
        ).json()
        tok = owner.post(f"/accounts/{acct['id']}/tokens", json={"scopes": ["write"]}).json()
        agent = httpx.Client(
            base_url=f"{BASE}/api/v1",
            headers={"Authorization": f"Bearer {tok['token']}", "X-Slate-Session-Ref": "bench"},
        )
        section = owner.post("/sections", json={"name": "Bench", "audience": "ai"}).json()
        lists = [
            owner.post("/lists", json={"section_id": section["id"], "name": f"L{i}"}).json()
            for i in range(5)
        ]
        seed = [
            {"tool": "item", "action": "create",
             "params": {"list_id": lists[i % 5]["id"], "title": f"task {i}",
                        "due_at": f"2026-11-{1 + i % 28:02d}",
                        "assignees": ["my-agent"] if i % 5 == 0 else []}}
            for i in range(n)
        ]  # fmt: skip
        for k in range(0, n, 100):
            assert owner.post("/batch", json={"ops": seed[k : k + 100]}).status_code == 200
        work = [
            owner.post("/items", json={"list_id": lists[0]["id"], "title": f"loop {i}"}).json()
            for i in range(runs * 3)
        ]
        print(f"seeded {n} items; {len(work)} loop items", file=sys.stderr)

        b = Bench(runs)
        b.time("GET /me", lambda: agent.get("/me"))
        b.time("GET /checkin (cold start)", lambda: agent.get("/checkin"), max(20, runs // 4))
        since = agent.get("/checkin").json()["next_since"]
        b.time("GET /checkin?since= (nothing new)", lambda: agent.get(f"/checkin?since={since}"))
        trim = "fields=id,title,status,priority,due_at"
        b.time(
            "GET /checkin?since=&fields=id,title,status,priority,due_at",
            lambda: agent.get(f"/checkin?since={since}&{trim}"),
        )
        etag = agent.get(f"/checkin?since={since}").headers["etag"]
        b.time(
            "GET /checkin + If-None-Match (304)",
            lambda: agent.get(f"/checkin?since={since}", headers={"If-None-Match": etag}),
        )
        b.time("GET /agents", lambda: agent.get("/agents"))
        b.time(
            "GET /items?assignee=my-agent&fields=id,title,due_at",
            lambda: agent.get("/items?assignee=my-agent&fields=id,title,due_at"),
            max(20, runs // 4),
        )

        ids = iter(w["id"] for w in work)
        claimed: list[str] = []

        def claim():
            i = next(ids)
            claimed.append(i)
            return agent.post(f"/items/{i}/report", json={"state": "working", "claim": True})

        b.time("POST /items/{id}/report claim", claim)
        it = iter(claimed)
        b.time(
            "POST /items/{id}/report progress",
            lambda: agent.post(
                f"/items/{next(it)}/report",
                json={"state": "working", "note": "halfway", "progress": 50, "comment": False},
            ),
        )
        it = iter(claimed)
        b.time(
            "POST /items/{id}/comments",
            lambda: agent.post(f"/items/{next(it)}/comments", json={"body_md": "Result: link"}),
        )
        it = iter(claimed)
        b.time(
            "POST /items/{id}/report done",
            lambda: agent.post(f"/items/{next(it)}/report", json={"state": "done"}),
        )

        def loop():
            i = next(ids)
            agent.get(f"/checkin?since={since}&{trim}")
            agent.post(f"/items/{i}/report", json={"state": "working", "claim": True})
            agent.post(f"/items/{i}/report", json={"state": "working", "note": "50%",
                                                   "progress": 50, "comment": False})  # fmt: skip
            agent.post(f"/items/{i}/comments", json={"body_md": "Result: link"})
            return agent.post(f"/items/{i}/report", json={"state": "done"})

        b.time("Whole loop: check-in (trimmed), claim, progress, comment, done", loop)

        def batch_loop():
            i = next(ids)
            return agent.post(
                "/batch",
                json={"ops": [
                    {"tool": "item", "action": "report",
                     "params": {"id": i, "state": "working", "claim": True}},
                    {"tool": "comment", "action": "add", "params": {"id": i, "body_md": "Result"}},
                    {"tool": "item", "action": "report", "params": {"id": i, "state": "done"}},
                ]},
            )  # fmt: skip

        b.time("Same loop as one POST /batch (claim, comment, done)", batch_loop)

        mcp = httpx.Client(headers={"Authorization": f"Bearer {tok['token']}"})
        b.time("MCP slate_checkin", lambda: mcp_call(mcp, "slate_checkin", {"params": {}}))
        target = work[0]["id"]
        b.time(
            "MCP slate_item report",
            lambda: mcp_call(
                mcp,
                "slate_item",
                {"action": "report",
                 "params": {"id": target, "state": "working", "progress": 10, "comment": False}},
            ),
        )  # fmt: skip

        b.add("Write → event on an open SSE stream", stream_latency(owner, agent, target, runs))
        if markdown:
            host = f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs"
            print(
                f"Items: {n}. Runs per call: {runs}. Python {platform.python_version()}, {host}.\n"
            )
            print(b.markdown())
    finally:
        server.terminate()
        server.wait(timeout=10)


def stream_latency(owner: httpx.Client, agent: httpx.Client, item_id: str, runs: int):
    """The owner changes a title; time from sending the PATCH to the agent's stream having it."""
    arrivals: queue.Queue[float] = queue.Queue()
    ready = threading.Event()

    def listen():
        params = {"type": "item.updated", "item_id": item_id}
        try:
            with (
                httpx.Client(timeout=None, headers=agent.headers) as c,
                c.stream("GET", f"{BASE}/api/v1/events/stream", params=params) as r,
            ):
                for line in r.iter_lines():
                    if line.startswith(": ready"):
                        ready.set()
                    elif line.startswith("data:"):
                        arrivals.put(time.perf_counter())
        except httpx.HTTPError:
            pass  # the server stops at the end of the run

    threading.Thread(target=listen, daemon=True).start()
    ready.wait(10)
    xs = []
    for i in range(runs):
        t = time.perf_counter()
        owner.patch(f"/items/{item_id}", json={"title": f"stream {i}"})
        xs.append((arrivals.get(timeout=5) - t) * 1000)
    return xs


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--items", type=int, default=2000)
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--markdown", action="store_true", help="print the docs table to stdout")
    a = ap.parse_args()
    main(a.items, a.runs, a.markdown)
