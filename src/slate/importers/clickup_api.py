"""Reading ClickUp: the live API (rate-limited), or a directory of recorded responses.

Both sources answer `get(path, params) -> dict` in the ClickUp API v2 response shape, so the
importer cannot tell them apart. `fixture_name()` maps a request to a file name; `--record DIR`
writes each live response under that name, and `--fixture DIR` reads them back.

Endpoints used (developer.clickup.com/reference):
- `GET /team/{team_id}/space`, `GET /space/{id}/folder`, `GET /folder/{id}/list`,
  `GET /space/{id}/list` (folderless), `GET /list/{id}` (for statuses when a list omits them).
- `GET /team/{team_id}/task?page=N&include_closed=true&subtasks=true&list_ids[]=...` — 100 tasks
  a page, pages from 0; stop on `last_page: true` or a short page.
- `GET /task/{id}/comment` — newest first, 25 at a time; older pages via `start` + `start_id`
  of the oldest comment so far.
Rate limit: 100 requests a minute per token on Personal-tier workspaces; a 429 carries
`X-RateLimit-Reset` (unix seconds).
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import httpx

API = "https://api.clickup.com/api/v2"
PAGE_SIZE = 100
COMMENT_PAGE = 25
# Params that make two requests to the same path different; they go into the fixture name.
_NAMED_PARAMS = ("list_ids[]", "page", "start_id")


class Source(Protocol):
    def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict: ...


class ClickUpError(RuntimeError):
    pass


def fixture_name(path: str, params: Mapping[str, Any] | None = None) -> str:
    """`/team/9/task`, `{page: 0, list_ids[]: 12}` -> `team_9_task__list_ids-12__page-0.json`."""
    name = "_".join(p for p in path.strip("/").split("/") if p)
    for key in _NAMED_PARAMS:
        if params and key in params:
            name += f"__{key.strip('[]')}-{params[key]}"
    return re.sub(r"[^A-Za-z0-9_.=-]", "-", name) + ".json"


class TokenBucket:
    """`rate` requests per `per` seconds, bursting up to `rate`. `take()` blocks until allowed."""

    def __init__(
        self,
        rate: int = 100,
        per: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.capacity = float(rate)
        self.fill_rate = rate / per
        self.tokens = float(rate)
        self.clock, self.sleep = clock, sleep
        self.stamp = clock()
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            now = self.clock()
            self.tokens = min(self.capacity, self.tokens + (now - self.stamp) * self.fill_rate)
            self.stamp = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            # Wait exactly until one token has refilled, then spend it. (No re-check loop: float
            # rounding could leave 0.9999… tokens and spin forever.)
            self.sleep((1 - self.tokens) / self.fill_rate)
            self.tokens = 0.0
            self.stamp = self.clock()


class HttpSource:
    """The live API. Retries a 429 (after `X-RateLimit-Reset`) and 5xx a few times."""

    def __init__(
        self,
        token: str,
        *,
        client: httpx.Client | None = None,
        bucket: TokenBucket | None = None,
        record_dir: Path | None = None,
        retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client or httpx.Client(base_url=API, timeout=30.0)
        self.headers = {"Authorization": token}
        self.bucket = bucket or TokenBucket()
        self.record_dir = record_dir
        self.retries = retries
        self.sleep = sleep
        self.requests = 0
        if record_dir is not None:
            record_dir.mkdir(parents=True, exist_ok=True)

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict:
        for attempt in range(self.retries + 1):
            self.bucket.take()
            self.requests += 1
            r = self.client.get(path, params=dict(params or {}), headers=self.headers)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self.retries:
                    break
                reset = r.headers.get("X-RateLimit-Reset")
                wait = max(1.0, float(reset) - time.time()) if reset else 2.0**attempt
                self.sleep(min(wait, 65.0))
                continue
            if r.status_code != 200:
                raise ClickUpError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
            data = r.json()
            if self.record_dir is not None:
                (self.record_dir / fixture_name(path, params)).write_text(
                    json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8"
                )
            return data
        raise ClickUpError(
            f"GET {path} kept failing ({r.status_code}) after {self.retries} retries"
        )


class FixtureSource:
    """Recorded responses in a directory. A request with no file is an error, not an empty answer,
    so a missing recording cannot silently drop tasks."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.requests = 0
        if not directory.is_dir():
            raise ClickUpError(f"fixture directory {directory} does not exist")

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> dict:
        self.requests += 1
        name = fixture_name(path, params)
        file = self.directory / name
        if not file.exists():
            raise ClickUpError(f"no recorded response {name} in {self.directory}")
        return json.loads(file.read_text(encoding="utf-8"))


# --- The walk -------------------------------------------------------------------------------


def fetch_lists(source: Source, team_id: str) -> list[dict]:
    """Every list in the workspace as `{space, folder, list}` (folder None when folderless)."""
    out: list[dict] = []
    for space in source.get(f"/team/{team_id}/space", {"archived": "false"})["spaces"]:
        folders = source.get(f"/space/{space['id']}/folder", {"archived": "false"})["folders"]
        for folder in folders:
            lists = source.get(f"/folder/{folder['id']}/list", {"archived": "false"})["lists"]
            out += [{"space": space, "folder": folder, "list": lst} for lst in lists]
        folderless = source.get(f"/space/{space['id']}/list", {"archived": "false"})["lists"]
        out += [{"space": space, "folder": None, "list": lst} for lst in folderless]
    return out


def list_statuses(source: Source, entry: dict) -> list[dict]:
    """The list's own statuses; if the list object omits them, ask for the list, then fall back
    to the space's statuses (ClickUp inheritance)."""
    statuses = entry["list"].get("statuses")
    if not statuses:
        statuses = source.get(f"/list/{entry['list']['id']}").get("statuses")
    return list(statuses or entry["space"].get("statuses") or [])


def fetch_tasks(source: Source, team_id: str, list_id: str) -> list[dict]:
    """All tasks (open and closed, with subtasks) whose list is `list_id`, every page."""
    tasks: list[dict] = []
    page = 0
    while True:
        data = source.get(
            f"/team/{team_id}/task",
            {
                "page": page,
                "include_closed": "true",
                "subtasks": "true",
                "include_markdown_description": "true",
                "list_ids[]": list_id,
            },
        )
        batch = data.get("tasks", [])
        tasks += batch
        if data.get("last_page", len(batch) < PAGE_SIZE) or not batch:
            return tasks
        page += 1


def fetch_comments(source: Source, task_id: str) -> list[dict]:
    """Every comment on the task, oldest first."""
    comments: list[dict] = []
    params: dict[str, Any] = {}
    seen: set[str] = set()
    while True:
        batch = [c for c in source.get(f"/task/{task_id}/comment", params)["comments"]]
        fresh = [c for c in batch if c["id"] not in seen]
        comments += fresh
        seen.update(c["id"] for c in fresh)
        if len(batch) < COMMENT_PAGE or not fresh:
            return sorted(comments, key=lambda c: int(c["date"]))
        oldest = min(fresh, key=lambda c: int(c["date"]))
        params = {"start": oldest["date"], "start_id": oldest["id"]}
