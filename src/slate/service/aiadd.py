"""AI add: turn plain words into one properly filed task (and optional recurrence).

`POST /items/ai` reads the caller's access-scoped context (editable task lists and their
"What belongs here" notes, visible people, known tags, workspace calendar), asks the configured
model (Anthropic Claude or Ollama) to extract the destination list, title, description,
assignees, dates, recurrence, priority, status and tags, and creates the item once in the
right list. If the model is down, slow or over the concurrency cap, a deterministic local
parser handles dates, recurrences, tags, assignees, priorities and `~list` hints so the task
still lands cleanly in the default list instead of failing silently.

- Context is built under the caller's access before any network call: only task lists the
  caller can edit and only people they can reach (`access.participant_ids` + `guests.may_reach`)
  are offered. Guests never see workspace lists or workspace agents.
- Every model value is validated against that context and Slate's rules before writing.
  Bogus values are dropped individually; Rule 1 holds (exactly one item, never children).
- Model calls run one at a time on their own worker thread (`parse_async`), never holding a
  request thread or a database connection, capped at `MAX_QUEUED` waiting requests.
- Off unless `SLATE_AI_API_KEY` (Anthropic) or `SLATE_AI_URL` (Ollama) is configured (`enabled`);
  `GET /me` says `ai_add` so the board shows the switch only when it works.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import weakref
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import anyio
import anyio.to_thread
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from slate import dates
from slate.models import SYSTEM_HANDLE, Account, Item, List, Section, Status, utcnow
from slate.service import access, ai_settings, guests, items, recurrences
from slate.service import lists as lists_svc
from slate.service import tags as tags_svc
from slate.service.core import Actor
from slate.service.errors import Invalid
from slate.settings import settings

log = logging.getLogger(__name__)

SESSION_REF = "ai-add"
MAX_KNOWN_TAGS = 100
MAX_LISTS = 40
MAX_NOTE_CHARS = 240
# Parses waiting or running, process-wide; overflow falls back to local parsing.
MAX_QUEUED = 16
MAX_ANSWER_BYTES = 1024 * 1024
ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5-5"
DEFAULT_OLLAMA_MODEL = "gemma4:latest"
# Tests swap in an `httpx.MockTransport`; None means real network.
transport: httpx.BaseTransport | None = None

PRIORITIES = ("urgent", "high", "normal", "low")
TRIGGERS = ("schedule", "on_done")
WHEN = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2})(?::\d{2})?)?$")
EXPLICIT_5PM = re.compile(r"\b(?:17:00|5(?::00)?\s*(?:pm|p\.m\.)|at\s+5\b)", re.I)
TODAY_WORD = re.compile(r"\b(?:today|tonight|this\s+(?:morning|afternoon|evening))\b", re.I)

TASK_PROPERTIES: dict[str, Any] = {
    "title": {
        "type": "string",
        "description": (
            "Short, clear imperative task title with filler words, dates, repeats and "
            "assignees removed."
        ),
    },
    "body_md": {
        "type": "string",
        "description": (
            "Extra details, links, notes, or '- [ ] step' lines ONLY if present beyond "
            "the title; otherwise empty string ''."
        ),
    },
    "list_id": {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": (
            "Exact id of the destination list from Available lists, or null for the default list."
        ),
    },
    "assignees": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Account handles from People who own or do this task.",
    },
    "due_at": {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": (
            "Local 'YYYY-MM-DD' when no clock time was given, or 'YYYY-MM-DDTHH:MM' only "
            "when a specific time was given; null if undated."
        ),
    },
    "start_at": {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": (
            "Local 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM' when a start date/time was stated; "
            "otherwise null."
        ),
    },
    "rrule": {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": (
            "RFC 5545 RRULE without 'RRULE:' prefix (FREQ must be DAILY, WEEKLY, MONTHLY, "
            "or YEARLY) ONLY if repeating; else null."
        ),
    },
    "trigger": {
        "anyOf": [{"type": "string", "enum": list(TRIGGERS)}, {"type": "null"}],
        "description": (
            "'on_done' ONLY if repeating counted from when each instance is completed; else null."
        ),
    },
    "skip_weekends": {
        "anyOf": [{"type": "boolean"}, {"type": "null"}],
        "description": "true ONLY if weekend occurrences should move to Monday; else null.",
    },
    "priority": {
        "anyOf": [{"type": "string", "enum": list(PRIORITIES)}, {"type": "null"}],
        "description": "urgent, high, normal, or low ONLY if stated or clearly implied; else null.",
    },
    "status": {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": "Exact status name in the chosen list ONLY if explicitly stated; else null.",
    },
    "tags": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Lowercase tag names only when explicitly named or required by the chosen list's note."
        ),
    },
}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": TASK_PROPERTIES,
    "required": ["title", "body_md"],
}

ANTHROPIC_TOOL: dict[str, Any] = {
    "name": "create_task",
    "description": "Record the structured fields of the single task described by the user.",
    "input_schema": SCHEMA,
}

PROMPT = (
    "You turn what a person said into the structured fields of ONE task.\n"
    "Today is {today} ({tz}).\n"
    "Calendar ahead (a week runs Monday to Sunday; "
    '"morning"=09:00, "afternoon"=15:00, "evening"=18:00):\n'
    "{calendar}\n\n"
    'The person filing it is @{me} ("me", "I", "my", "for me", "remind me" mean @{me}).\n'
    "People you may assign (handle: name): {people}\n"
    "{lists_block}\n"
    "Tags in use: {tags}\n\n"
    "Rules (leave optional fields null/empty unless the person's words decide them):\n"
    "- title: short, clear imperative title. Strip command/dictation wrappers "
    '("hey", "please", "create a recurring task", "do a task", "remind me to", "add a task to"). '
    "Fix obvious speech-to-text phonetic slips when the intended word is clear from context "
    "(for example medication or everyday words). Do not repeat dates, recurrence phrases, or "
    "assignees that belong in fields.\n"
    '- body_md: extra context, notes, links, or "- [ ] step" lines ONLY when there are details '
    'beyond the title; otherwise "". Never repeat the title or write filler like '
    '"Recurring task every Tuesday."\n'
    "- list_id: {list_rule}\n"
    "- assignees: handles from People for who owns or does it.\n"
    '- due_at / start_at: use "YYYY-MM-DD" when no specific clock time was given; use '
    '"YYYY-MM-DDTHH:MM" ONLY when a specific time or morning/afternoon/evening was given. '
    "Never append T17:00 unless they literally said 5pm. For a repeating task, set due_at to "
    "the FIRST upcoming occurrence AFTER now (if today's slot already passed, pick the next).\n"
    '- rrule: RFC 5545 RRULE without "RRULE:" ONLY if the task repeats, else null. FREQ MUST '
    "be one of DAILY, WEEKLY, MONTHLY, or YEARLY (use INTERVAL=N for 'every 2 weeks' -> "
    "FREQ=WEEKLY;INTERVAL=2, 'every 3 months' -> FREQ=MONTHLY;INTERVAL=3; weekdays -> "
    "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR; every Tuesday -> FREQ=WEEKLY;BYDAY=TU; "
    "1st of month -> FREQ=MONTHLY;BYMONTHDAY=1).\n"
    '- trigger: "on_done" ONLY if repeating after completion ("every 3 months after I finish '
    'it"); else null.\n'
    "- skip_weekends: true ONLY if weekend occurrences should move to Monday; else null.\n"
    "- priority: urgent, high, normal, or low ONLY if stated or clearly implied; else null.\n"
    "- status: one of the chosen list's statuses ONLY if explicitly stated; else null.\n"
    "- tags: only tags explicitly named or required by the chosen list's description; prefer "
    "tags in use; else []."
)


def provider(session: Session | None = None) -> ai_settings.ProviderName | None:
    """Active AI provider from workspace settings or environment, or None when off."""
    cfg = ai_settings.resolve(session)
    return cfg.provider if ai_settings.is_active(cfg) else None


def enabled(session: Session | None = None) -> bool:
    return ai_settings.is_active(ai_settings.resolve(session))


def model_name(session: Session | None = None) -> str:
    cfg = ai_settings.resolve(session)
    if cfg.model:
        return cfg.model
    if cfg.provider:
        return ai_settings.DEFAULT_MODELS.get(cfg.provider, "")
    return DEFAULT_OLLAMA_MODEL


def first_sentence(text: str) -> str:
    """Fallback title from raw text: the first sentence, at most 80 characters."""
    flat = " ".join(text.split())
    m = re.match(r"^(.{8,}?[.!?])(\s|$)", flat)
    first = m.group(1) if m else flat
    if len(first) <= 80:
        return first
    cut = first[:80]
    space = cut.rfind(" ")
    return (cut[:space] if space > 40 else cut).rstrip() + "…"


@dataclass(frozen=True)
class ListOption:
    id: str
    section: str
    name: str
    slug: str
    note: str
    reference: bool
    statuses: tuple[str, ...]


@dataclass
class Ask:
    """Everything parsing and committing need, read under the caller's access."""

    text: str
    me: str
    default_list_id: str
    route_mode: Literal["auto", "locked"] = "locked"
    lists: list[ListOption] = field(default_factory=list)
    people: dict[str, str] = field(default_factory=dict)  # handle -> display name
    known_tags: list[str] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)
    now: datetime = field(default_factory=utcnow)
    ai_config: ai_settings.ResolvedAI | None = None

    def list_by_id(self, list_id: str | None) -> ListOption | None:
        if not list_id:
            return None
        return next((x for x in self.lists if x.id == list_id), None)

    @property
    def default_list(self) -> ListOption | None:
        return self.list_by_id(self.default_list_id)


