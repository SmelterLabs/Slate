"""AI add: describe a task in plain words and the configured Ollama model fills in its fields.

`POST /items/ai` files exactly one item at once (AGENTS.md rule 1) with the words as typed:
the title is their first sentence, the description all of it, plus whatever the column it was
typed into implies. The caller gets that item back straight away; then, after the response,
`fill` asks the configured model (`SLATE_AI_URL`, an Ollama server, `SLATE_AI_MODEL`) for the
title, description, assignees, dates, priority, status and tags their words decide, and writes
them as Slate's own account (`slate`, source `system`, session_ref `ai-add`), so the card's
history says the model set them, not the person. Every board sees it fill in through the stream.

- Context is built in the request, under the caller's access: the people offered are the ones
  the caller can see (`access.participant_ids`), the statuses are the list's, the calendar is
  the next three weeks in the workspace timezone. The model sees nothing else.
- Only fields still as filed are written: anything the person changed in the meantime wins.
- Assignees outside the offered people, a status the list lacks, a bad date: dropped, not
  guessed. A model that is down or slow leaves the card as typed; nothing is retried.
- Fills run one at a time on their own worker thread (`fill_later`), never in the thread pool
  that serves requests, so a slow model delays only other fills. At most `MAX_QUEUED` wait;
  beyond that a card simply stays as typed. The model's answer is read up to `MAX_ANSWER_BYTES`.
- Off unless `SLATE_AI_URL` is set (`enabled`); `GET /me` says `ai_add` so the board shows the
  switch only when it works.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import anyio
import anyio.to_thread
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Account, Item, Status
from slate.service import access, guests, items
from slate.service.core import Actor, Source
from slate.settings import settings

log = logging.getLogger(__name__)

SESSION_REF = "ai-add"
# Fills waiting or running, process-wide; more are dropped (the card stays as typed).
MAX_QUEUED = 16
MAX_ANSWER_BYTES = 1024 * 1024
# Tests swap in an `httpx.MockTransport`; None means real network.
transport: httpx.BaseTransport | None = None
PRIORITIES = ("urgent", "high", "normal", "low")
WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body_md": {"type": "string"},
        "assignees": {"type": "array", "items": {"type": "string"}},
        "due_at": {"type": "string"},
        "start_at": {"type": "string"},
        "priority": {"type": "string", "enum": list(PRIORITIES)},
        "status": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "body_md"],
}
PROMPT = """You turn what a person said into the fields of ONE task. Answer with JSON only.
Today is {today} ({tz}). The days ahead, for working out dates (a week runs Monday to Sunday;
"next <weekday>" is that day in the coming week; "end of the week" is its Friday, "end of next
week" the following Friday; "morning" is 09:00, "afternoon" 15:00, "evening" 18:00):
{calendar}
The person filing it is @{me} ("me", "I", "my" mean them).
People you may assign (handle: name): {people}
The list's statuses: {statuses}
Fields (leave a field out unless their words decide it):
- title: a short, clear imperative title; no dates or names that are already fields
- body_md: every detail and step they gave, as tidy Markdown; steps as "- [ ] step" lines
- assignees: handles from the people list only, for who owns or does it
- due_at / start_at: "YYYY-MM-DD" (means 5pm) or "YYYY-MM-DDTHH:MM", local time
- priority: urgent, high, normal or low, only if they said how important or urgent it is
- status: one of the statuses, only if they said
- tags: only if they named a tag"""


def enabled() -> bool:
    return bool(settings.ai_url)


def first_sentence(text: str) -> str:
    """The card's title until the model answers: the first sentence, at most 80 characters."""
    flat = " ".join(text.split())
    m = re.match(r"^(.{8,}?[.!?])(\s|$)", flat)
    first = m.group(1) if m else flat
    if len(first) <= 80:
        return first
    cut = first[:80]
    space = cut.rfind(" ")
    return (cut[:space] if space > 40 else cut).rstrip() + "…"


@dataclass
class Ask:
    """Everything the fill needs, taken in the request under the caller's access."""

    item_id: str
    text: str
    me: str
    people: dict[str, str]  # handle -> display name
    statuses: list[str]
    filed: dict[str, Any] = field(default_factory=dict)  # field -> value as filed


def file(session: Session, actor: Actor, list_id: str, text: str, extra: dict[str, Any]) -> Item:
    """Create the one item and return it (the fill comes later, via `ask` + `fill`)."""
    body = text.strip()
    return items.create_item(
        session, actor, list_id=list_id, **{**extra, "title": first_sentence(body), "body_md": body}
    )


def ask(session: Session, actor: Actor, item: Item, text: str) -> Ask:
    """The fill's context, read now under the caller's access (people they can see)."""
    me = session.get(Account, actor.account_id)
    visible = access.participant_ids(session)
    q = select(Account).where(Account.disabled_at.is_(None), Account.handle != "slate")
    if visible is not None:
        q = q.where(Account.id.in_(visible))
    # The fill writes as Slate's own account, so the caller's own limits are applied here: a
    # guest is never offered a workspace agent (service/guests.py).
    people = {
        a.handle: a.display_name
        for a in session.scalars(q)
        if guests.may_reach(session, actor.account_id, a)
    }
    statuses = list(
        session.scalars(
            select(Status.name).where(Status.list_id == item.list_id).order_by(Status.position)
        )
    )
    view = items.views(session, [item])[0]
    filed = {
        "title": view["title"],
        "body_md": view["body_md"],
        "assignees": sorted(a["handle"] for a in view["assignees"]),
        "due_at": view["due_at"],
        "start_at": view["start_at"],
        "priority": view["priority"],
        "status": (view.get("status") or {}).get("name"),
        "tags": sorted(view["tags"]),
    }
    return Ask(item.id, text.strip(), me.handle if me else "me", people, statuses, filed)


def prompt(a: Ask, now: datetime | None = None) -> str:
    tz = ZoneInfo(settings.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    days = []
    for k in range(21):
        d = now.date() + timedelta(days=k)
        note = " (today)" if k == 0 else " (tomorrow)" if k == 1 else ""
        days.append(f"{d:%a %Y-%m-%d}{note}")
    return PROMPT.format(
        today=f"{now:%A %Y-%m-%d %H:%M}",
        tz=settings.timezone,
        calendar="\n".join(days),
        me=a.me,
        people=", ".join(f"{h}: {n}" for h, n in sorted(a.people.items())) or "(only them)",
        statuses=", ".join(a.statuses) or "(any)",
    )


def call_model(system: str, text: str) -> dict[str, Any]:
    """One structured chat call to the Ollama server. Raises on any failure, including an
    answer longer than `MAX_ANSWER_BYTES`."""
    body = {
        "model": settings.ai_model,
        "stream": False,
        "format": SCHEMA,
        "keep_alive": "15m",
        "options": {"temperature": 0, "num_ctx": 4096},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
    }
    with (
        httpx.Client(
            transport=transport, timeout=settings.ai_timeout, follow_redirects=False
        ) as client,
        client.stream("POST", settings.ai_url.rstrip("/") + "/api/chat", json=body) as r,
    ):
        r.raise_for_status()
        raw = bytearray()
        for chunk in r.iter_bytes():
            raw += chunk
            if len(raw) > MAX_ANSWER_BYTES:
                raise ValueError("model answer is too long")
    out = json.loads(json.loads(bytes(raw))["message"]["content"])
    if not isinstance(out, dict):
        raise ValueError("model answered something other than an object")
    return out


def clean(a: Ask, raw: dict[str, Any]) -> dict[str, Any]:
    """Only well-formed values the context allows; never a guess."""
    out: dict[str, Any] = {}
    title = raw.get("title")
    if isinstance(title, str) and title.strip():
        out["title"] = title.strip()[:500]
    body = raw.get("body_md")
    if isinstance(body, str) and body.strip():
        out["body_md"] = body.strip()[:20000]
    who = raw.get("assignees")
    if isinstance(who, list):
        handles = []
        for h in who:
            h = str(h).strip().lstrip("@").lower()
            if (h in a.people or h == a.me) and h not in handles:
                handles.append(h)
        if handles:
            out["assignees"] = handles[:10]
    for key in ("due_at", "start_at"):
        v = raw.get(key)
        if isinstance(v, str) and WHEN.match(v.strip()):
            out[key] = v.strip()
    p = raw.get("priority")
    if isinstance(p, str) and p.lower() in PRIORITIES:
        out["priority"] = p.lower()
    s = raw.get("status")
    if isinstance(s, str):
        match = next((x for x in a.statuses if x.lower() == s.strip().lower()), None)
        if match:
            out["status"] = match
    tags = raw.get("tags")
    if isinstance(tags, list):
        names = [str(t).strip().lstrip("#") for t in tags if str(t).strip().lstrip("#")]
        if names:
            out["tags"] = names[:10]
    return out


def _still_as_filed(view: dict[str, Any], a: Ask) -> set[str]:
    now = {
        "title": view["title"],
        "body_md": view["body_md"],
        "assignees": sorted(x["handle"] for x in view["assignees"]),
        "due_at": view["due_at"],
        "start_at": view["start_at"],
        "priority": view["priority"],
        "status": (view.get("status") or {}).get("name"),
        "tags": sorted(view["tags"]),
    }
    return {k for k, v in a.filed.items() if now.get(k) == v}


_queued = 0
_queued_lock = threading.Lock()
# One fill at a time per event loop (the server has one; tests start several).
_workers: weakref.WeakKeyDictionary[Any, anyio.CapacityLimiter] = weakref.WeakKeyDictionary()


def queued() -> int:
    """Fills waiting or running now."""
    return _queued


def _worker() -> anyio.CapacityLimiter:
    import asyncio  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    if loop not in _workers:
        _workers[loop] = anyio.CapacityLimiter(1)
    return _workers[loop]


async def fill_later(a: Ask) -> None:
    """Run `fill` after the response, one at a time, on a thread of its own: waiting for the
    model never holds one of the threads that serve requests. Beyond `MAX_QUEUED` waiting
    fills the card stays as typed."""
    global _queued
    with _queued_lock:
        if _queued >= MAX_QUEUED:
            log.warning("ai add: %d fills waiting, %s stays as typed", _queued, a.item_id)
            return
        _queued += 1
    try:
        await anyio.to_thread.run_sync(fill, a, limiter=_worker())
    finally:
        with _queued_lock:
            _queued -= 1


def fill(a: Ask) -> dict[str, Any]:
    """Ask the model and write what it decided (runs after the response). Returns what was
    written, for tests; any failure leaves the card as typed."""
    from slate import db  # noqa: PLC0415 (tests repoint db.SessionLocal)
    from slate.service.due import system_actor  # noqa: PLC0415

    try:
        changes = clean(a, call_model(prompt(a), a.text))
    except Exception:  # noqa: BLE001 (model down, slow or nonsense: the card stays as typed)
        log.warning("ai add: no fill for %s", a.item_id, exc_info=True)
        return {}
    if not changes:
        return {}
    with db.SessionLocal() as session:
        try:
            item = items.get_item(session, a.item_id)
        except Exception:  # noqa: BLE001 (deleted or trashed meanwhile)
            return {}
        if item.archived_at is not None:
            return {}
        keep = _still_as_filed(items.views(session, [item])[0], a)
        changes = {k: v for k, v in changes.items() if k in keep}
        if "tags" in changes:  # a tag column's tag stays; the model's are added to it
            changes["tags"] = list(dict.fromkeys([*a.filed["tags"], *changes["tags"]]))
        if not changes:
            return {}
        sys = system_actor(session)
        actor = Actor(account_id=sys.account_id, source=Source.system, session_ref=SESSION_REF)
        try:
            items.update_item(session, actor, a.item_id, changes)
        except Exception:  # noqa: BLE001 (one bad value: write the rest, one at a time)
            session.rollback()
            done = {}
            for k, v in changes.items():
                try:
                    items.update_item(session, actor, a.item_id, {k: v})
                    done[k] = v
                except Exception:  # noqa: BLE001
                    session.rollback()
            return done
    return changes
