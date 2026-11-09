"""Slate under load: a big board, many agents writing, several boards watching.

    uv run python scripts/load_test.py                  # 20k tasks, 50 lists, 10 agents, 30 s
    uv run python scripts/load_test.py --ci             # CI size, against bench/budgets.json
    uv run python scripts/load_test.py --json out.json  # also write the numbers as JSON

Seeds a throwaway SQLite file directly (items, tags, assignees and one `item.created` event per
item, so the event log is realistic), starts `uvicorn slate.app:app` on it, then for
`--seconds`:

- `--boards` browsers each do the board's first load (the seven GETs of `#loadAll`), hold the
  SSE stream open and refetch the items the board's reducer would refetch (one
  `GET /items?ids=` per 50 ms, as `store.svelte.ts` does);
- `--agents` agents each loop: `GET /checkin` from their cursor (with `If-None-Match`), then a
  `POST /batch` of five changes (status, priority, title, comment, report) on their own items,
  then sleep `--think` seconds.

It reports p50/p95/p99 per call, how long after a commit each board saw each event (fan-out
lag, from the event's `at`), events missed by any board, writes refused or failed, the
server's resident memory, and SQLite write-lock waits (from `GET /api/v1/stats`). Nothing
touches your real database.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"
API = f"{BASE}/api/v1"
BUDGETS = ROOT / "bench" / "budgets.json"

# The reducer refetches an item on these (web/src/lib/reducer.ts); the rest apply in place.
# LOAD_BOARD=old replays an unbatched board for comparison: one GET /items/{id} per
# refetch and the whole /today on every quiet spell.
OLD_BOARD = os.environ.get("LOAD_BOARD") == "old"
BATCH_REFETCH = not OLD_BOARD
TODAY = "/today"  # LOAD_BOARD=old only; the board counts Today from its own items
REFETCH_TYPES = {"item.assigned", "item.linked", "item.unlinked", "item.split"}


# --- seeding -------------------------------------------------------------------------------


def seed(db_path: str, n_items: int, n_lists: int, n_agents: int) -> dict:
    """Fill a fresh database through the service layer (bulk inserts inside one change)."""
    os.environ["SLATE_DATABASE_PATH"] = db_path
    from sqlalchemy import insert

    from slate import db
    from slate.ids import new_event_id, new_id
    from slate.models import Event, Item, ItemAssignee, ItemTag, Priority, Source, utcnow
    from slate.service import accounts, lists, sections, tags
    from slate.service.core import Actor, apply_change

    rng = random.Random(7)
    with db.SessionLocal() as s:
        owner, _, owner_token = accounts.bootstrap(s, handle="alex", display_name="Alex")
        actor = Actor(account_id=owner.id, source=Source.system)
        agents = []
        for n in range(n_agents):
            a = accounts.create_account(
                s, actor, handle=f"agent{n}", display_name=f"Agent {n}", kind="agent"
            )
            _, token = accounts.create_token(s, actor, account_id=a.id, scopes=["write"])
            agents.append({"id": a.id, "handle": a.handle, "token": token})
        secs = [
            sections.create_section(s, actor, name=f"Area {k}", audience="ai") for k in range(5)
        ]
        all_lists = [
            lists.create_list(s, actor, section_id=secs[k % 5].id, name=f"List {k}")
            for k in range(n_lists)
        ]
        statuses = {lst.id: [st.id for st in lists.statuses_of(s, lst.id)] for lst in all_lists}
        all_tags = tags.ensure_tags(s, actor, [f"tag{k}" for k in range(30)])
        now = utcnow()
        priorities = [None, Priority.low, Priority.normal, Priority.high, Priority.urgent]
        item_rows, tag_rows, assignee_rows, event_rows = [], [], [], []
        by_agent: dict[str, list[str]] = defaultdict(list)
        for n in range(n_items):
            lst = all_lists[n % n_lists]
            item_id = new_id()
            st = statuses[lst.id]
            due = now + timedelta(days=rng.randint(-20, 40)) if rng.random() < 0.7 else None
            item_rows.append(
                {
                    "id": item_id,
                    "list_id": lst.id,
                    "kind": "task",
                    "title": f"Task {n} {rng.choice(['fix', 'write', 'call', 'ship', 'plan'])}",
                    "body_md": "Some notes about the task." if n % 4 == 0 else "",
                    "status_id": st[0] if rng.random() < 0.6 else rng.choice(st),
                    "priority": rng.choice(priorities),
                    "due_at": due,
                    "position": n,
                    "created_by": owner.id,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            for tag in rng.sample(all_tags, rng.randint(0, 3)):
                tag_rows.append({"item_id": item_id, "tag_id": tag.id})
            if n % 2 == 0:
                agent = agents[(n // 2) % n_agents]
                assignee_rows.append({"item_id": item_id, "account_id": agent["id"]})
                by_agent[agent["handle"]].append(item_id)
            event_rows.append(
                {
                    "id": new_event_id(),
                    "item_id": item_id,
                    "subject_id": item_id,
                    "actor_id": owner.id,
                    "source": Source.system,
                    "type": "item.created",
                    "at": now,
                }
            )
        with apply_change(s, actor) as cx:
            for k in range(0, n_items, 2000):
                s.execute(insert(Item), item_rows[k : k + 2000])
                s.execute(insert(Event), event_rows[k : k + 2000])
            if tag_rows:
                s.execute(insert(ItemTag), tag_rows)
            s.execute(insert(ItemAssignee), assignee_rows)
            cx.record("test.seeded", after={"items": n_items})
    db.engine.dispose()
    return {"owner": owner_token, "agents": agents, "work": dict(by_agent)}


# --- server --------------------------------------------------------------------------------


def start_server(db_path: str, static: bool = False) -> subprocess.Popen:
    env = dict(
        os.environ,
        **({"SLATE_STATIC_DIR": str(ROOT / "web" / "dist")} if static else {}),
        SLATE_DATABASE_PATH=db_path,
        SLATE_SCHEDULER_ENABLED="false",
        SLATE_AUTOMATIONS_ENABLED="false",
        SLATE_PUSH_ENABLED="false",
        SLATE_RATE_LIMIT_ENABLED="false",  # ten agents in a tight loop are the point
    )
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


def rss_mb(pid: int) -> tuple[float, float]:
    """(current, peak) resident memory of the server in MB, from /proc (Linux only)."""
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return (0.0, 0.0)
    vals = {}
    for line in status.splitlines():
        key, _, rest = line.partition(":")
        if key in ("VmRSS", "VmHWM"):
            vals[key] = int(rest.split()[0]) / 1024
    return (vals.get("VmRSS", 0.0), vals.get("VmHWM", 0.0))


# --- measuring -----------------------------------------------------------------------------


class Timings:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: dict[str, list[float]] = defaultdict(list)
        self.sizes: dict[str, int] = {}
        self.errors: dict[str, int] = defaultdict(int)

    def time(self, name: str, fn):
        t = time.perf_counter()
        try:
            r = fn()
        except httpx.HTTPError:
            with self._lock:
                self.errors[name] += 1
            return None
        ms = (time.perf_counter() - t) * 1000
        with self._lock:
            self.calls[name].append(ms)
            if r.status_code >= 400:
                self.errors[name] += 1
            elif r.status_code != 304:
                self.sizes[name] = len(r.content)
        return r


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    if q == 0.5:
        return statistics.median(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def parse_at(at: str) -> float:
    return datetime.strptime(at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC).timestamp()


class Board(threading.Thread):
    """One browser tab: first load, then the SSE stream and the reducer's refetches."""

    def __init__(
        self, n: int, token: str, t: Timings, stop: threading.Event, closed: threading.Event
    ) -> None:
        super().__init__(daemon=True, name=f"board{n}")
        self.token, self.t, self.stop, self.closed = token, t, stop, closed
        self.ready = threading.Event()
        self.seen: dict[str, float] = {}  # event id -> lag in ms
        self.item_count = 0
        self._pending: set[str] = set()
        self.today_due = 0.0  # monotonic time of the last item event, 0 when none waits
        self._plock = threading.Lock()

    def first_load(self, c: httpx.Client) -> None:
        paths = ["/sections", "/lists", "/items", "/views", "/favorites"]
        if OLD_BOARD:
            paths.insert(3, TODAY)
        t0 = time.perf_counter()
        for p in paths:
            r = self.t.time(f"board load GET {p}", lambda p=p: c.get(p))
            if p == "/items" and r is not None:
                self.item_count = len(r.json())
        self.t.calls["board first load (GETs in order)"].append((time.perf_counter() - t0) * 1000)

    def refetcher(self, c: httpx.Client) -> None:
        while not self.stop.is_set():
            time.sleep(0.05)
            # The sidebar's Today count: 300 ms after the last item event (a trailing debounce).
            if OLD_BOARD and self.today_due and time.monotonic() - self.today_due > 0.3:
                self.today_due = 0.0
                self.t.time(f"board refetch GET {TODAY}", lambda: c.get(TODAY))
            with self._plock:
                ids, self._pending = self._pending, set()
            if ids and BATCH_REFETCH:
                q = ",".join(sorted(ids)[:200])
                self.t.time("board refetch GET /items?ids=", lambda q=q: c.get(f"/items?ids={q}"))
            for i in [] if BATCH_REFETCH else ids:
                self.t.time("board refetch GET /items/{id}", lambda i=i: c.get(f"/items/{i}"))

    def run(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}"}
        with httpx.Client(base_url=API, headers=headers, timeout=30) as c:
            self.first_load(c)
            threading.Thread(target=self.refetcher, args=(c,), daemon=True).start()
            try:
                with (
                    httpx.Client(timeout=None, headers=headers) as sc,
                    sc.stream("GET", f"{API}/events/stream") as r,
                ):
                    for line in r.iter_lines():
                        if self.closed.is_set():
                            return
                        if line.startswith(": ready"):
                            self.ready.set()
                        elif line.startswith("data:"):
                            now = time.time()
                            ev = json.loads(line[5:])
                            self.seen[ev["id"]] = (now - parse_at(ev["at"])) * 1000
                            if ev["type"].startswith("item.") and ev["type"] != "item.commented":
                                self.today_due = time.monotonic()
                            if ev["type"] in REFETCH_TYPES and ev.get("item_id"):
                                with self._plock:
                                    self._pending.add(ev["item_id"])
            except httpx.HTTPError:
                pass