def _flatten_note(note_md: str | None) -> str:
    if not note_md:
        return ""
    flat = " ".join(note_md.split())
    if len(flat) <= MAX_NOTE_CHARS:
        return flat
    return flat[: MAX_NOTE_CHARS - 1].rsplit(" ", 1)[0] + "…"


def _editable_lists(session: Session) -> list[ListOption]:
    caller = access.current(session)
    rows = session.execute(
        select(List, Section.name)
        .join(Section, Section.id == List.section_id)
        .where(
            List.kind == "tasks",
            List.archived_at.is_(None),
            Section.archived_at.is_(None),
        )
        .order_by(Section.position, List.position, List.name)
    ).all()
    editable = [
        (lst, sec_name)
        for lst, sec_name in rows
        if caller is None or min(caller.cap, caller.list_level(lst.id)) >= access.EDIT
    ]
    if not editable:
        return []
    list_ids = [lst.id for lst, _ in editable]
    status_rows = session.execute(
        select(Status.list_id, Status.name)
        .where(Status.list_id.in_(list_ids))
        .order_by(Status.position)
    ).all()
    by_list: dict[str, list[str]] = {}
    for lid, sname in status_rows:
        by_list.setdefault(lid, []).append(sname)
    return [
        ListOption(
            id=lst.id,
            section=sec_name,
            name=lst.name,
            slug=lst.slug,
            note=_flatten_note(lst.note_md),
            reference=bool(lst.reference),
            statuses=tuple(by_list.get(lst.id, ())),
        )
        for lst, sec_name in editable[:MAX_LISTS]
    ]


