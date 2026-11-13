"""Chat: one channel per list and per section, plus `general`, with threads and reactions.

A channel is named by a key, not a row: `general`, `list:<list id>`, `section:<section id>` or
`agent:<agent account id>` (`parse_channel`). A list's channel lives and is archived with the
list. Messages (`messages`) have time-ordered ids, so the id is both the order and the read
cursor.

An agent's chat (`agent:<id>`) is a conversation with that agent that is not about a card: a
question, or something to do in Slate. Members and admins read every agent's chat; a guest (or
a list-limited token) reads the chats of the agents shared into a section or list it can see,
at its level there, so a project's guests see what the project's agent says
(service/access.py `Access.agents`). It lives with the agent (archived while it is
disabled). For workspace agents (non-guest role), `post` adds the channel agent's handle to
`mentions`, so its inbox, check-in and runner hear it like an `@mention` without typing the
handle. Guest-role project agents require an explicit `@handle`: human conversation, mentions
to others and plain follow-ups never implicitly address them, including in threads. Edits
wake only newly added explicit mentions (service/chatwake.py).

- `post` writes `message.posted`; `after` is the message plus `mentions` (handles of accounts
  `@mentioned`) and, for a reply, `thread_followers` (account ids of the thread's earlier
  authors). The inbox, check-in, push and webhooks read those two to route a mention or a reply
  (service/inbox.py `reason_for`, service/checkin.py `why`). `@channel` is not a thing.
- `edit` (author only) writes `message.edited` with `after.mentions` = only the handles the
  edit added; `delete` (author only) blanks the body (`message.deleted`).
- `react` / `unreact`: one emoji per account per message (`message.reacted|unreacted`).
- `make_task` files an item from a message (in the channel's list unless told otherwise) and
  links the two (`message.task_created`). Filing one item never creates children (rule 1).
- `mark_read` moves the caller's read cursor on a channel (`channel.read`, subject = the
  account), so every board the account has open updates its unread counts live. Exact
  exposures (`message_ids`) sit above the cursor in `channel_reads.exposed_ids`; `_compact`
  keeps that list small: the cursor follows the exposures whenever nothing unread is left
  below, and at most `EXPOSED_KEEP` ids are kept beyond it.

Task links: a body that names an item, as a board link (`...?task=<id>`) or `#<id>`, carries
that item in `linked_items`, which the board renders as a card.

Files: a message carries the files sent with it (`files`, service/chat_files.py): uploaded to
the channel first, then named in `post(file_ids=...)`. A message may be files alone.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.ids import new_event_id
from slate.models import (
    Account,
    AccountKind,
    ChannelRead,
    ChatWake,
    Item,
    List,
    Message,
    MessageFile,
    MessageReaction,
    Role,
    Section,
    Status,
    StatusKind,
    utcnow,
)
from slate.service import access, attachments, chat_files, guests
from slate.service import items as items_svc
from slate.service.comments import mentions_in
from slate.service.core import Actor, apply_change, snapshot
from slate.service.errors import Forbidden, Invalid, NotFound

GENERAL = "general"
AGENT = "agent"
SYSTEM_HANDLE = "slate"  # Slate's own account (service/due.py): not an agent anyone talks to
MAX_BODY = 20_000
MAX_EMOJI = 32
PAGE = 50
MAX_PAGE = 200
# Exact exposures kept beyond the read cursor. Past this, the oldest become the cursor: a
# message skipped while this many later ones were read counts as read.
EXPOSED_KEEP = 500
ITEM_REF_RE = re.compile(r"(?:[?&]task=|(?<![\w/#&])#)([0-9A-Za-z]{12})(?![0-9A-Za-z])")


@dataclass(frozen=True)
class Channel:
    key: str
    kind: str  # general | list | section | agent
    name: str
    list_id: str | None = None
    section_id: str | None = None
    archived: bool = False
    account_id: str | None = None  # the agent, for an agent's chat
    mention_required: bool = True


def place(key: str | None, name: str | None) -> str:
    """How a notification names a channel: `#Todo`, or `chat with My Agent` for an agent's chat."""
    if (key or "").startswith(f"{AGENT}:"):
        return f"chat with {name or 'an agent'}"
    return f"#{name or 'chat'}"