class Agent(threading.Thread):
    def __init__(
        self, info: dict, work: list[str], t: Timings, stop: threading.Event, think: float
    ) -> None:
        super().__init__(daemon=True, name=info["handle"])
        self.info, self.work, self.t, self.stop, self.think = info, work, t, stop, think
        self.event_ids: list[str] = []
        self.cycles = 0

    def run(self) -> None:
        rng = random.Random(self.info["handle"])
        headers = {"Authorization": f"Bearer {self.info['token']}"}
        with httpx.Client(base_url=API, headers=headers, timeout=30) as c:
            r = self.t.time("agent GET /checkin (first)", lambda: c.get("/checkin?queue_limit=50"))
            since = r.json()["next_since"] if r is not None and r.status_code == 200 else None
            etag = None
            while not self.stop.is_set():
                q = "/checkin?fields=id,title,status,due_at&queue_limit=50"
                if since:
                    q += f"&since={since}"
                h = {"If-None-Match": etag} if etag else {}
                r = self.t.time("agent GET /checkin?since=", lambda q=q, h=h: c.get(q, headers=h))
                if r is not None and r.status_code == 200:
                    since = r.json()["next_since"]
                    etag = r.headers.get("etag")
                ids = rng.sample(self.work, 4)
                ops = [
                    {"tool": "item", "action": "update",
                     "params": {"id": ids[0], "status": rng.choice(["To do", "In progress"])}},
                    {"tool": "item", "action": "update",
                     "params": {"id": ids[1], "priority": rng.choice(["low", "normal", "high"])}},
                    {"tool": "item", "action": "update",
                     "params": {"id": ids[2], "title": f"Task {rng.randint(0, 10**6)}"}},
                    {"tool": "comment", "action": "add",
                     "params": {"id": ids[3], "body_md": "Progress note"}},
                    {"tool": "item", "action": "report",
                     "params": {"id": ids[3], "state": "working", "progress": rng.randint(1, 99),
                                "comment": False}},
                ]  # fmt: skip
                r = self.t.time(
                    "agent POST /batch (5 changes)",
                    lambda ops=ops: c.post("/batch", json={"ops": ops}),
                )
                self.cycles += 1
                if self.think:
                    time.sleep(rng.uniform(0.5, 1.5) * self.think)


