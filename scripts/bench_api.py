"""Latency of the calls agents make most, against a real server on a throwaway database.

    uv run python scripts/bench_api.py [items]      # default 2000

Starts `uvicorn slate.app:app` on 127.0.0.1:8765 with a temp SQLite file, seeds `items` items
across five lists, then times each call (keep-alive, gzip) and prints p50/p95. Nothing touches
your real database.
"""

from __future__ import annotations

import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765


def main(n: int) -> None:
    tmp = tempfile.mkdtemp()
    env = dict(
        os.environ,
        SLATE_DATABASE_PATH=f"{tmp}/bench.db",
        SLATE_SCHEDULER_ENABLED="false",
        SLATE_RATE_LIMIT_ENABLED="false",  # seeding thousands of items is not an agent
    )
    subprocess.run(["uv", "run", "slate", "migrate"], cwd=ROOT, env=env, check=True)
    # The venv's uvicorn directly: under `uv run`, terminate() would leave it running.
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "slate.app:app",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
    )
    try:
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{PORT}/healthz")
                break
            except httpx.TransportError:
                time.sleep(0.2)
        c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}/api/v1")
        token = c.post("/bootstrap", json={"handle": "alex", "display_name": "Alex"}).json()
        c.headers["Authorization"] = f"Bearer {token['token']}"
        section = c.post("/sections", json={"name": "Bench", "audience": "personal"}).json()
        lists = [
            c.post("/lists", json={"section_id": section["id"], "name": f"L{i}"}).json()
            for i in range(5)
        ]
        ids = []
        start = time.perf_counter()
        for i in range(n):
            body = {
                "list_id": lists[i % 5]["id"],
                "title": f"task {i}",
                "tags": ["a" if i % 3 else "b"],
                "due_at": f"2026-11-0{1 + i % 9}",
            }
            ids.append(c.post("/items", json=body).json()["id"])
        print(f"seeded {n} items: {(time.perf_counter() - start) / n * 1000:.2f} ms per create")

        def bench(name, fn, runs=200):
            xs = []
            for _ in range(runs):
                t = time.perf_counter()
                r = fn()
                xs.append((time.perf_counter() - t) * 1000)
                assert r.status_code < 400, r.text
            xs.sort()
            print(
                f"{name:40s} p50 {statistics.median(xs):7.2f}  "
                f"p95 {xs[int(len(xs) * 0.95)]:7.2f} ms  {len(r.content):>8} bytes"
            )

        one_list = {"list_id": lists[0]["id"]}
        etag = c.get("/items", params=one_list).headers["etag"]
        batch = {
            "ops": [
                {
                    "tool": "item",
                    "action": "create",
                    "params": {"list_id": lists[1]["id"], "title": f"b{i}"},
                }
                for i in range(20)
            ]
        }
        bench("GET /me", lambda: c.get("/me"))
        bench("GET /items?list_id (all fields)", lambda: c.get("/items", params=one_list), 50)
        bench(
            "GET /items?list_id&fields=id,title,status",
            lambda: c.get("/items", params={**one_list, "fields": "id,title,status"}),
            50,
        )
        bench(
            "GET /items?list_id + If-None-Match (304)",
            lambda: c.get("/items", params=one_list, headers={"If-None-Match": etag}),
        )
        bench("GET /items (everything)", lambda: c.get("/items"), 20)
        bench("GET /items/{id}", lambda: c.get(f"/items/{random.choice(ids)}"))
        bench(
            "PATCH /items/{id}",
            lambda: c.patch(f"/items/{random.choice(ids)}", json={"title": str(random.random())}),
        )
        bench(
            "POST /items", lambda: c.post("/items", json={"list_id": lists[1]["id"], "title": "x"})
        )
        bench(
            "POST /items/{id}/comments",
            lambda: c.post(f"/items/{random.choice(ids)}/comments", json={"body_md": "hi"}),
        )
        bench("POST /batch (20 creates, atomic)", lambda: c.post("/batch", json=batch), 50)
        bench("GET /today", lambda: c.get("/today"), 50)
        bench("GET /events?since=0&limit=100", lambda: c.get("/events", params={"since": "0"}), 50)
    finally:
        server.terminate()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2000)