def parse_channel(session: Session, key: str) -> Channel:
    """The channel named by `key`. Unknown lists and sections, and channels the caller
    cannot read (service/access.py), are a NotFound."""
    key = (key or "").strip()
    gate = access.current(session)
    if gate is not None and gate.lists is not None and not gate.channel_level(key):
        raise NotFound(f"channel {key} not found")
    if key == GENERAL:
        return Channel(GENERAL, GENERAL, "General")
    kind, _, target = key.partition(":")
    if kind == "list" and target:
        lst = session.get(List, target)
        if lst is None:
            raise NotFound(f"list {target} not found")
        section = session.get(Section, lst.section_id)
        archived = lst.archived_at is not None or bool(section and section.archived_at)
        return Channel(key, "list", lst.name, lst.id, lst.section_id, archived)
    if kind == "section" and target:
        sec = session.get(Section, target)
        if sec is None:
            raise NotFound(f"section {target} not found")
        return Channel(key, "section", sec.name, None, sec.id, sec.archived_at is not None)
    if kind == AGENT and target:
        agent = session.get(Account, target)
        if agent is None or agent.kind != AccountKind.agent or agent.handle == SYSTEM_HANDLE:
            raise NotFound(f"agent {target} not found")
        archived = agent.disabled_at is not None
        return Channel(
            key,
            AGENT,
            agent.display_name,
            archived=archived,
            account_id=target,
            mention_required=agent.role == Role.guest,
        )
    raise Invalid(
        "channel is `general`, `list:<list id>`, `section:<section id>` or `agent:<agent id>`"
    )


def writable(session: Session, key: str) -> Channel:
    """The channel named by `key`, for a post or an upload: an archived one is refused."""
    ch = parse_channel(session, key)
    if ch.archived:
        raise Invalid(f"#{ch.name} is archived; restore it to post there")
    return ch


def _body(value: str | None, *, files: bool = False) -> str:
    """A message's text. Empty only when the message has `files` to show instead."""
    body = (value or "").strip()
    if not body and not files:
        raise Invalid("message is empty")
    if len(body) > MAX_BODY:
        raise Invalid(f"message is longer than {MAX_BODY} characters")
    return body


def _emoji(value: str) -> str:
    emoji = (value or "").strip()
    if not emoji or len(emoji) > MAX_EMOJI or any(c.isspace() for c in emoji):
        raise Invalid(f"emoji must be 1-{MAX_EMOJI} characters with no spaces")
    return emoji


def get_message(session: Session, message_id: str) -> Message:
    msg = session.get(Message, message_id)
    if msg is None:
        raise NotFound(f"message {message_id} not found")
    return msg


# --- Reading ---------------------------------------------------------------------------------


def item_refs(body_md: str) -> list[str]:
    """Item ids a body names (`?task=<id>` links and `#<id>`), first-named first."""
    return list(dict.fromkeys(ITEM_REF_RE.findall(body_md or "")))


HASH_REF_RE = re.compile(r"(?<![\w/#&])#([0-9A-Za-z]{12})(?![0-9A-Za-z])")
# `[[doc:<id>]]` / `[[list:<id>]]`: the board's `#` picker links pages and lists with these.
TOKEN_RE = re.compile(r"\[\[(doc|list):[0-9A-Za-z]{12}\]\]")
TOKEN_WORDS = {"doc": "a page", "list": "a list"}


def readable(session: Session, body_md: str) -> str:
    """The body with each `#<item id>` shown as `#` plus the item's title, as the board shows
    it (for notification text). Unknown ids stay as they are. A page or list token reads as
    "a page" / "a list": the text goes to whoever is notified, who may not see that page, so
    its title is never resolved here (the board resolves it with the reader's own index)."""
    body = TOKEN_RE.sub(lambda m: TOKEN_WORDS[m.group(1)], body_md or "")
    ids = set(HASH_REF_RE.findall(body))
    if not ids:
        return body
    titles = dict(session.execute(select(Item.id, Item.title).where(Item.id.in_(ids))).all())
    return HASH_REF_RE.sub(lambda m: f"#{titles.get(m.group(1), m.group(1))}", body)