def run(args: argparse.Namespace) -> dict:
    tmp = tempfile.mkdtemp()
    db_path = f"{tmp}/load.db"
    subprocess.run(
        [sys.executable, "-m", "slate.cli", "migrate"],
        cwd=ROOT,
        env=dict(os.environ, SLATE_DATABASE_PATH=db_path),
        check=True,
        stdout=subprocess.DEVNULL,
    )
    t0 = time.perf_counter()
    seeded = seed(db_path, args.items, args.lists, args.agents)
    print(f"seeded {args.items} items in {args.lists} lists in {time.perf_counter() - t0:.1f}s",
          file=sys.stderr)  # fmt: skip
    if args.browser and not (ROOT / "web" / "dist" / "index.html").exists():
        raise SystemExit("--browser needs the built board: cd web && npm run build")
    server = start_server(db_path, static=args.browser)
    t = Timings()
    stop = threading.Event()
    closed = threading.Event()  # boards keep listening until the last events are in
    try:
        boards = [Board(n, seeded["owner"], t, stop, closed) for n in range(args.boards)]
        for b in boards:
            b.start()
        for b in boards:
            b.ready.wait(60)
        idle_rss = rss_mb(server.pid)
        agents = [
            Agent(info, seeded["work"][info["handle"]], t, stop, args.think)
            for info in seeded["agents"]
        ]
        start_ids = httpx.get(
            f"{API}/events?tail=true&limit=1",
            headers={"Authorization": f"Bearer {seeded['owner']}"},
        ).json()["next_since"]
        for a in agents:
            a.start()
        browser = None
        if args.browser:  # a real Chromium opens the board while the agents work
            browser = subprocess.Popen(
                ["node", "web/scripts/board-perf.mjs", BASE, seeded["owner"]],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                text=True,
            )
        peak_rss = idle_rss
        end = time.time() + args.seconds
        while time.time() < end:
            time.sleep(0.5)
            cur = rss_mb(server.pid)
            peak_rss = (max(peak_rss[0], cur[0]), max(peak_rss[1], cur[1]))
        browser_result = None
        if browser is not None:
            stdout, _ = browser.communicate(timeout=300)
            browser_result = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else None
        stop.set()
        for a in agents:
            a.join(30)
        time.sleep(2)  # the last events reach the boards
        closed.set()

        owner = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {seeded['owner']}"})
        written: list[str] = []
        cursor = start_ids
        while True:
            page = owner.get(f"/events?since={cursor}&limit=1000").json()
            written += [e["id"] for e in page["events"]]
            cursor = page["next_since"]
            if not page["has_more"]:
                break
        stats = owner.get("/stats").json()
        # A cold sample of the reads agents and the board make, after the storm.
        for _ in range(20):
            t.time("GET /items (all, owner)", lambda: owner.get("/items"))
        for _ in range(50):
            t.time(
                "GET /items?list_id= (one list)",
                lambda: owner.get("/items", params={"list_id": _first_list(owner)}),
            )
        for _ in range(50):
            t.time("GET /today (whole items)", lambda: owner.get("/today"))
        missed = sum(1 for b in boards for eid in written if eid not in b.seen)
        lags = [b.seen[eid] for b in boards for eid in written if eid in b.seen]
        cycles = sum(a.cycles for a in agents)
        result = {
            "config": {
                "items": args.items, "lists": args.lists, "agents": args.agents,
                "boards": args.boards, "seconds": args.seconds, "think": args.think,
                "host": f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs, "
                        f"Python {platform.python_version()}",
            },
            "calls": {
                name: {"n": len(xs), "p50": round(pct(xs, 0.5), 1), "p95": round(pct(xs, 0.95), 1),
                       "p99": round(pct(xs, 0.99), 1), "bytes": t.sizes.get(name),
                       "errors": t.errors.get(name, 0)}
                for name, xs in sorted(t.calls.items())
            },
            "fanout_ms": {"n": len(lags), "p50": round(pct(lags, 0.5), 1),
                          "p95": round(pct(lags, 0.95), 1), "p99": round(pct(lags, 0.99), 1),
                          "max": round(max(lags), 1) if lags else 0.0},
            "events_written": len(written),
            "events_missed": missed,
            "writes_per_second": round(len(written) / args.seconds, 1),
            "batches": cycles,
            "errors": sum(t.errors.values()),
            "board_items_loaded": boards[0].item_count,
            "rss_mb": {"idle": round(idle_rss[0], 1), "peak": round(peak_rss[1], 1)},
            "write_lock": stats.get("write_lock"),
            "item_cache": stats.get("item_cache"),
            "browser": browser_result,
        }  # fmt: skip
        return result
    finally:
        stop.set()
        closed.set()
        server.terminate()
        server.wait(timeout=10)