def _default_list(session: Session, available: list[ListOption], list_id: str | None) -> str:
    if list_id is not None:
        lst = lists_svc.require_tasks(lists_svc.get_list(session, list_id))
        if lst.archived_at is not None:
            raise Invalid(f"list '{lst.name}' is archived")
        caller = access.current(session)
        if caller is not None and min(caller.cap, caller.list_level(lst.id)) < access.EDIT:
            from slate.service.errors import Forbidden  # noqa: PLC0415

            raise Forbidden("you cannot file items in this list")
        return lst.id
    inbox = lists_svc.inbox_list_id(session)
    if inbox and lists_svc.can_file_inbox(session, inbox):
        return inbox
    work = next((x.id for x in available if not x.reference), None)
    if work is not None:
        return work
    if available:
        return available[0].id
    raise Invalid("list_id is required (no default inbox list is available)")


def prepare(
    session: Session,
    actor: Actor,
    list_id: str | None,
    text: str,
    defaults: dict[str, Any] | None = None,
    *,
    route: bool | None = None,
    now: datetime | None = None,
) -> Ask:
    """Gather the access-scoped context for one AI add before releasing the DB session."""
    cleaned = text.strip()
    if not cleaned:
        raise Invalid("text cannot be blank")
    available = _editable_lists(session)
    default_id = _default_list(session, available, list_id)
    if route is None:
        mode: Literal["auto", "locked"] = "auto" if list_id is None else "locked"
    else:
        mode = "auto" if route else "locked"
    # Ensure default_id is present in `offered` even if there are > MAX_LISTS lists.
    if mode == "locked":
        chosen = next((x for x in available if x.id == default_id), None)
        if chosen is None:
            lst = lists_svc.get_list(session, default_id)
            sec = session.get(Section, lst.section_id)
            st_names = tuple(
                session.scalars(
                    select(Status.name).where(Status.list_id == lst.id).order_by(Status.position)
                )
            )
            chosen = ListOption(
                id=lst.id,
                section=sec.name if sec else "",
                name=lst.name,
                slug=lst.slug,
                note=_flatten_note(lst.note_md),
                reference=bool(lst.reference),
                statuses=st_names,
            )
        offered = [chosen]
    else:
        offered = available
        if not any(x.id == default_id for x in offered):
            lst = lists_svc.get_list(session, default_id)
            sec = session.get(Section, lst.section_id)
            st_names = tuple(
                session.scalars(
                    select(Status.name).where(Status.list_id == lst.id).order_by(Status.position)
                )
            )
            offered = [
                ListOption(
                    id=lst.id,
                    section=sec.name if sec else "",
                    name=lst.name,
                    slug=lst.slug,
                    note=_flatten_note(lst.note_md),
                    reference=bool(lst.reference),
                    statuses=st_names,
                ),
                *offered,
            ]
    me = session.get(Account, actor.account_id)
    visible = access.participant_ids(session)
    q = select(Account).where(Account.disabled_at.is_(None), Account.handle != SYSTEM_HANDLE)
    if visible is not None:
        q = q.where(Account.id.in_(visible))
    people = {
        a.handle: a.display_name
        for a in session.scalars(q)
        if guests.may_reach(session, actor.account_id, a)
    }
    known_tags = [t.name for t in tags_svc.list_tags(session)][:MAX_KNOWN_TAGS]
    me_handle = me.handle if me else "me"
    return Ask(
        text=cleaned,
        me=me_handle,
        default_list_id=default_id,
        route_mode=mode,
        lists=offered,
        people=people,
        known_tags=known_tags,
        defaults=dict(defaults or {}),
        now=now or utcnow(),
        ai_config=ai_settings.resolve(session),
    )


def _calendar_block(now_local: datetime) -> str:
    today = now_local.date()
    monday_this_week = today - timedelta(days=today.weekday())
    monday_next_week = monday_this_week + timedelta(days=7)
    monday_after = monday_this_week + timedelta(days=14)
    groups: list[tuple[str, list[str]]] = [
        ("This week:", []),
        ('Next week ("next <weekday>" is in this block):', []),
        ("Week after next:", []),
    ]
    for k in range(21):
        d = today + timedelta(days=k)
        note = " (today)" if k == 0 else " (tomorrow)" if k == 1 else ""
        line = f"  {d:%a %Y-%m-%d}{note}"
        if d < monday_next_week:
            groups[0][1].append(line)
        elif d < monday_after:
            groups[1][1].append(line)
        else:
            groups[2][1].append(line)
    return "\n".join(f"{header}\n" + "\n".join(lines) for header, lines in groups if lines)


def prompt(a: Ask, now: datetime | None = None) -> str:
    tz = ZoneInfo(settings.timezone)
    local_now = (now or a.now).astimezone(tz)
    if a.route_mode == "auto" and len(a.lists) > 1:
        list_lines = []
        for lst in a.lists:
            flags = []
            if lst.id == a.default_list_id:
                flags.append("DEFAULT")
            if lst.reference:
                flags.append("reference")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            statuses = f" | statuses: {', '.join(lst.statuses)}" if lst.statuses else ""
            note = f" — {lst.note}" if lst.note else ""
            list_lines.append(f"- {lst.id}: {lst.section} / {lst.name}{flag_str}{note}{statuses}")
        lists_block = f"Default list: {a.default_list_id}\nAvailable lists:\n" + "\n".join(
            list_lines
        )
        list_rule = (
            f"exact id from Available lists matching the task and list descriptions; "
            f"never pick a specialized/automated/reference list unless the task clearly belongs "
            f"there; when in doubt, use the default list {a.default_list_id}."
        )
    else:
        only = a.default_list
        statuses = ", ".join(only.statuses) if only and only.statuses else "(any)"
        note = f"\nList note: {only.note}" if only and only.note else ""
        label = f"{only.section} / {only.name}" if only else a.default_list_id
        lists_block = (
            f"Target list: {label} (id: {a.default_list_id}){note}\nThe list's statuses: {statuses}"
        )
        list_rule = f"always {a.default_list_id} (or null)."

    return PROMPT.format(
        today=f"{local_now:%A %Y-%m-%d %H:%M}",
        tz=settings.timezone,
        calendar=_calendar_block(local_now),
        me=a.me,
        people=", ".join(f"{h}: {n}" for h, n in sorted(a.people.items())) or "(only them)",
        lists_block=lists_block,
        list_rule=list_rule,
        tags=", ".join(a.known_tags) or "(none yet)",
    )