def _linked_items(session: Session, ids: set[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    rows = session.execute(
        select(Item.id, Item.title, Item.list_id, Item.due_at, Status.name, Status.kind)
        .outerjoin(Status, Status.id == Item.status_id)
        .where(Item.id.in_(ids), Item.trashed_at.is_(None))
    ).all()
    return {
        r[0]: {
            "id": r[0],
            "title": r[1],
            "list_id": r[2],
            "due_at": r[3],
            "status": r[4],
            "done": r[5] in (StatusKind.done, StatusKind.cancelled),
        }
        for r in rows
    }


def message_views(session: Session, msgs: Sequence[Message]) -> list[dict[str, Any]]:
    """Messages as dicts: reactions, reply summary (count, last reply, who replied), the
    items they link and their files."""
    ids = [m.id for m in msgs]
    files = chat_files.files_of(session, ids)
    reactions: dict[str, dict[str, list[str]]] = defaultdict(dict)
    replies: dict[str, dict[str, Any]] = {}
    if ids:
        for r in session.scalars(
            select(MessageReaction)
            .where(MessageReaction.message_id.in_(ids))
            .order_by(MessageReaction.created_at)
        ):
            reactions[r.message_id].setdefault(r.emoji, []).append(r.account_id)
        roots = [m.id for m in msgs if m.parent_id is None]
        for parent_id, count, last in session.execute(
            select(Message.parent_id, func.count(), func.max(Message.id))
            .where(Message.parent_id.in_(roots), Message.deleted_at.is_(None))
            .group_by(Message.parent_id)
        ):
            replies[parent_id] = {"count": count, "last": last, "authors": []}
        if replies:
            for parent_id, author_id in session.execute(
                select(Message.parent_id, Message.author_id)
                .where(Message.parent_id.in_(list(replies)), Message.deleted_at.is_(None))
                .order_by(Message.id.desc())
            ):
                authors = replies[parent_id]["authors"]
                if author_id not in authors and len(authors) < 5:
                    authors.append(author_id)
    last_at = dict(
        session.execute(
            select(Message.id, Message.created_at).where(
                Message.id.in_([r["last"] for r in replies.values()])
            )
        ).all()
    )
    refs = {m.id: item_refs(m.body_md) for m in msgs}
    linked = _linked_items(
        session, {i for r in refs.values() for i in r} | {m.item_id for m in msgs if m.item_id}
    )
    out = []
    for m in msgs:
        rep = replies.get(m.id)
        out.append(
            {
                "id": m.id,
                "channel": m.channel,
                "parent_id": m.parent_id,
                "author_id": m.author_id,
                "body_md": m.body_md,
                "mentions": list(m.mentions_json or []),
                "item_id": m.item_id,
                "created_at": m.created_at,
                "edited_at": m.edited_at,
                "deleted_at": m.deleted_at,
                "members_only": bool(m.members_only),
                "reactions": [
                    {"emoji": e, "account_ids": a} for e, a in reactions.get(m.id, {}).items()
                ],
                "reply_count": rep["count"] if rep else 0,
                "last_reply_at": last_at.get(rep["last"]) if rep else None,
                "reply_author_ids": rep["authors"] if rep else [],
                "linked_items": [
                    linked[i]
                    for i in dict.fromkeys([*([m.item_id] if m.item_id else []), *refs[m.id]])
                    if i in linked
                ],
                "files": files.get(m.id, []),
            }
        )
    return out


def messages(
    session: Session,
    key: str,
    *,
    before: str | None = None,
    after: str | None = None,
    limit: int = PAGE,
) -> list[Message]:
    """A page of a channel, oldest first.

    Default: the newest `limit` top-level messages (replies live in their thread). `before`:
    the `limit` top-level messages before that id (scrolling back). `after`: every message
    after that id, replies too, oldest first (for an agent following a channel on a cursor).
    """
    ch = parse_channel(session, key)
    limit = max(1, min(limit, MAX_PAGE))
    q = select(Message).where(Message.channel == ch.key)
    if after is not None:
        return list(session.scalars(q.where(Message.id > after).order_by(Message.id).limit(limit)))
    q = q.where(Message.parent_id.is_(None))
    if before is not None:
        q = q.where(Message.id < before)
    rows = list(session.scalars(q.order_by(Message.id.desc()).limit(limit)))
    rows.reverse()
    return rows


def thread(session: Session, message_id: str) -> list[Message]:
    """A thread: its first message, then every reply, oldest first."""
    msg = get_message(session, message_id)
    root = get_message(session, msg.parent_id) if msg.parent_id else msg
    replies = session.scalars(
        select(Message).where(Message.parent_id == root.id).order_by(Message.id)
    )
    return [root, *replies]


def unread_messages(session: Session, account: Account) -> list[Message]:
    """Unread live messages, honoring both intentional cursors and exact exposure."""
    reads = {
        r.channel: r
        for r in session.scalars(select(ChannelRead).where(ChannelRead.account_id == account.id))
    }
    exposed = {key: set(r.exposed_ids or []) for key, r in reads.items()}
    return [
        m
        for m in session.scalars(
            select(Message)
            .outerjoin(
                ChannelRead,
                (ChannelRead.account_id == account.id) & (ChannelRead.channel == Message.channel),
            )
            .where(
                Message.author_id != account.id,
                Message.deleted_at.is_(None),
                (ChannelRead.read_through.is_(None)) | (Message.id > ChannelRead.read_through),
            )
            .order_by(Message.id)
        )
        if (
            m.channel not in reads
            or (m.id > reads[m.channel].read_through and m.id not in exposed[m.channel])
        )
    ]


def unread(session: Session, account: Account) -> dict[str, dict[str, Any]]:
    """{channel: {unread, mentions}} for channels with something the account has not read.
    Unread is every live message by someone else after the read cursor, replies included;
    mentions are the unread ones that `@mention` the account."""
    out: dict[str, dict[str, Any]] = {}
    for m in unread_messages(session, account):
        c = out.setdefault(
            m.channel,
            {
                "unread": 0,
                "mentions": 0,
                "first_unread_id": m.id,
                "first_unread_thread_id": m.parent_id,
                "unread_threads": {},
            },
        )
        c["unread"] += 1
        if m.parent_id:
            threads = c["unread_threads"]
            threads[m.parent_id] = threads.get(m.parent_id, 0) + 1
        if account.handle in (m.mentions_json or []):
            c["mentions"] += 1
    return out


def agent_turns(session: Session) -> dict[str, list[dict[str, Any]]]:
    """{channel: [open agent turns]} for the board's "is working" line (service/chatwake.py).
    A row not yet taken is `waiting` (mentioned, the runner has not picked it up); a taken row
    with a live lease is `working`. A lease that ran out is neither: `expire` ended that turn
    (the row stays only as a quarantine until the runner reconciles)."""
    now = utcnow()
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in session.scalars(
        select(ChatWake).order_by(ChatWake.channel, ChatWake.thread_id, ChatWake.account_id)
    ):
        if row.claimed_at is None:
            state, since = "waiting", row.created_at
        elif row.until is not None and row.until >= now:
            state, since = "working", row.claimed_at
        else:
            continue
        out[row.channel].append(
            {
                "agent_id": row.account_id,
                "thread_id": row.thread_id or None,
                "state": state,
                "since": since,
            }
        )
    return out


def list_channels(session: Session, account: Account) -> list[dict[str, Any]]:
    """Every channel the board shows: General, then each live section and its lists in board
    order, each with the account's unread and mention counts, the newest message and the agent
    turns open there."""
    counts = unread(session, account)
    turns = agent_turns(session)
    latest = dict(
        session.execute(
            select(Message.channel, func.max(Message.id)).group_by(Message.channel)
        ).all()
    )
    latest_at = dict(
        session.execute(
            select(Message.id, Message.created_at).where(Message.id.in_(list(latest.values())))
        ).all()
    )

    def row(ch: Channel, position: int) -> dict[str, Any]:
        last = latest.get(ch.key)
        c = counts.get(ch.key, {})
        return {
            "key": ch.key,
            "kind": ch.kind,
            "name": ch.name,
            "list_id": ch.list_id,
            "section_id": ch.section_id,
            "account_id": ch.account_id,
            "position": position,
            "mention_required": ch.mention_required,
            "unread": c.get("unread", 0),
            "mentions": c.get("mentions", 0),
            "first_unread_id": c.get("first_unread_id"),
            "first_unread_thread_id": c.get("first_unread_thread_id"),
            "unread_threads": c.get("unread_threads", {}),
            "last_message_id": last,
            "last_message_at": latest_at.get(last) if last else None,
            "agent_turns": turns.get(ch.key, []),
        }

    gate = access.current(session)
    out = [row(Channel(GENERAL, GENERAL, "General"), 0)]
    # Each enabled agent's own chat, read like General.
    agents = session.scalars(
        select(Account)
        .where(
            Account.kind == AccountKind.agent,
            Account.disabled_at.is_(None),
            Account.handle != SYSTEM_HANDLE,
        )
        .order_by(func.lower(Account.display_name), Account.id)
    ).all()
    if gate is not None and not gate.general:
        out = []  # no General for a guest or a list-limited token
    for agent in agents:
        key = f"{AGENT}:{agent.id}"
        # A guest sees the chats of the agents shared into its projects (service/access.py).
        if gate is None or gate.channel_level(key):
            out.append(
                row(
                    Channel(
                        key,
                        AGENT,
                        agent.display_name,
                        account_id=agent.id,
                        mention_required=agent.role == Role.guest,
                    ),
                    len(out),
                )
            )
    sections = session.scalars(
        select(Section).where(Section.archived_at.is_(None)).order_by(Section.position)
    ).all()
    lists_by: dict[str, list[List]] = defaultdict(list)
    for lst in session.scalars(
        select(List).where(List.archived_at.is_(None)).order_by(List.position)
    ):
        lists_by[lst.section_id].append(lst)
    n = max(len(out), 1)
    for sec in sections:
        # A section seen only through one of its lists has no channel for this caller.
        if gate is None or gate.channel_level(f"section:{sec.id}"):
            out.append(row(Channel(f"section:{sec.id}", "section", sec.name, None, sec.id), n))
        n += 1
        for lst in lists_by.get(sec.id, []):
            out.append(row(Channel(f"list:{lst.id}", "list", lst.name, lst.id, sec.id), n))
            n += 1
    return out


def channel_view(session: Session, account: Account, key: str) -> dict[str, Any]:
    ch = parse_channel(session, key)
    for row in list_channels(session, account):
        if row["key"] == ch.key:
            return row
    # An archived list or section: still readable, never listed.
    return {
        "key": ch.key,
        "kind": ch.kind,
        "name": ch.name,
        "list_id": ch.list_id,
        "section_id": ch.section_id,
        "account_id": ch.account_id,
        "position": -1,
        "mention_required": ch.mention_required,
        "unread": 0,
        "mentions": 0,
        "last_message_id": None,
        "last_message_at": None,
        "agent_turns": [],
    }


# --- Writing ---------------------------------------------------------------------------------


# Any non-guest agent: the workspace's own (service/guests.py).
workspace_agent = guests.workspace_agent


def members_only_post(
    session: Session, author_id: str, mentions: list[str], root: Message | None
) -> bool:
    """Whether a new message is hidden from guests (`Message.members_only`). A project's guests
    talk with its own agent; what passes between the workspace and a workspace agent stays
    with members: a workspace agent's post, a member's post that @mentions one, and every
    reply in a thread that started that way. A guest's own post is never hidden from it."""
    if root is not None and root.members_only:
        return True
    author = session.get(Account, author_id)
    if author is None or author.role == Role.guest:
        return False
    if workspace_agent(author):
        return True
    if not mentions:
        return False
    with access.unrestricted(session):
        named = session.scalars(select(Account).where(Account.handle.in_(mentions))).all()
    return any(workspace_agent(a) for a in named)


def post(
    session: Session,
    actor: Actor,
    key: str,
    *,
    body_md: str,
    parent_id: str | None = None,
    file_ids: Sequence[str] | None = None,
) -> Message:
    """Post to a channel, or reply in a thread (`parent_id`: any message of the thread).
    `file_ids`: the caller's unsent uploads to this channel (service/chat_files.py), which
    the message then carries; with files, the text may be empty."""
    body = _body(body_md, files=bool(file_ids))
    followers: list[str] = []
    root = None
    if parent_id is not None:
        msg = get_message(session, parent_id)
        root = get_message(session, msg.parent_id) if msg.parent_id else msg
        if key and key != root.channel:
            raise Invalid("that thread is in another channel")
        key = root.channel
        followers = list(
            dict.fromkeys(
                [
                    root.author_id,
                    *session.scalars(
                        select(Message.author_id)
                        .where(Message.parent_id == root.id)
                        .order_by(Message.id)
                    ),
                ]
            )
        )
        followers = [a for a in followers if a != actor.account_id]
    ch = writable(session, key)
    files = chat_files.unsent(session, actor.account_id, ch.key, file_ids or [])
    mentions = guests.reachable_handles(session, actor.account_id, mentions_in(session, body))
    if ch.kind == AGENT and ch.account_id != actor.account_id:
        # Workspace agents retain direct-chat addressing. A guest-role project agent shares
        # its chat with people talking to each other: only an explicit mention addresses it.
        agent = session.get(Account, ch.account_id)
        if agent is not None and agent.role != Role.guest and agent.handle not in mentions:
            mentions = [agent.handle, *mentions]
    hidden = members_only_post(session, actor.account_id, mentions, root)
    with apply_change(session, actor) as cx:
        msg = Message(
            id=new_event_id(),
            channel=ch.key,
            parent_id=root.id if root else None,
            author_id=actor.account_id,
            body_md=body,
            mentions_json=mentions,
            created_at=utcnow(),
            members_only=hidden,
        )
        cx.session.add(msg)
        after = {
            **snapshot(msg),
            "mentions": mentions,
            "channel_name": ch.name,
            "files": [chat_files.view(f) for f in files],
        }
        if root is not None:
            after["thread_followers"] = followers
        cx.record("message.posted", subject_id=msg.id, after=after, covers=[msg, *files])
        if files:
            cx.session.flush()  # the message is in its table before its files point at it
            for f in files:
                f.message_id = msg.id
        # A person's mention of a push agent wakes its runner; an agent's post answers its turn.
        from slate.service import chatwake  # noqa: PLC0415 (chatwake reads this module)

        chatwake.on_posted(cx, session, actor, msg, mentions, ch)
    return msg


def edit(session: Session, actor: Actor, message_id: str, *, body_md: str) -> Message:
    """Only the author edits a message. `after.mentions` carries only newly added handles."""
    msg = get_message(session, message_id)
    if msg.author_id != actor.account_id:
        raise Forbidden("only a message's author can edit it")
    if msg.deleted_at is not None:
        raise Invalid("that message was deleted")
    body = _body(body_md, files=bool(chat_files.files_of(session, [msg.id])))
    if body == msg.body_md:
        return msg
    before = msg.body_md
    mentions = guests.reachable_handles(session, actor.account_id, mentions_in(session, body))
    added = [h for h in mentions if h not in (msg.mentions_json or [])]
    # An edit that now @mentions a workspace agent hides the message from guests; an edit never
    # shows a hidden one again. Read before the change (a query would flush the edit early).
    hide = not msg.members_only and members_only_post(session, msg.author_id, added, None)
    with apply_change(session, actor) as cx:
        if hide:
            msg.members_only = True
        msg.body_md = body
        msg.mentions_json = mentions
        msg.edited_at = utcnow()
        cx.record(
            "message.edited",
            subject_id=msg.id,
            field="body_md",
            before={"body_md": before, "channel": msg.channel},
            after={"body_md": body, "channel": msg.channel, "mentions": added},
            covers=[msg],
        )
        if added:
            from slate.service import chatwake  # noqa: PLC0415

            chatwake.on_mentioned(
                cx, session, actor, msg, added, parse_channel(session, msg.channel)
            )
    return msg


def delete(session: Session, actor: Actor, message_id: str) -> Message:
    """The author deletes a message: the body and its files go, the row stays so its thread
    holds."""
    msg = get_message(session, message_id)
    if msg.author_id != actor.account_id:
        raise Forbidden("only a message's author can delete it")
    if msg.deleted_at is not None:
        return msg
    files = list(session.scalars(select(MessageFile).where(MessageFile.message_id == msg.id)))
    with apply_change(session, actor) as cx:
        before = {"body_md": msg.body_md, "channel": msg.channel, "parent_id": msg.parent_id}
        if files:
            before["files"] = [chat_files.view(f) for f in files]
        msg.body_md = ""
        msg.mentions_json = []
        msg.deleted_at = utcnow()
        cx.record(
            "message.deleted",
            subject_id=msg.id,
            before=before,
            after={"channel": msg.channel, "parent_id": msg.parent_id},
            covers=[msg, *files],
        )
        for f in files:
            cx.session.delete(f)
    return msg


def react(session: Session, actor: Actor, message_id: str, *, emoji: str) -> Message:
    """Add the caller's emoji. Twice is the same as once (nothing recorded)."""
    msg = get_message(session, message_id)
    emoji = _emoji(emoji)
    if msg.deleted_at is not None:
        raise Invalid("that message was deleted")
    if session.get(MessageReaction, (msg.id, actor.account_id, emoji)) is not None:
        return msg
    with apply_change(session, actor) as cx:
        reaction = MessageReaction(message_id=msg.id, account_id=actor.account_id, emoji=emoji)
        cx.session.add(reaction)
        cx.record(
            "message.reacted",
            subject_id=msg.id,
            after={"message_id": msg.id, "channel": msg.channel, "emoji": emoji},
            covers=[reaction],
        )
    return msg


def unreact(session: Session, actor: Actor, message_id: str, *, emoji: str) -> Message:
    """Take back the caller's emoji. Absent is fine (nothing recorded)."""
    msg = get_message(session, message_id)
    reaction = session.get(MessageReaction, (msg.id, actor.account_id, _emoji(emoji)))
    if reaction is None:
        return msg
    with apply_change(session, actor) as cx:
        cx.record(
            "message.unreacted",
            subject_id=msg.id,
            before={"message_id": msg.id, "channel": msg.channel, "emoji": reaction.emoji},
            covers=[reaction],
        )
        cx.session.delete(reaction)
    return msg


TASK_FIELDS = frozenset({"title", "due_at", "assignees", "priority", "status", "tags"})


def default_title(body_md: str) -> str:
    """The first line of a message, without markdown noise, as a task title."""
    for line in (body_md or "").splitlines():
        text = re.sub(r"^\s*(?:#+|[-*+]|\d+[.)])\s+", "", line)
        text = re.sub(r"[*_`]", "", text).strip()
        if text:
            return text[:200]
    return "Task from chat"


def make_task(
    session: Session,
    actor: Actor,
    message_id: str,
    *,
    list_id: str | None = None,
    **fields: Any,
) -> Item:
    """File one item from a message and link them. The item lands in `list_id`, else the
    channel's own list (General and section channels need a `list_id`). Its description quotes
    the message and says where it came from, and the message's files are attached to it (the
    message keeps its own). A message already made into a task is refused."""
    unknown = set(fields) - TASK_FIELDS
    if unknown:
        raise Invalid(f"unknown field(s): {', '.join(sorted(unknown))}")
    msg = get_message(session, message_id)
    if msg.deleted_at is not None:
        raise Invalid("that message was deleted")
    if msg.item_id is not None and session.get(Item, msg.item_id) is not None:
        raise Invalid(f"that message is already task {msg.item_id}")
    ch = parse_channel(session, msg.channel)
    target = list_id or ch.list_id
    if target is None:
        raise Invalid(f"#{ch.name} is not a list's channel: say which list_id the task goes in")
    author = session.get(Account, msg.author_id)
    quote = "\n".join(f"> {line}" for line in msg.body_md.splitlines())
    who = author.display_name if author else "someone"
    body = f"{quote}\n\nFrom #{ch.name} chat, posted by {who}.".strip()
    copies = [
        attachments.Pasted(f.filename, f.content_type, f.data)
        for f in session.scalars(
            select(MessageFile)
            .where(MessageFile.message_id == msg.id)
            .order_by(MessageFile.created_at, MessageFile.id)
        )
    ]
    fields = {k: v for k, v in fields.items() if v is not None}
    fields.setdefault("title", default_title(msg.body_md))
    with apply_change(session, actor) as cx:
        item = items_svc.create_item(session, actor, list_id=target, body_md=body, **fields)
        attachments.attach_pasted(cx, item, copies)
        msg.item_id = item.id
        cx.record(
            "message.task_created",
            item_id=item.id,
            subject_id=msg.id,
            after={"message_id": msg.id, "channel": msg.channel, "item_id": item.id},
            covers=[msg],
        )
    return item


def _advance(cx: Any, account_id: str, channel: str, through: str) -> bool:
    row = cx.session.get(ChannelRead, (account_id, channel), populate_existing=True)
    if row is not None and row.read_through >= through:
        return False
    if row is None:
        row = ChannelRead(account_id=account_id, channel=channel, read_through=through)
        cx.session.add(row)
    else:
        row.read_through = through
        row.exposed_ids = [i for i in (row.exposed_ids or []) if i > through]
    cx.record(
        "channel.read",
        subject_id=account_id,
        after={"channel": channel, "through": through},
        covers=[row],
    )
    return True


def _compact(
    session: Session, account_id: str, channel: str, cursor: str, exposed: set[str]
) -> tuple[str, list[str]]:
    """Fold exposures into the cursor and cap the rest. Returns (cursor, exposed ids above it).

    Safe step: the cursor moves up to the newest exposure below the first message that is
    still unread (live, by someone else, not exposed), so no unread message is ever covered
    and every id the cursor now covers can go. Cap: beyond `EXPOSED_KEEP` ids above that,
    the oldest become the cursor too, which does cover whatever unread sat below them: a
    message skipped while 500 later ones were read is read enough. Without this the list
    grew with reading history until a read-all.
    """
    ids = sorted(i for i in exposed if i > cursor)
    if not ids:
        return cursor, ids
    first_unread = session.scalar(
        select(Message.id)
        .where(
            Message.channel == channel,
            Message.id > cursor,
            Message.id < ids[-1],
            Message.author_id != account_id,
            Message.deleted_at.is_(None),
            Message.id.not_in(ids),
        )
        .order_by(Message.id)
        .limit(1)
    )
    through = max((i for i in ids if first_unread is None or i < first_unread), default=cursor)
    kept = [i for i in ids if i > through]
    if len(kept) > EXPOSED_KEEP:
        through, kept = kept[-EXPOSED_KEEP - 1], kept[-EXPOSED_KEEP:]
    return through, kept


def mark_read(
    session: Session,
    actor: Actor,
    key: str,
    *,
    through: str | None = None,
    message_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Union exact `message_ids`, or intentionally read a prefix through `through` (default
    newest). Neither path rewinds another device. Returns {channel, through, changed}."""
    ch = parse_channel(session, key)
    if message_ids is not None:
        if through is not None:
            raise Invalid("message_ids and through are exclusive")
        ids = set(message_ids)
        if len(ids) > MAX_PAGE:
            raise Invalid("at most 200 exposed messages per request")
        found = set(
            session.scalars(
                select(Message.id).where(Message.channel == ch.key, Message.id.in_(ids))
            )
        )
        if found != ids:
            raise Invalid("exposed messages must belong to this channel")
        with apply_change(session, actor) as cx:
            # Read under the single-process write lock, discarding stale device/session state.
            row = session.get(ChannelRead, (actor.account_id, ch.key), populate_existing=True)
            cursor = row.read_through if row else ""
            previous = set(row.exposed_ids or []) if row else set()
            merged = previous | {i for i in ids if i > cursor}
            changed = merged != previous
            if changed:
                # Query before touching the row: an autoflush of an uncovered write would trip
                # the guard (`WriteWithoutEvent`) before `record` covers it.
                moved, kept = _compact(session, actor.account_id, ch.key, cursor, merged)
                if row is None:
                    row = ChannelRead(account_id=actor.account_id, channel=ch.key, read_through="")
                    session.add(row)
                row.read_through, row.exposed_ids = moved, kept
                after: dict[str, Any] = {"channel": ch.key}
                if moved != cursor:
                    after["through"] = moved
                cursor = moved
                cx.record("channel.read", subject_id=actor.account_id, after=after, covers=[row])
        return {"channel": ch.key, "through": cursor or None, "changed": changed}
    newest = session.scalar(select(func.max(Message.id)).where(Message.channel == ch.key))
    target = min(through, newest) if through and newest else newest
    if target is None:
        return {"channel": ch.key, "through": None, "changed": False}
    with apply_change(session, actor) as cx:
        changed = _advance(cx, actor.account_id, ch.key, target)
    return {"channel": ch.key, "through": target, "changed": changed}


def mark_all_read(session: Session, actor: Actor) -> dict[str, Any]:
    """Read every channel up to its newest message, in one change."""
    newest = dict(
        session.execute(
            select(Message.channel, func.max(Message.id)).group_by(Message.channel)
        ).all()
    )
    done = []
    with apply_change(session, actor) as cx:
        for channel, through in newest.items():
            if _advance(cx, actor.account_id, channel, through):
                done.append(channel)
    return {"channels": done}