_LIST: list[str] = []


def _first_list(c: httpx.Client) -> str:
    if not _LIST:
        _LIST.append(c.get("/lists").json()[0]["id"])
    return _LIST[0]


def report(result: dict) -> str:
    cfg = result["config"]
    out = [
        f"{cfg['items']} items, {cfg['lists']} lists, {cfg['agents']} agents, "
        f"{cfg['boards']} boards, {cfg['seconds']} s, think {cfg['think']} s. {cfg['host']}.",
        "",
        "| Call | n | p50 ms | p95 ms | p99 ms | Bytes | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, c in result["calls"].items():
        out.append(
            f"| {name} | {c['n']} | {c['p50']} | {c['p95']} | {c['p99']} "
            f"| {c['bytes'] or '—'} | {c['errors']} |"
        )
    f = result["fanout_ms"]
    wl = result.get("write_lock") or {}
    out += [
        "",
        f"- Commit → board (SSE fan-out): p50 {f['p50']} ms, p95 {f['p95']} ms, "
        f"p99 {f['p99']} ms, max {f['max']} ms over {f['n']} deliveries.",
        f"- Events written: {result['events_written']} ({result['writes_per_second']}/s) in "
        f"{result['batches']} batches; missed by a board: {result['events_missed']}; "
        f"failed calls: {result['errors']}.",
        f"- Write lock wait: p50 {wl.get('wait_p50_ms', '—')} ms, p95 {wl.get('wait_p95_ms', '—')} "
        f"ms; held p50 {wl.get('held_p50_ms', '—')} ms, p95 {wl.get('held_p95_ms', '—')} ms.",
        f"- Server memory: {result['rss_mb']['idle']} MB with the boards loaded, peak "
        f"{result['rss_mb']['peak']} MB.",
    ]
    b = result.get("browser")
    if b:
        groups = ", ".join(f"{k} {v} ms" for k, v in b["regroup_ms"].items())
        out.append(
            f"- Chromium, during the load: first load to cards {b['first_load_ms']} ms; regroup "
            f"{groups}; API change to screen {b['live_ms']} ms; JS heap {b['js_heap_mb']} MB."
        )
    return "\n".join(out)


def check(result: dict, budgets: dict) -> list[str]:
    """Each budget is an upper bound; returns the ones broken."""
    broken = []
    for name, limits in budgets.get("calls", {}).items():
        got = result["calls"].get(name)
        if got is None:
            broken.append(f"{name}: not measured")
            continue
        for key, limit in limits.items():
            if got[key] > limit:
                broken.append(f"{name} {key} {got[key]} > {limit}")
    for key, limit in budgets.get("fanout_ms", {}).items():
        if result["fanout_ms"][key] > limit:
            broken.append(f"fan-out {key} {result['fanout_ms'][key]} ms > {limit}")
    for key in ("events_missed", "errors"):
        if result[key] > budgets.get(key, 0):
            broken.append(f"{key} {result[key]} > {budgets.get(key, 0)}")
    if result["rss_mb"]["peak"] > budgets.get("rss_mb_peak", 1e9):
        broken.append(f"peak memory {result['rss_mb']['peak']} MB > {budgets['rss_mb_peak']}")
    return broken


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--items", type=int, default=20_000)
    ap.add_argument("--lists", type=int, default=50)
    ap.add_argument("--agents", type=int, default=10)
    ap.add_argument("--boards", type=int, default=4)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--think", type=float, default=0.25, help="agent pause between cycles")
    ap.add_argument("--json", type=Path, help="also write the result here")
    ap.add_argument("--ci", action="store_true", help="the CI size, checked against budgets")
    ap.add_argument("--browser", action="store_true", help="also time the board in Chromium")
    a = ap.parse_args()
    budgets = {}
    if a.ci:
        budgets = json.loads(BUDGETS.read_text())
        for key, value in budgets["config"].items():
            setattr(a, key, value)
    res = run(a)
    print(report(res))
    if a.json:
        a.json.write_text(json.dumps(res, indent=2) + "\n")
    if a.ci:
        broken = check(res, budgets)
        for line in broken:
            print(f"BUDGET BROKEN: {line}", file=sys.stderr)
        sys.exit(1 if broken else 0)