def _read_bounded(r: httpx.Response) -> bytes:
    raw = bytearray()
    for chunk in r.iter_bytes():
        raw += chunk
        if len(raw) > MAX_ANSWER_BYTES:
            raise ValueError("model answer is too long")
    return bytes(raw)


def _extract_json(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.I).strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned) or re.search(r"(\{[\s\S]*\})", cleaned)
    if m:
        parsed = json.loads(m.group(1))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("model answer did not contain a JSON object")


def _call_anthropic(system: str, text: str, cfg: ai_settings.ResolvedAI) -> dict[str, Any]:
    url = (cfg.url or ANTHROPIC_URL).rstrip("/") + "/v1/messages"
    body = {
        "model": cfg.model or DEFAULT_ANTHROPIC_MODEL,
        "max_tokens": 1024,
        "temperature": 0,
        "system": system,
        "tools": [ANTHROPIC_TOOL],
        "tool_choice": {"type": "tool", "name": ANTHROPIC_TOOL["name"]},
        "messages": [{"role": "user", "content": text}],
    }
    headers = {
        "x-api-key": cfg.api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    with (
        httpx.Client(
            transport=transport, timeout=settings.ai_timeout, follow_redirects=False
        ) as client,
        client.stream("POST", url, headers=headers, json=body) as r,
    ):
        r.raise_for_status()
        data = json.loads(_read_bounded(r))
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            inp = block.get("input")
            if isinstance(inp, dict):
                return inp
    raise ValueError("anthropic response contained no create_task tool call")


def _call_openai_chat(
    system: str, text: str, *, base_url: str, api_key: str, model: str
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/v1"):
        endpoint += "/v1"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system + "\n\nRespond with a single JSON object only."},
            {"role": "user", "content": text},
        ],
    }
    with (
        httpx.Client(
            transport=transport, timeout=settings.ai_timeout, follow_redirects=False
        ) as client,
        client.stream("POST", endpoint + "/chat/completions", headers=headers, json=body) as r,
    ):
        r.raise_for_status()
        data = json.loads(_read_bounded(r))
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return _extract_json(str(content))


def _call_chatgpt_oauth(system: str, text: str, cfg: ai_settings.ResolvedAI) -> dict[str, Any]:
    if not cfg.oauth:
        raise ValueError("ChatGPT subscription is not connected")
    oauth = ai_settings.refresh_chatgpt_token(cfg.oauth)
    token = str(oauth.get("access_token") or "")
    if not token:
        raise ValueError("ChatGPT access token is missing")
    url = (cfg.url or ai_settings.OPENAI_URL).rstrip("/") + "/v1/responses"
    body = {
        "model": cfg.model or ai_settings.DEFAULT_MODELS["chatgpt-oauth"],
        "instructions": system + "\n\nRespond with a single JSON object only.",
        "input": text,
        "text": {"format": {"type": "json_object"}},
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with (
        httpx.Client(
            transport=transport, timeout=settings.ai_timeout, follow_redirects=False
        ) as client,
        client.stream("POST", url, headers=headers, json=body) as r,
    ):
        r.raise_for_status()
        data = json.loads(_read_bounded(r))
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return _extract_json(data["output_text"])
    for item in data.get("output") or []:
        for part in item.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                return _extract_json(part["text"])
    raise ValueError("ChatGPT response contained no JSON text")


def _call_gemini(system: str, text: str, cfg: ai_settings.ResolvedAI) -> dict[str, Any]:
    model = cfg.model or ai_settings.DEFAULT_MODELS["gemini"]
    base = (cfg.url or ai_settings.GEMINI_URL).rstrip("/")
    url = f"{base}/v1beta/models/{model}:generateContent"
    body = {
        "systemInstruction": {
            "parts": [{"text": system + "\n\nRespond with a single JSON object only."}]
        },
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    with (
        httpx.Client(
            transport=transport, timeout=settings.ai_timeout, follow_redirects=False
        ) as client,
        client.stream("POST", url, params={"key": cfg.api_key}, json=body) as r,
    ):
        r.raise_for_status()
        data = json.loads(_read_bounded(r))
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    raw_text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
    return _extract_json(raw_text)


def _call_ollama(system: str, text: str, cfg: ai_settings.ResolvedAI) -> dict[str, Any]:
    body = {
        "model": cfg.model or DEFAULT_OLLAMA_MODEL,
        "stream": False,
        "format": SCHEMA,
        "keep_alive": settings.ai_keep_alive,
        "options": {"temperature": 0, "num_ctx": 4096},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
    }
    with (
        httpx.Client(
            transport=transport, timeout=settings.ai_timeout, follow_redirects=False
        ) as client,
        client.stream("POST", cfg.url.rstrip("/") + "/api/chat", json=body) as r,
    ):
        r.raise_for_status()
        data = json.loads(_read_bounded(r))
    out = json.loads(data["message"]["content"])
    if not isinstance(out, dict):
        raise ValueError("model answered something other than an object")
    return out


def _call_cli(
    cli: Literal["claude", "codex"], system: str, text: str, cfg: ai_settings.ResolvedAI
) -> dict[str, Any]:
    sys_json = system + "\n\nRespond with a single valid JSON object only. No prose or fences."
    if cfg.url:
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        payload = {
            "cli": cli,
            "model": cfg.model,
            "system": sys_json,
            "prompt": text,
            "timeout": settings.ai_timeout,
        }
        with (
            httpx.Client(
                transport=transport, timeout=settings.ai_timeout + 5, follow_redirects=False
            ) as client,
            client.stream(
                "POST", cfg.url.rstrip("/") + "/complete", headers=headers, json=payload
            ) as r,
        ):
            r.raise_for_status()
            data = json.loads(_read_bounded(r))
        return _extract_json(str(data.get("text") or ""))

    full = f"{sys_json}\n\n---\n\n{text}"
    if cli == "claude":
        cmd = ["claude", "-p", "-"]
        if cfg.model and cfg.model != "default":
            cmd += ["--model", cfg.model]
    else:
        cmd = ["codex", "exec", "-"]
        if cfg.model and cfg.model != "default":
            cmd += ["--model", cfg.model]
    proc = subprocess.run(
        cmd,
        input=full,
        capture_output=True,
        text=True,
        timeout=settings.ai_timeout,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()[:300]
        raise RuntimeError(f"{cli} CLI exited {proc.returncode}: {detail}")
    return _extract_json(proc.stdout)


def _dispatch(system: str, text: str, cfg: ai_settings.ResolvedAI) -> dict[str, Any]:
    if not ai_settings.is_active(cfg) or cfg.provider is None:
        raise ValueError("AI add is not configured")
    if cfg.provider == "anthropic":
        return _call_anthropic(system, text, cfg)
    if cfg.provider == "openai":
        return _call_openai_chat(
            system,
            text,
            base_url=cfg.url or ai_settings.OPENAI_URL,
            api_key=cfg.api_key,
            model=cfg.model or ai_settings.DEFAULT_MODELS["openai"],
        )
    if cfg.provider == "chatgpt-oauth":
        return _call_chatgpt_oauth(system, text, cfg)
    if cfg.provider == "gemini":
        return _call_gemini(system, text, cfg)
    if cfg.provider == "ollama":
        return _call_ollama(system, text, cfg)
    if cfg.provider == "openai-compatible":
        return _call_openai_chat(
            system, text, base_url=cfg.url, api_key=cfg.api_key, model=cfg.model
        )
    if cfg.provider == "claude-cli":
        return _call_cli("claude", system, text, cfg)
    if cfg.provider == "codex-cli":
        return _call_cli("codex", system, text, cfg)
    raise ValueError(f"unsupported AI provider: {cfg.provider}")


def call_model(system: str, text: str) -> dict[str, Any]:
    """One structured call to the environment-configured AI provider (or test stand-in)."""
    return _dispatch(system, text, ai_settings.resolve(None))


def test_connection(
    session: Session,
    *,
    provider_name: str | None = None,
    model: str | None = None,
    url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Run a tiny live round-trip against the configured or draft AI provider."""
    saved = ai_settings.resolve(session)
    prov = (provider_name or saved.provider or "").strip()
    if prov not in ai_settings.PROVIDERS:
        return {
            "ok": False,
            "provider": prov or None,
            "model": "",
            "latency_ms": 0,
            "detail": "Select a provider first.",
        }
    cfg = ai_settings.ResolvedAI(
        enabled=True,
        provider=prov,  # type: ignore[arg-type]
        model=(model if model is not None else saved.model).strip()
        or ai_settings.DEFAULT_MODELS.get(prov, ""),
        url=(url if url is not None else saved.url).strip(),
        api_key=(api_key or "").strip() or (saved.api_key if prov == saved.provider else ""),
        oauth=saved.oauth if prov == "chatgpt-oauth" else None,
        source=saved.source,
        host_id=saved.host_id,
    )
    t0 = time.monotonic()
    try:
        out = _dispatch(
            'Return JSON: {"title": "Test ok", "body_md": ""}',
            "Test connection",
            cfg,
        )
        ms = int((time.monotonic() - t0) * 1000)
        ok = isinstance(out, dict) and bool(out.get("title"))
        return {
            "ok": ok,
            "provider": cfg.provider,
            "model": cfg.model,
            "latency_ms": ms,
            "detail": f"Connected in {ms} ms" if ok else "Model returned empty JSON",
        }
    except Exception as exc:  # noqa: BLE001
        ms = int((time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "provider": cfg.provider,
            "model": cfg.model,
            "latency_ms": ms,
            "detail": str(exc)[:300],
        }


# --- Deterministic fallback & RRULE salvage -------------------------------------------------

_DAY_CODES = {
    "monday": "MO",
    "mon": "MO",
    "tuesday": "TU",
    "tue": "TU",
    "tues": "TU",
    "wednesday": "WE",
    "wed": "WE",
    "thursday": "TH",
    "thu": "TH",
    "thur": "TH",
    "thurs": "TH",
    "friday": "FR",
    "fri": "FR",
    "saturday": "SA",
    "sat": "SA",
    "sunday": "SU",
    "sun": "SU",
}
_WEEKDAY_NUM = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_FILLER_RE = re.compile(
    r"^(?:(?:hey|hi|ok(?:ay)?|please|so)\b[\s,]*)*"
    r"(?:(?:can you|could you|please)\s+)?"
    r"(?:(?:create|add|make|do|set up|schedule)\s+(?:a\s+)?(?:(?:new|recurring|repeating)\s+)*"
    r"(?:task|reminder|item|card)\s*(?:for\s+me\s+)?(?:for|to)?\s*|"
    r"remind\s+me\s+(?:to\s+)?)",
    re.I,
)
_ME_PHRASE = re.compile(r"\b(?:for\s+me|remind\s+me|i\s+need\s+to|my\s+self|myself)\b", re.I)
_PRIO_MAP = {
    "urgent": "urgent",
    "high": "high",
    "normal": "normal",
    "low": "low",
    "1": "urgent",
    "2": "high",
    "3": "normal",
    "4": "low",
}


_UNIT_FREQ = {"day": "DAILY", "week": "WEEKLY", "month": "MONTHLY", "year": "YEARLY"}


def _extract_rrule(text: str) -> tuple[str | None, str | None, str]:
    """Extract a simple RFC 5545 RRULE and optional trigger from plain English.

    Returns (rrule, trigger, remaining_text).
    """
    low = text
    trigger: str | None = None
    m_done = re.search(
        r"\b(?:after\s+(?:i\s+)?(?:finish|complete|do)\s+it|from\s+completion)\b", low, re.I
    )
    if m_done:
        trigger = "on_done"
        low = (low[: m_done.start()] + " " + low[m_done.end() :]).strip()

    def _unit_rule(m: re.Match[str]) -> str:
        freq = _UNIT_FREQ[m.group(3).lower()]
        iv = 2 if m.group(1) else (int(m.group(2)) if m.group(2) else 1)
        return f"FREQ={freq}" + (f";INTERVAL={iv}" if iv > 1 else "")

    def _word_rule(m: re.Match[str]) -> str:
        w = m.group(1).lower()
        return f"FREQ={'YEARLY' if w == 'annually' else w.upper()}"

    patterns: list[tuple[re.Pattern[str], Any]] = [
        (
            re.compile(r"\bevery\s+weekday(?:s)?\b", re.I),
            lambda m: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        ),
        (
            re.compile(r"\bevery\s+(?:(other)|(\d{1,2}))?\s*(day|week|month|year)s?\b", re.I),
            _unit_rule,
        ),
        (
            re.compile(
                r"\bevery\s+(?:(other)|(\d{1,2}))?\s*"
                r"((?:(?:mon|tue(?:s)?|wed|thu(?:r|rs)?|fri|sat|sun)(?:day)?s?)"
                r"(?:\s*(?:,|and|&)\s*"
                r"(?:mon|tue(?:s)?|wed|thu(?:r|rs)?|fri|sat|sun)(?:day)?s?)*)\b",
                re.I,
            ),
            lambda m: _weekday_rrule(m.group(1), m.group(2), m.group(3)),
        ),
        (
            re.compile(
                r"\b(?:on\s+the\s+)?(\d{1,2})(?:st|nd|rd|th)\s+of\s+every\s+month\b|"
                r"\bevery\s+(\d{1,2})(?:st|nd|rd|th)(?:\s+of\s+the\s+month)?\b",
                re.I,
            ),
            lambda m: _monthday_rrule(int(m.group(1) or m.group(2))),
        ),
        (re.compile(r"\b(daily|weekly|monthly|yearly|annually)\b", re.I), _word_rule),
    ]
    for pat, build in patterns:
        m = pat.search(low)
        if m:
            rule = build(m)
            if rule:
                rest = (low[: m.start()] + " " + low[m.end() :]).strip()
                return rule, trigger, rest
    return None, None, text


def _weekday_rrule(other: str | None, count: str | None, raw_days: str) -> str | None:
    codes: list[str] = []
    for tok in re.split(r"[\s,&]+|\band\b", raw_days, flags=re.I):
        key = tok.strip().lower().rstrip("s")
        code = _DAY_CODES.get(key)
        if code and code not in codes:
            codes.append(code)
    if not codes:
        return None
    interval = 2 if other else (int(count) if count and int(count) > 1 else 1)
    iv = f";INTERVAL={interval}" if interval > 1 else ""
    return f"FREQ=WEEKLY{iv};BYDAY={','.join(codes)}"


def _monthday_rrule(day: int) -> str | None:
    if not 1 <= day <= 31:
        return None
    return f"FREQ=MONTHLY;BYMONTHDAY={day}"


def _valid_rrule(rule_text: str, now: datetime) -> str | None:
    candidate = rule_text.strip()
    if candidate.upper().startswith("RRULE:"):
        candidate = candidate[6:].strip()
    if not candidate:
        return None
    try:
        return recurrences.preview(candidate, now=now)["rrule"]
    except Invalid:
        return None


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _match_list_hint(hint: str, offered: list[ListOption]) -> str | None:
    h = _squash(hint)
    if not h:
        return None
    for pred in (
        lambda x: _squash(x.name) == h or _squash(x.slug) == h,
        lambda x: _squash(x.name).startswith(h) or _squash(x.slug).startswith(h),
        lambda x: h in _squash(x.name),
    ):
        found = next((x.id for x in offered if pred(x)), None)
        if found:
            return found
    return None


def _extract_time(text: str) -> tuple[str | None, str]:
    m = re.search(
        r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\bat\s+(\d{1,2}):(\d{2})\b",
        text,
        re.I,
    )
    if not m:
        return None, text
    if m.group(1):
        h = int(m.group(1))
        minute = int(m.group(2) or 0)
        pm = m.group(3).lower() == "pm"
        if not (1 <= h <= 12 and 0 <= minute <= 59):
            return None, text
        if h == 12:
            h = 12 if pm else 0
        elif pm:
            h += 12
    else:
        h, minute = int(m.group(4)), int(m.group(5))
        if not (0 <= h <= 23 and 0 <= minute <= 59):
            return None, text
    rest = (text[: m.start()] + " " + text[m.end() :]).strip()
    return f"{h:02d}:{minute:02d}", rest


def _extract_date(text: str, today: date) -> tuple[str | None, str]:
    # ISO date
    m = re.search(r"\b(?:due\s+|by\s+|on\s+)?(\d{4}-\d{2}-\d{2})\b", text, re.I)
    if m:
        try:
            date.fromisoformat(m.group(1))
            return m.group(1), (text[: m.start()] + " " + text[m.end() :]).strip()
        except ValueError:
            pass
    # today / tomorrow / tonight
    m = re.search(r"\b(?:due\s+|by\s+|on\s+)?(today|tonight|tomorrow|tmrw)\b", text, re.I)
    if m:
        w = m.group(1).lower()
        d = today + timedelta(days=1) if w in ("tomorrow", "tmrw") else today
        return d.isoformat(), (text[: m.start()] + " " + text[m.end() :]).strip()
    # next <weekday> or [by/on/due] <weekday>
    m = re.search(
        r"\b(?:(next|this)\s+|(?:due|by|on)\s+)?"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        text,
        re.I,
    )
    if m:
        qual = (m.group(1) or "").lower()
        target = _WEEKDAY_NUM[_DAY_CODES[m.group(2).lower()]]
        if qual == "next":
            monday_next = today - timedelta(days=today.weekday()) + timedelta(days=7)
            d = monday_next + timedelta(days=target)
        else:
            delta = (target - today.weekday()) % 7
            d = today + timedelta(days=delta)
        return d.isoformat(), (text[: m.start()] + " " + text[m.end() :]).strip()
    return None, text


def fallback_parse(a: Ask) -> dict[str, Any]:
    """Deterministic local parser used when the AI provider is down, slow, or full."""
    tz = ZoneInfo(settings.timezone)
    today = a.now.astimezone(tz).date()
    work = a.text.strip()
    out: dict[str, Any] = {}

    # Tokens: #tag, @handle, ~list, !priority
    tags: list[str] = []
    assignees: list[str] = []
    tokens_out: list[str] = []
    for word in work.split():
        low = word.lower().rstrip(".,;:")
        if len(word) > 1 and word.startswith("#"):
            t = _tag(a, low[1:])
            if t and t not in tags:
                tags.append(t)
            continue
        if len(word) > 1 and word.startswith("@"):
            h = low[1:]
            if (h in a.people or h == a.me) and h not in assignees:
                assignees.append(h)
            continue
        if len(word) > 1 and word.startswith("~") and a.route_mode == "auto":
            matched = _match_list_hint(word[1:], a.lists)
            if matched:
                out["list_id"] = matched
                continue
        if word.startswith("!") and low[1:] in _PRIO_MAP:
            out["priority"] = _PRIO_MAP[low[1:]]
            continue
        tokens_out.append(word)
    work = " ".join(tokens_out)

    if not assignees and _ME_PHRASE.search(a.text):
        assignees.append(a.me)
    if "priority" not in out and re.search(r"\burgent\b", work, re.I):
        out["priority"] = "urgent"

    rrule, trigger, work = _extract_rrule(work)
    if rrule:
        valid = _valid_rrule(rrule, a.now)
        if valid:
            out["rrule"] = valid
            if trigger:
                out["trigger"] = trigger

    hm, work = _extract_time(work)
    ymd, work = _extract_date(work, today)
    if ymd:
        out["due_at"] = f"{ymd}T{hm}" if hm else ymd
    elif hm and not rrule:
        out["due_at"] = f"{today.isoformat()}T{hm}"

    # Strip leading dictation filler and dangling prepositions.
    title_src = _FILLER_RE.sub("", work).strip()
    title_src = re.sub(r"\b(?:for\s+me|to\s+me)\b", "", title_src, flags=re.I)
    title_src = re.sub(r"^[\s,.:;-]+|[\s,.:;-]+$", "", title_src)
    title_src = re.sub(r"^(?:for|to|on)\s+", "", title_src, flags=re.I)
    title_src = " ".join(title_src.split())
    if title_src:
        title = title_src[0].upper() + title_src[1:] if title_src[0].islower() else title_src
        out["title"] = title[:500]
    else:
        out["title"] = first_sentence(a.text)
    out["body_md"] = a.text if len(a.text) > 120 or "\n" in a.text else ""
    if assignees:
        out["assignees"] = assignees[:10]
    if tags:
        out["tags"] = tags[:10]
    if "rrule" in out and "due_at" in out:
        _drop_past_recurring_due(out, a)
    return out


def _normalize_when(raw_when: str, source_text: str) -> str | None:
    m = WHEN.match(raw_when.strip())
    if not m:
        return None
    ymd, hm = m.group(1), m.group(2)
    try:
        date.fromisoformat(ymd)
    except ValueError:
        return None
    if hm:
        hh, mm = int(hm[:2]), int(hm[3:])
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        # Models often append T17:00 because date-only defaults to 5pm; keep it date-only
        # unless the user actually asked for 5pm.
        if hm == "17:00" and not EXPLICIT_5PM.search(source_text):
            return ymd
        return f"{ymd}T{hm}"
    return ymd


def _drop_past_recurring_due(out: dict[str, Any], a: Ask) -> None:
    """If a recurring task's first due_at is already in the past (e.g. 'every Tuesday' typed
    Tuesday at 10pm) and the person didn't ask for today, drop due_at so adopt_instance picks
    the next upcoming occurrence."""
    due = out.get("due_at")
    if not due or TODAY_WORD.search(a.text):
        return
    try:
        due_dt = dates.parse_when(due)
    except ValueError:
        out.pop("due_at", None)
        return
    if due_dt < a.now:
        out.pop("due_at", None)


def clean(a: Ask, raw: dict[str, Any]) -> dict[str, Any]:
    """Validate every model field against the caller's context; never guess."""
    out: dict[str, Any] = {}

    # Destination list
    target_list_id = a.default_list_id
    if a.route_mode == "auto":
        picked = raw.get("list_id")
        if isinstance(picked, str) and a.list_by_id(picked.strip()) is not None:
            target_list_id = picked.strip()
        out["list_id"] = target_list_id
    target_list = a.list_by_id(target_list_id)

    title = raw.get("title")
    if isinstance(title, str) and title.strip():
        out["title"] = title.strip()[:500]
    body = raw.get("body_md")
    if isinstance(body, str):
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
        if isinstance(v, str) and v.strip():
            norm = _normalize_when(v, a.text)
            if norm:
                out[key] = norm

    # Validate RRULE with service/recurrences; salvage obvious repeats if the model missed one.
    raw_rrule = raw.get("rrule")
    valid_rrule = _valid_rrule(raw_rrule, a.now) if isinstance(raw_rrule, str) else None
    salvaged_trigger: str | None = None
    if valid_rrule is None:
        extracted, salvaged_trigger, _ = _extract_rrule(a.text)
        if extracted:
            valid_rrule = _valid_rrule(extracted, a.now)
    if valid_rrule:
        out["rrule"] = valid_rrule
        trig = raw.get("trigger")
        if isinstance(trig, str) and trig.lower() in TRIGGERS:
            out["trigger"] = trig.lower()
        elif salvaged_trigger:
            out["trigger"] = salvaged_trigger
        sw = raw.get("skip_weekends")
        if isinstance(sw, bool) and sw:
            out["skip_weekends"] = True
        _drop_past_recurring_due(out, a)

    p = raw.get("priority")
    if isinstance(p, str) and p.lower() in PRIORITIES:
        out["priority"] = p.lower()

    s = raw.get("status")
    if isinstance(s, str) and s.strip() and target_list is not None:
        match = next((x for x in target_list.statuses if x.lower() == s.strip().lower()), None)
        if match:
            out["status"] = match

    tags = raw.get("tags")
    if isinstance(tags, list):
        names = [n for n in (_tag(a, t) for t in tags) if n]
        if names:
            out["tags"] = list(dict.fromkeys(names))[:10]
    return out


def _tag(a: Ask, raw: Any) -> str | None:
    """A tag the model may set: well-formed, and either already in use or named in the words."""
    try:
        name = tags_svc.normalize(str(raw).strip().lstrip("#"))
    except Invalid:
        return None
    if name in a.known_tags:
        return name
    words = " ".join(a.text.lower().split())
    return name if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", words) else None


# --- Bounded worker execution (never holds request thread pool) -----------------------------

_queued = 0
_queued_lock = threading.Lock()
_workers: weakref.WeakKeyDictionary[Any, anyio.CapacityLimiter] = weakref.WeakKeyDictionary()


def queued() -> int:
    """Model parses waiting or running now."""
    return _queued


def _worker() -> anyio.CapacityLimiter:
    import asyncio  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    if loop not in _workers:
        _workers[loop] = anyio.CapacityLimiter(1)
    return _workers[loop]


_ORIG_CALL_MODEL = call_model


def _run_model(a: Ask) -> tuple[dict[str, Any], bool]:
    try:
        sys_prompt = prompt(a)
        if call_model is not _ORIG_CALL_MODEL or a.ai_config is None:
            raw = call_model(sys_prompt, a.text)
        else:
            raw = _dispatch(sys_prompt, a.text, a.ai_config)
        parsed = clean(a, raw)
        if parsed.get("title"):
            return parsed, False
        log.warning("ai add: model returned no title, falling back to local parser")
    except Exception:  # noqa: BLE001
        log.warning("ai add: model parse failed, falling back to local parser", exc_info=True)
    return fallback_parse(a), True


async def parse_async(a: Ask) -> tuple[dict[str, Any], bool]:
    """Run one model parse on its dedicated worker thread, never holding request threads.

    Returns (cleaned_fields, used_fallback). Beyond `MAX_QUEUED` waiting parses, falls back
    immediately to deterministic local parsing so the user's task is never dropped.
    """
    global _queued
    with _queued_lock:
        if _queued >= MAX_QUEUED:
            log.warning("ai add: %d parses waiting, using local fallback", _queued)
            return fallback_parse(a), True
        _queued += 1
    try:
        return await anyio.to_thread.run_sync(_run_model, a, limiter=_worker())
    finally:
        with _queued_lock:
            _queued -= 1


def commit(session: Session, actor: Actor, a: Ask, parsed: dict[str, Any]) -> Item:
    """Create the single item in its resolved list (and adopt a recurrence rule if extracted)."""
    target_list_id = parsed.get("list_id") or a.default_list_id
    routed_elsewhere = target_list_id != a.default_list_id

    merged: dict[str, Any] = dict(a.defaults)
    if routed_elsewhere:
        # A column's status belonged to the starting list, not the routed destination.
        merged.pop("status", None)

    default_tags = list(merged.get("tags") or [])
    for key in ("title", "body_md", "assignees", "due_at", "start_at", "priority", "status"):
        if key in parsed:
            merged[key] = parsed[key]
    if not merged.get("title"):
        merged["title"] = first_sentence(a.text)
    if "body_md" not in merged:
        merged["body_md"] = ""

    merged_tags = list(dict.fromkeys([*default_tags, *(parsed.get("tags") or [])]))
    if merged_tags:
        merged["tags"] = merged_tags

    item = items.create_item(session, actor, list_id=target_list_id, **merged)

    rrule = parsed.get("rrule")
    if rrule:
        tz = ZoneInfo(settings.timezone)
        dtstart: str | None = None
        if item.due_at is not None:
            local_due = item.due_at.astimezone(tz)
            dtstart = local_due.date().isoformat() if item.due_date_only else local_due.isoformat()
        view = items.views(session, [item])[0]
        rule_fields: dict[str, Any] = {
            "list_id": item.list_id,
            "title_template": item.title,
            "rrule": rrule,
            "dtstart": dtstart,
            "adopt_item_id": item.id,
            "now": a.now,
        }
        if item.body_md:
            rule_fields["body_template_md"] = item.body_md
        if view["priority"]:
            rule_fields["priority"] = view["priority"]
        if view["tags"]:
            rule_fields["tags"] = list(view["tags"])
        if view["assignees"]:
            rule_fields["assignees"] = [x["id"] for x in view["assignees"]]
        if parsed.get("trigger"):
            rule_fields["trigger"] = parsed["trigger"]
        if parsed.get("skip_weekends"):
            rule_fields["skip_weekends"] = True
        try:
            recurrences.create_recurrence(session, actor, **rule_fields)
            session.refresh(item)
        except Exception:  # noqa: BLE001 (keep the created item if rule creation fails)
            log.warning(
                "ai add: created %s but could not attach recurrence %r",
                item.id,
                rrule,
                exc_info=True,
            )
            session.rollback()
            item = items.get_item(session, item.id)

    return item
