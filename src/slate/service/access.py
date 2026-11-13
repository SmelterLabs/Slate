"""Workspace permissions: roles, shares, token limits, and the two hooks that enforce them.

Who sees what (ClickUp's model, without spaces/folders: a section is the space):

- **owner** and **admin** see and change everything. Only they manage accounts, roles and
  shares; only the owner makes or unmakes admins.
- **member** sees every section and list that is not private, at `edit`.
- **guest** sees nothing until something is shared with them.
- A **share** grants one account `view`, `comment` or `edit` on one section or list. A section
  share covers the section's lists that are not private; a list share reaches into a private
  list (or a private section). Shares only add; the highest grant wins.
- A **private** section or list drops out of members' default reach; owners and admins still
  see it.
- A **token** reaches no further than its account: `read` caps at `view`; `write`/`admin` at
  `edit`. `Token.limit_json` can narrow it further to some sections/lists and a lower level,
  which is how an agent gets a token for one list only.

Enforcement is not in the routes. `attach()` puts an `Access` on the request's session (REST
`api.auth`, MCP `mcp_server`), and two hooks on every `Session` (`slate.db`) do the rest:

- `read_criteria()` (do_orm_execute) adds `with_loader_criteria` for every table that belongs
  to a list or an item, plus events and chat messages, so an ORM read can never return a row
  the caller may not see: searches, boards, check-in, export and the audit log
  included. An unseen row is simply absent (404, not 403: no existence leak).
- `check_write()` (called from `core.Change` for every create/set/delete/record) raises
  `Forbidden` when the actor's level on the row's list is too low: `comment` for comments,
  reactions and chat, `view` for personal bookkeeping (watching, reminders, read marks), `edit`
  for the rest.

A session with no `Access` (scheduler, CLI, tests calling services directly) is unrestricted,
and so is an owner or admin using an unlimited token: both skip the hooks entirely.

What an *agent* may do at all, whatever its token, is the workspace deny-list in
`service/agent_rules.py`, checked by `core.Change` next to `check_object()` / `check_record()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, exists, false, func, literal, or_, select, true, union_all
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from slate.connection_models import AgentConnection
from slate.models import (
    Account,
    AccountKind,
    Attachment,
    AttachmentBlob,
    Automation,
    Capacity,
    ChannelRead,
    ChatWake,
    Comment,
    Doc,
    DocComment,
    DocFile,
    DocTag,
    DocVersion,
    DocWake,
    Event,
    InboxMark,
    InboxState,
    Item,
    ItemAssignee,
    ItemLink,
    List,
    Message,
    MessageFile,
    MessageReaction,
    Role,
    ScopeKind,
    Section,
    Share,
    ShareLevel,
    Token,
    View,
    Webhook,
    WorkspaceSetting,
    utcnow,
)
from slate.service.errors import Forbidden

ACCESS_KEY = "slate_access"
# The bound caller's `Access`, kept even when it is unrestricted (and ACCESS_KEY is absent so
# the read and write hooks cost nothing): "is this caller an admin" and the per-account event
# rules read it. Absent for Slate's own sessions (scheduler, CLI).
CALLER_KEY = "slate_caller"
VIEW, COMMENT, EDIT = 1, 2, 3
LEVEL_RANK = {ShareLevel.view: VIEW, ShareLevel.comment: COMMENT, ShareLevel.edit: EDIT}
LEVEL_NAME = {0: None, VIEW: "view", COMMENT: "comment", EDIT: "edit"}
ROLE_RANK = {Role.guest: 0, Role.member: 1, Role.admin: 2, Role.owner: 3}


def grants_admin(role: Role | str | None, scopes: Any, limit: Any = None) -> bool:
    """The one rule for "is this caller an admin": an owner or admin account, through a token
    that carries the `admin` scope and is not limited to some sections or lists."""
    return Role(role or Role.member) in (Role.owner, Role.admin) and "admin" in scopes and not limit


def level_rank(level: str | ShareLevel | None) -> int:
    if level is None:
        return 0
    return LEVEL_RANK[ShareLevel(level)]


@dataclass(frozen=True)
class Access:
    """What one caller (account + token) can reach. `lists is None` means every list at `cap`."""

    account_id: str
    role: Role
    cap: int = EDIT
    lists: dict[str, int] | None = None
    # Sections the caller sees in the sidebar, with the level of their own share or default.
    # 0 = visible only because one of its lists is (no section-wide rights).
    sections: dict[str, int] | None = None
    general: bool = True  # the General chat channel
    limited: bool = False  # the token narrows the account (Token.limit_json)
    # Agents whose own chat (`agent:<id>`) a restricted caller reaches without General: those
    # shared into a section or list the caller can see (a project's agent, for its guests),
    # with the caller's best level there. None when unrestricted.
    agents: dict[str, int] | None = None
    # When each of those agents was shared into a place the caller sees (the earliest live
    # share): its chat is readable from then on, not before (`agent_chat_since`).
    agent_since: dict[str, datetime] | None = None
    admin_scope: bool = True  # the token carries the `admin` scope (no token: Slate's own)

    @property
    def unrestricted(self) -> bool:
        return self.lists is None

    @property
    def is_admin(self) -> bool:
        return grants_admin(self.role, ("admin",) if self.admin_scope else (), self.limited)

    def list_level(self, list_id: str | None) -> int:
        if list_id is None:
            return 0
        if self.lists is None:
            return self.cap
        return self.lists.get(list_id, 0)

    def section_level(self, section_id: str | None) -> int:
        if section_id is None:
            return 0
        if self.sections is None:
            return self.cap
        return self.sections.get(section_id, 0)

    def channel_level(self, channel: str) -> int:
        kind, _, ref = channel.partition(":")
        if kind == "list":
            return self.list_level(ref)
        if kind == "section":
            return self.section_level(ref)
        if kind == "agent":
            # Your own chat; else members and admins (like General); else, for a guest or a
            # list-limited token, an agent shared into one of the projects it can see.
            if ref == self.account_id:
                return self.cap
            if self.general:
                return self.cap
            return (self.agents or {}).get(ref, 0)
        return self.cap if self.general else 0

    def agent_chat_since(self, channel: str) -> datetime | None:
        """The moment before which `channel` (`agent:<id>`) is history this caller may not
        read: None for an unrestricted caller, a member, or the agent itself."""
        kind, _, ref = channel.partition(":")
        if kind != "agent" or ref == self.account_id or self.general:
            return None
        return (self.agent_since or {}).get(ref)

    def doc_level(self, place: tuple[str | None, str | None]) -> int:
        """The level on a doc page, from its top page's `(section_id, list_id)`: the list's,
        else the section's (0 when the section is only visible through one of its lists), else
        the workspace's (General's: members yes, guests and list-limited tokens no)."""
        section_id, list_id = place
        if list_id is not None:
            return self.list_level(list_id)
        if section_id is not None:
            return self.section_level(section_id)
        return self.cap if self.general else 0

    def doc_sections(self) -> list[str]:
        return [sid for sid, lvl in (self.sections or {}).items() if lvl]

    def visible_channels(self) -> list[str] | None:
        if self.lists is None:
            return None
        out = [f"list:{lid}" for lid in self.lists]
        out += [f"section:{sid}" for sid, lvl in (self.sections or {}).items() if lvl]
        if self.general:
            out.append("general")
        out += [f"agent:{aid}" for aid, lvl in (self.agents or {}).items() if lvl]
        out.append(f"agent:{self.account_id}")
        return out


def compute(session: Session, account: Account, token: Token | None = None) -> Access:
    """The `Access` for `account` through `token`. Reads sections, lists and the account's
    shares; call it before `attach()` so its own reads are not filtered."""
    role = Role(account.role or Role.member)
    scopes = set((token.scopes if token is not None else "admin").split())
    cap = EDIT if scopes & {"write", "admin"} else VIEW
    limit = (token.limit_json if token is not None else None) or None
    if limit:
        cap = min(cap, level_rank(limit.get("level")) or EDIT)
    full = role in (Role.owner, Role.admin)
    admin_scope = "admin" in scopes
    if full and not limit:
        return Access(account.id, role, cap, admin_scope=admin_scope)
    if role == Role.member and not limit and not _any_private(session):
        return Access(account.id, role, cap, admin_scope=admin_scope)

    rows = session.execute(
        select(List.id, List.section_id, List.private, Section.private).join(
            Section, Section.id == List.section_id
        )
    ).all()
    shares = session.execute(
        select(Share.scope_kind, Share.scope_id, Share.level).where(Share.account_id == account.id)
    ).all()
    sec_share = {sid: level_rank(lvl) for kind, sid, lvl in shares if kind == "section"}
    list_share = {lid: level_rank(lvl) for kind, lid, lvl in shares if kind == "list"}
    section_rows = session.execute(select(Section.id, Section.private)).all()

    sections: dict[str, int] = {}
    for sid, private in section_rows:
        lvl = _section_rule(role, private, sec_share.get(sid, 0))
        if lvl:
            sections[sid] = lvl
    lists: dict[str, int] = {}
    for lid, sid, list_private, _section_private in rows:
        lvl = _list_rule(role, list_private, sections.get(sid, 0), list_share.get(lid, 0))
        if lvl:
            lists[lid] = lvl

    if limit:
        allow_lists = set(limit.get("list_ids") or [])
        allow_sections = set(limit.get("section_ids") or [])
        if allow_lists or allow_sections:
            section_of = {lid: sid for lid, sid, _, _ in rows}
            lists = {
                lid: lvl
                for lid, lvl in lists.items()
                if lid in allow_lists or section_of.get(lid) in allow_sections
            }
            sections = {sid: lvl for sid, lvl in sections.items() if sid in allow_sections}
    lists = {lid: min(lvl, cap) for lid, lvl in lists.items()}
    sections = {sid: min(lvl, cap) for sid, lvl in sections.items()}
    section_of = {lid: sid for lid, sid, _, _ in rows}
    for lid in lists:
        sections.setdefault(section_of[lid], 0)
    restricted_lists = limit and (limit.get("list_ids") or limit.get("section_ids"))
    agents, agent_since = _project_agents(session, lists, sections, guest=role == Role.guest)
    return Access(
        account.id,
        role,
        cap,
        lists=lists,
        sections=sections,
        general=role != Role.guest and not restricted_lists,
        limited=bool(limit),
        agents=agents,
        agent_since=agent_since,
        admin_scope=admin_scope,
    )


def _section_rule(role: Role, private: bool, share: int) -> int:
    """An account's level on a section: owners and admins edit every one, members every one
    that is not private; a share can only add to that."""
    full = role in (Role.owner, Role.admin)
    return max(EDIT if full or (role == Role.member and not private) else 0, share)


def _list_rule(role: Role, private: bool, section_level: int, share: int) -> int:
    """An account's level on a list: its section's, unless the list is private (then only an
    owner or admin keeps it); a share can only add to that."""
    full = role in (Role.owner, Role.admin)
    return max(0 if private and not full else section_level, share)


def levels_on(session: Session, scope_kind: str, scope_id: str) -> dict[str, int]:
    """Every enabled account's level on one section or list, as `compute` (with no token
    limit) would give it, in a fixed number of queries whatever the roster's size."""
    section_id = scope_id
    list_private = False
    if scope_kind == "list":
        section_id, list_private = session.execute(
            select(List.section_id, List.private).where(List.id == scope_id)
        ).one()
    section_private = bool(session.scalar(select(Section.private).where(Section.id == section_id)))
    shares: dict[tuple[str, str], int] = {}
    for account_id, kind, lvl in session.execute(
        select(Share.account_id, Share.scope_kind, Share.level).where(
            or_(
                and_(Share.scope_kind == "section", Share.scope_id == section_id),
                and_(Share.scope_kind == "list", Share.scope_id == scope_id),
            )
        )
    ):
        shares[(account_id, kind)] = level_rank(lvl)
    out: dict[str, int] = {}
    for account_id, role in session.execute(
        select(Account.id, Account.role).where(Account.disabled_at.is_(None))
    ):
        role = Role(role or Role.member)
        lvl = _section_rule(role, section_private, shares.get((account_id, "section"), 0))
        if scope_kind == "list":
            lvl = _list_rule(role, list_private, lvl, shares.get((account_id, "list"), 0))
        if lvl:
            out[account_id] = lvl
    return out


def _project_agents(
    session: Session, lists: dict[str, int], sections: dict[str, int], *, guest: bool = False
) -> tuple[dict[str, int], dict[str, datetime]]:
    """Enabled agents shared into a section or list the caller can see, each with the caller's
    best level on those places and the earliest such share's date: the guests of a project
    read and talk in its agent's chat, from the moment it joined the project (a share revoked
    and granted again starts the clock over). A guest caller reaches only guest agents (a
    project's own agent); a workspace agent shared into the project stays out of its
    sight (docs/collaboration.md "Members-only chat")."""
    q = (
        select(Share.account_id, Share.scope_kind, Share.scope_id, Share.created_at)
        .join(Account, Account.id == Share.account_id)
        .where(Account.kind == AccountKind.agent, Account.disabled_at.is_(None))
    )
    if guest:
        q = q.where(Account.role == Role.guest)
    rows = session.execute(q).all()
    out: dict[str, int] = {}
    since: dict[str, datetime] = {}
    for agent_id, kind, scope_id, created_at in rows:
        lvl = sections.get(scope_id, 0) if kind == "section" else lists.get(scope_id, 0)
        if not lvl:
            continue
        if lvl > out.get(agent_id, 0):
            out[agent_id] = lvl
        if agent_id not in since or created_at < since[agent_id]:
            since[agent_id] = created_at
    return out, since


def _any_private(session: Session) -> bool:
    return bool(
        session.scalar(select(Section.id).where(Section.private.is_(True)).limit(1))
        or session.scalar(select(List.id).where(List.private.is_(True)).limit(1))
    )


def bind(session: Session, account: Account, token_id: str) -> Access:
    """Compute and attach the access of `account` using token `token_id`."""
    found = compute(session, account, session.get(Token, token_id))
    attach(session, found)
    session.info["slate_authenticated_token_id"] = token_id
    return found


def attach(session: Session, access: Access) -> None:
    """Make `access` govern every later read and write on `session` (no-op when unrestricted
    and not limited: the hooks then cost nothing)."""
    session.info[CALLER_KEY] = access
    if access.unrestricted and access.cap == EDIT:
        session.info.pop(ACCESS_KEY, None)
        return
    session.info[ACCESS_KEY] = access


def current(session: Session) -> Access | None:
    """The caller's `Access` when it filters anything (None: unrestricted, or Slate's own)."""
    return session.info.get(ACCESS_KEY)


def caller(session: Session) -> Access | None:
    """The bound caller's `Access`, restricted or not (None: Slate's own session)."""
    return session.info.get(CALLER_KEY)


def is_admin(session: Session, account_id: str | None = None) -> bool:
    """Whether the session's caller is an owner or admin through an unlimited admin token
    (`grants_admin`). With no bound caller (scheduler, CLI, a service called directly) it is
    the role of `account_id` when one is named, else Slate's own session: an admin."""
    found = caller(session)
    if found is not None:
        return found.is_admin
    if account_id is None:
        return True
    with session.no_autoflush:
        account = session.get(Account, account_id)
    return account is not None and account.role in (Role.owner, Role.admin)


@dataclass
class _Detach:
    session: Session
    saved: Any = field(default=None)

    def __enter__(self) -> None:
        self.saved = self.session.info.pop(ACCESS_KEY, None)

    def __exit__(self, *exc: object) -> None:
        if self.saved is not None:
            self.session.info[ACCESS_KEY] = self.saved


def unrestricted(session: Session) -> _Detach:
    """`with access.unrestricted(session):` reads without the caller's filter, for bookkeeping
    that must see the whole table (slug uniqueness, computing someone else's access)."""
    return _Detach(session)


# --- Read filter -----------------------------------------------------------------------------

_items = Item.__table__
_messages = Message.__table__
_accounts = Account.__table__
_comments = Comment.__table__
_doc_comments = DocComment.__table__
_events = Event.__table__
_docs = Doc.__table__
# Workspace-wide things: capacities belong to no list, so they follow General
# (members yes; guests and list-limited tokens no).
_WORKSPACE_TYPES = ("capacity.",)
# Events about one account's own settings and bookkeeping: only that account and an admin
# read them, whatever else the caller can see (`_owned`, `EventGate._owns`).
PERSONAL_PREFIXES = (
    "agent.connection_",
    "favorite.",
    "inbox.",
    "push.",
    "session.",
    "token.",
    "webhook.",
)
PERSONAL_TYPES = ("account.prefs_changed", "account.digest", "channel.read", "item.read")


def is_personal(type_: str) -> bool:
    return type_ in PERSONAL_TYPES or type_.startswith(PERSONAL_PREFIXES)


def _owner_tables() -> list[Any]:
    from slate.models import Favorite, PushSubscription, Webhook

    return [Token.__table__, Webhook.__table__, Favorite.__table__, PushSubscription.__table__]


def _view_events(c: Any, me: str) -> Any:
    """SQL: the event is not about a saved view someone else keeps unshared."""
    t = View.__table__
    hidden = select(t.c.id).where(t.c.shared.is_(False), t.c.owner_id != me)
    return or_(~c.type.startswith("view."), c.subject_id.not_in(hidden))


def _owned(c: Any, me: str) -> Any:
    """SQL: the event is not personal, or it is `me`'s (subject, payload or owning row)."""
    personal = or_(c.type.in_(PERSONAL_TYPES), *(c.type.startswith(p) for p in PERSONAL_PREFIXES))
    return or_(
        ~personal,
        c.subject_id == me,
        c.after_json["account_id"].as_string() == me,
        c.before_json["account_id"].as_string() == me,
        *(c.subject_id.in_(select(t.c.id).where(t.c.account_id == me)) for t in _owner_tables()),
    )


_ITEM_SCOPED: list[type] = []
_LIST_SCOPED: list[type] = []
# Slate's own landing views (service/views.py SEED_VIEWS): generic lenses, so everyone's.
_SEED_VIEW_IDS: list[str] = []


def _classify() -> None:
    from slate.db import Base
    from slate.service.views import SEED_VIEWS

    _SEED_VIEW_IDS[:] = [spec["id"] for spec in SEED_VIEWS]
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        cols = mapper.columns
        if cls in (Item, Event, Message, List, Section, Doc):
            continue
        if "item_id" in cols:
            _ITEM_SCOPED.append(cls)
        elif "list_id" in cols:
            _LIST_SCOPED.append(cls)


def _visible_docs(lists: list[str], sections: list[str], general: bool) -> Any:
    """Ids of the doc pages whose top page sits somewhere visible. Only a top page carries a
    place, so a recursive CTE hands it down to every subpage."""
    tree = (
        select(_docs.c.id, _docs.c.section_id, _docs.c.list_id)
        .where(_docs.c.parent_id.is_(None))
        .cte("doc_tree", recursive=True)
    )
    child = _docs.alias("doc_child")
    tree = tree.union_all(
        select(child.c.id, tree.c.section_id, tree.c.list_id).where(child.c.parent_id == tree.c.id)
    )
    return select(tree.c.id).where(
        or_(
            tree.c.list_id.in_(lists),
            and_(tree.c.list_id.is_(None), tree.c.section_id.in_(sections)),
            and_(
                tree.c.list_id.is_(None),
                tree.c.section_id.is_(None),
                true() if general else false(),
            ),
        )
    )


def _chat_visible(
    c: Any,
    channels: list[str],
    agent_chats: str,
    lists: list[str],
    sections: list[str],
    members: Any = None,
) -> Any:
    """Whether a message (`c`: `messages` columns, under any alias) is in a channel the caller
    reads: one of `channels` (lists, sections, General, the caller's own agent chat), any
    agent's chat when `agent_chats` is the `agent:%` pattern (members), or a project agent's
    chat from the moment a share put the agent into a list or section the caller sees. A guest
    (`members` false) never reads a members-only message (`Message.members_only`)."""
    placed = or_(
        c.channel.in_(channels),
        c.channel.like(agent_chats),
        exists(
            select(Share.id).where(
                (literal("agent:") + Share.account_id) == c.channel,
                Share.created_at <= c.created_at,
                or_(
                    and_(Share.scope_kind == "section", Share.scope_id.in_(sections)),
                    and_(Share.scope_kind == "list", Share.scope_id.in_(lists)),
                ),
            )
        ),
    )
    if members is None:
        return placed
    return and_(placed, or_(members, c.members_only.is_(False)))


def read_criteria(state: ORMExecuteState) -> None:
    # Personal events are private even for members with unrestricted project access, and a
    # saved view that is not shared (and its history) is its owner's alone, admins included.
    bound: Access | None = state.session.info.get(CALLER_KEY)
    if state.is_select and bound is not None:
        owner = bound.account_id
        opts = [
            with_loader_criteria(
                View, lambda c: or_(c.shared.is_(True), c.owner_id == owner), include_aliases=True
            ),
            with_loader_criteria(Event, lambda c: _view_events(c, owner), include_aliases=True),
        ]
        if not bound.is_admin:
            opts.append(
                with_loader_criteria(Event, lambda c: _owned(c, owner), include_aliases=True)
            )
        state.statement = state.statement.options(*opts)
    access: Access | None = state.session.info.get(ACCESS_KEY)
    if access is None or access.lists is None or not state.is_select:
        return
    if state.execution_options.get("slate_unfiltered"):
        return
    if not _ITEM_SCOPED and not _LIST_SCOPED:
        _classify()
    lists = list(access.lists)
    sections = list(access.sections or {})
    me = access.account_id
    # Agents' chats (`agent:<id>`) follow General; a pattern, so an agent added after this
    # access was computed is still seen. The pattern never matches when General is not. A
    # project's agent chat is dated from its share (`_chat_visible`), so it is not a channel
    # here (`chat_channels`); wakes have no date and keep the plain channel list.
    agent_chats = "agent:%" if access.general else "\x00"
    channels = access.visible_channels() or []
    chat_channels = [ch for ch in channels if not ch.startswith("agent:") or ch == f"agent:{me}"]
    full_sections = access.doc_sections()
    seed_views = list(_SEED_VIEW_IDS)
    docs = _visible_docs(lists, access.doc_sections(), access.general)
    ws = true() if access.general else false()
    members = false() if access.role == Role.guest else true()
    # Lambdas, so the criteria follow aliases (`statuses AS item_status`); the closure values
    # become bound parameters, so the statement cache stays shared between callers.
    opts = [
        with_loader_criteria(Item, lambda c: c.list_id.in_(lists), include_aliases=True),
        with_loader_criteria(List, lambda c: c.id.in_(lists), include_aliases=True),
        with_loader_criteria(Section, lambda c: c.id.in_(sections), include_aliases=True),
        with_loader_criteria(
            Message,
            lambda c: _chat_visible(c, chat_channels, agent_chats, lists, full_sections, members),
            include_aliases=True,
        ),
        # A guest is not shown a workspace agent's open turn ("My Agent is working…"): it is
        # answering a member, and its answer is members-only (`Message.members_only`).
        with_loader_criteria(
            ChatWake,
            lambda c: and_(
                or_(c.channel.in_(channels), c.channel.like(agent_chats)),
                or_(
                    members,
                    c.account_id.not_in(_workspace_agents()),
                ),
            ),
            include_aliases=True,
        ),
        with_loader_criteria(
            Event,
            lambda c: or_(
                c.item_id.in_(select(_items.c.id).where(_items.c.list_id.in_(lists))),
                c.subject_id.in_(lists),
                c.subject_id.in_(full_sections),
                c.subject_id.in_(
                    select(_messages.c.id).where(
                        _chat_visible(
                            _messages.c, chat_channels, agent_chats, lists, full_sections, members
                        )
                    )
                ),
                c.subject_id.in_(docs),
                c.subject_id == me,
                # Workspace-wide news, as the live gate passes it (personal ones are filtered
                # for every caller above).
                and_(
                    ws,
                    or_(
                        c.type.startswith("tag."),
                        c.type.startswith("account."),
                        c.type.startswith("capacity."),
                    ),
                ),
                and_(
                    c.type.startswith("share."),
                    or_(
                        c.subject_id.in_(select(Share.id).where(Share.account_id == me)),
                        c.before_json["account_id"].as_string() == me,
                        c.after_json["account_id"].as_string() == me,
                    ),
                ),
            ),
            include_aliases=True,
        ),
        # Docs are never joined under an alias, so a plain expression will do.
        with_loader_criteria(Doc, Doc.id.in_(docs)),
        with_loader_criteria(DocVersion, DocVersion.doc_id.in_(docs)),
        with_loader_criteria(DocFile, DocFile.doc_id.in_(docs)),
        with_loader_criteria(DocComment, DocComment.doc_id.in_(docs)),
        with_loader_criteria(DocWake, DocWake.doc_id.in_(docs)),
        with_loader_criteria(DocTag, DocTag.doc_id.in_(docs)),
        with_loader_criteria(Capacity, ws),
        # A saved view follows its scope; a workspace-wide one is General's, except Slate's
        # own landing views and the caller's own.
        with_loader_criteria(
            View,
            lambda c: or_(
                c.id.in_(seed_views),
                c.owner_id == me,
                and_(c.scope_kind == ScopeKind.list, c.scope_id.in_(lists)),
                and_(c.scope_kind == ScopeKind.section, c.scope_id.in_(full_sections)),
                and_(c.scope_kind == ScopeKind.everything, ws),
            ),
            include_aliases=True,
        ),
        # A rule scoped to a section the caller sees, or to a list; one for the whole
        # workspace (and its webhook URL) is General's.
        with_loader_criteria(
            Automation,
            lambda c: or_(
                c.list_id.in_(lists),
                and_(c.list_id.is_(None), c.section_id.in_(full_sections)),
                and_(c.list_id.is_(None), c.section_id.is_(None), ws),
            ),
            include_aliases=True,
        ),
        with_loader_criteria(
            ItemLink,
            lambda c: (
                c.from_item_id.in_(select(_items.c.id).where(_items.c.list_id.in_(lists)))
                & c.to_item_id.in_(select(_items.c.id).where(_items.c.list_id.in_(lists)))
            ),
            include_aliases=True,
        ),
    ]
    # A row with no item or no list belongs to the whole workspace: General's, like capacities.
    for cls in _ITEM_SCOPED:
        if cls.item_id.nullable:
            opts.append(
                with_loader_criteria(
                    cls,
                    lambda c: or_(
                        and_(c.item_id.is_(None), ws),
                        c.item_id.in_(select(_items.c.id).where(_items.c.list_id.in_(lists))),
                    ),
                    include_aliases=True,
                )
            )
        else:
            opts.append(
                with_loader_criteria(
                    cls,
                    lambda c: c.item_id.in_(select(_items.c.id).where(_items.c.list_id.in_(lists))),
                    include_aliases=True,
                )
            )
    for cls in _LIST_SCOPED:
        if cls is Automation:
            continue  # above: a section-scoped rule has no list
        if cls.list_id.nullable:
            opts.append(
                with_loader_criteria(
                    cls,
                    lambda c: or_(and_(c.list_id.is_(None), ws), c.list_id.in_(lists)),
                    include_aliases=True,
                )
            )
        else:
            opts.append(
                with_loader_criteria(cls, lambda c: c.list_id.in_(lists), include_aliases=True)
            )
    if access.role == Role.guest:
        opts += _guest_criteria()
    state.statement = state.statement.options(*opts)


def guest_payloads(session: Session, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Event payloads as a guest reader may see them (history pages): what a workspace agent
    said or did is gone, its id and handle are scrubbed (`EventGate._for_guest`). Any other
    reader gets `payloads` unchanged."""
    caller = current(session)
    if caller is None or caller.role != Role.guest:
        return payloads
    gate = EventGate(caller.account_id)
    gate.access = caller
    out = []
    for p in payloads:
        shown = gate._for_guest(session, p)
        if shown is not None:
            out.append(shown)
    return out


def _workspace_agents() -> Any:
    """Ids of the workspace's own agents (non-guest agents, service/guests.py)."""
    return select(_accounts.c.id).where(
        _accounts.c.kind == "agent", _accounts.c.role != "guest", _accounts.c.handle != "slate"
    )


def _guest_criteria() -> list[Any]:
    """A project's guests see no trace of a workspace agent: not its card or page comments,
    nor comments addressed to it, nor its place among a card's assignees, nor anything it did
    in history. The cards themselves, including those it made, stay: they are the work
    (docs/collaboration.md)."""
    return [
        with_loader_criteria(
            Comment,
            lambda c: and_(
                c.author_id.not_in(_workspace_agents()),
                or_(c.assignee_id.is_(None), c.assignee_id.not_in(_workspace_agents())),
            ),
            include_aliases=True,
        ),
        with_loader_criteria(
            DocComment,
            lambda c: and_(
                c.author_id.not_in(_workspace_agents()),
                or_(c.assignee_id.is_(None), c.assignee_id.not_in(_workspace_agents())),
            ),
            include_aliases=True,
        ),
        with_loader_criteria(
            ItemAssignee,
            lambda c: c.account_id.not_in(_workspace_agents()),
            include_aliases=True,
        ),
        with_loader_criteria(
            Event,
            lambda c: and_(
                c.actor_id.not_in(_workspace_agents()),
                c.subject_id.not_in(_hidden_comments()),
            ),
            include_aliases=True,
        ),
    ]


def _hidden_comments() -> Any:
    """Card and page comments addressed to a workspace agent: hidden from guests, with their
    events (the comment event comes before its Ask is set). A deleted card comment that was by
    or addressed to one stays hidden: its row is gone, so its `comment.deleted` event says."""
    gone = _events.c.before_json
    return union_all(
        select(_comments.c.id).where(_comments.c.assignee_id.in_(_workspace_agents())),
        select(_doc_comments.c.id).where(_doc_comments.c.assignee_id.in_(_workspace_agents())),
        select(_events.c.subject_id).where(
            _events.c.type == "comment.deleted",
            or_(
                func.json_extract(gone, "$.author_id").in_(_workspace_agents()),
                func.json_extract(gone, "$.assignee_id").in_(_workspace_agents()),
            ),
        ),
    )


# --- Write check -----------------------------------------------------------------------------

# Personal bookkeeping a viewer may do on what they can see.
_VIEW_MODELS = frozenset(
    {
        "Watcher",
        "ItemRead",
        "Reminder",
        "InboxState",
        "InboxMark",
        "ChannelRead",
        "Favorite",
        "PushSubscription",
    }
)
_COMMENT_MODELS = frozenset(
    {
        "Comment",
        "CommentReaction",
        "Message",
        "MessageFile",
        "MessageReaction",
        "ChatWake",
        "DocComment",
        "DocWake",
    }
)
# Global rows a guest may still write (their own preferences, tags they put on shared items).
_GUEST_GLOBAL = frozenset(
    {
        "Account",
        "Token",
        "Tag",
        "Favorite",
        "InboxState",
        "InboxMark",
        "PushSubscription",
        "Reminder",
        "View",
        "ChannelRead",
    }
)
_COMMENT_TYPES = (
    "item.commented",
    "comment.",
    "message.",
    "reaction.",
    "doc.commented",
    "doc.comment_",
)
_VIEW_TYPES = ("item.watched", "item.unwatched", "item.read", "reminder.", "inbox.")


def _need(obj: Any) -> int:
    name = type(obj).__name__
    if name in _VIEW_MODELS:
        return VIEW
    if name in _COMMENT_MODELS:
        return COMMENT
    return EDIT


def _need_for_type(type_: str) -> int:
    if type_.startswith(_VIEW_TYPES):
        return VIEW
    if type_.startswith(_COMMENT_TYPES):
        return COMMENT
    return EDIT


def _item_list(session: Session, item_id: str) -> str | None:
    # No autoflush: the change being checked is half made (fields set, event not recorded
    # yet), so loading the item must not flush it (a flush here raises WriteWithoutEvent).
    with session.no_autoflush:
        item = session.get(Item, item_id)
    if item is not None:
        return item.list_id
    # A row from this same change that the filter has not been told about yet, or an item
    # the caller cannot see (then it is absent and the check fails).
    return None


def doc_place(session: Session, doc: Doc | str | None) -> tuple[str | None, str | None]:
    """The `(section_id, list_id)` of a page's top page, read past the caller's filter (a
    page being created in this change is found among the session's new objects)."""
    seen: set[str] = set()
    # No autoflush: a change mid-way (fields set, event not yet recorded) must not flush here.
    with unrestricted(session), session.no_autoflush:
        while doc is not None:
            if isinstance(doc, str):
                doc_id = doc
                doc = session.get(Doc, doc_id) or next(
                    (o for o in session.new if isinstance(o, Doc) and o.id == doc_id), None
                )
                continue
            if doc.parent_id is None or doc.id in seen:
                return doc.section_id, doc.list_id
            seen.add(doc.id)
            doc = doc.parent_id
    return None, None


def _deny(need: int, where: str) -> Forbidden:
    return Forbidden(f"you need {LEVEL_NAME[need]} access to {where}")


def check_object(
    session: Session, obj: Any, *, field_name: str | None = None, value: Any = None
) -> None:
    """Raise `Forbidden` unless the session's caller may write `obj` (`field_name`/`value`
    for a `set`, so a move checks the destination too)."""
    access: Access | None = session.info.get(ACCESS_KEY)
    if access is None:
        return
    if isinstance(obj, AgentConnection):
        if obj.account_id != access.account_id and not access.is_admin:
            raise Forbidden("only this account or a manager can change a connection")
        return  # Exact bound credential is enforced by the connection service.
    if isinstance(obj, Token) and obj.account_id == access.account_id:
        return  # revoking your own token is always allowed, even with a read token
    need = _need(obj)
    if access.cap < need:
        raise Forbidden(f"this token can only {LEVEL_NAME[access.cap]}")
    if access.lists is None:
        return
    if isinstance(obj, Section):
        if field_name == "position" and (access.role == Role.guest or access.limited):
            raise Forbidden("guests and limited tokens cannot reorder global sections")
        if obj.id in (access.sections or {}) or field_name is not None:
            if access.section_level(obj.id) < EDIT:
                raise _deny(EDIT, "change this section")
            return
        if access.role == Role.guest or access.limited:
            raise Forbidden("guests and list-limited tokens cannot create sections")
        return
    if isinstance(obj, List):
        if field_name is None or field_name == "section_id":
            sid = value if field_name == "section_id" else obj.section_id
            if access.section_level(sid) < EDIT and access.list_level(obj.id) < EDIT:
                raise _deny(EDIT, "add lists to this section")
            if field_name is None:
                return
        if access.list_level(obj.id) < EDIT:
            raise _deny(EDIT, "change this list")
        return
    if isinstance(obj, Message | MessageFile | ChannelRead | ChatWake):
        if access.channel_level(obj.channel) < need:
            raise _deny(need, "post in this channel")
        return
    if isinstance(obj, MessageReaction):
        msg = session.get(Message, obj.message_id)
        if msg is None or access.channel_level(msg.channel) < need:
            raise _deny(need, "react in this channel")
        return
    if isinstance(obj, Item):
        if access.list_level(obj.list_id) < need:
            raise _deny(need, "change items in this list")
        if field_name == "list_id" and access.list_level(value) < EDIT:
            raise _deny(EDIT, "move items into that list")
        return
    if isinstance(obj, InboxState | InboxMark):
        return
    if isinstance(obj, Webhook):
        # A webhook hears what its *account* can read (the gate in deliveries.fan_out knows
        # no token), so a token narrowed below its account may not subscribe one.
        if access.limited:
            raise Forbidden("a list-limited token cannot subscribe webhooks; use an unlimited one")
        return
    if isinstance(obj, Automation):
        # A rule acts on every item in its scope as the system agent, so its scope must be
        # one the caller may edit; a rule for the whole workspace would reach private lists.
        check_scope(session, obj.list_id, obj.section_id, "automations")
        if field_name in ("list_id", "section_id") and value is not None:
            check_scope(
                session,
                value if field_name == "list_id" else None,
                value if field_name == "section_id" else None,
                "automations",
            )
        return
    if isinstance(obj, Doc | DocVersion | DocFile | DocComment | DocWake):
        if access.doc_level(doc_place(session, obj if isinstance(obj, Doc) else obj.doc_id)) < need:
            raise _deny(need, "change this doc")
        return
    item_id = getattr(obj, "item_id", None)
    if item_id is None and isinstance(obj, ItemLink):
        item_id = obj.from_item_id
    if item_id is None and isinstance(obj, AttachmentBlob):
        # The bytes follow their attachment's task; both rows arrive in one change, so the
        # attachment may still be pending (not yet in the identity map).
        att = next(
            (a for a in session.new if isinstance(a, Attachment) and a.id == obj.attachment_id),
            None,
        )
        if att is None:
            with session.no_autoflush:
                att = session.get(Attachment, obj.attachment_id)
        item_id = att.item_id if att is not None else None
        if item_id is None:
            raise _deny(need, "change this task")
    if item_id is not None:
        if access.list_level(_item_list(session, item_id)) < need:
            raise _deny(need, "change this task")
        return
    list_id = getattr(obj, "list_id", None)
    if list_id is not None:
        if access.list_level(list_id) < need:
            raise _deny(need, "change this list")
        if field_name == "list_id" and access.list_level(value) < need:
            raise _deny(need, "move this into that list")  # e.g. a recurrence rule
        return
    if isinstance(obj, WorkspaceSetting):
        if not access.is_admin:
            raise Forbidden("only an owner or admin can change workspace settings")
        return
    if isinstance(obj, Share):
        if not access.is_admin:
            raise Forbidden("only an owner or admin can share")
        return
    if isinstance(obj, Account):
        if obj.id != access.account_id and not access.is_admin:
            raise Forbidden("only an owner or admin can change other accounts")
        return
    if isinstance(obj, Capacity):
        if not access.general:
            raise Forbidden("capacity is for the whole workspace, not this token")
        if obj.account_id != access.account_id and not access.is_admin:
            raise Forbidden("only an owner or admin can set someone else's capacity")
        return
    if access.role == Role.guest and type(obj).__name__ not in _GUEST_GLOBAL:
        raise Forbidden("guests can only work inside the lists shared with them")


def check_record(session: Session, type_: str, item_id: str | None) -> None:
    """A `record()` with no covered object: check the item it names, by event type."""
    access: Access | None = session.info.get(ACCESS_KEY)
    if access is None or item_id is None:
        return
    need = _need_for_type(type_)
    if access.cap < need:
        raise Forbidden(f"this token can only {LEVEL_NAME[access.cap]}")
    if access.lists is not None and access.list_level(_item_list(session, item_id)) < need:
        raise _deny(need, "change this task")


# Digest snapshots contain references outside their outer account subject. Never authorize
# these by subject alone, and never reuse historical aggregate counts for a restricted reader.
DIGEST_GROUPS = ("overdue", "today", "waiting")


def digest_snapshot(session: Session, body: Any, caller: Access | None) -> dict[str, Any]:
    if caller is None or caller.unrestricted:
        return body if isinstance(body, dict) else {}
    body = body if isinstance(body, dict) else {}
    groups = {
        key: [r for r in body.get(key, []) if isinstance(r, dict) and isinstance(r.get("id"), str)]
        if isinstance(body.get(key), list)
        else []
        for key in DIGEST_GROUPS
    }
    ids = {r["id"] for rows in groups.values() for r in rows}
    # Explicit SQL, not Session.get(): a previously loaded item may still be in the identity
    # map after a move or revocation. Missing/deleted references fail closed as well.
    visible = (
        set(
            session.scalars(
                select(_items.c.id).where(
                    _items.c.id.in_(ids), _items.c.list_id.in_(list(caller.lists or {}))
                )
            )
        )
        if ids
        else set()
    )
    safe = {}
    for key, rows in groups.items():
        seen = set()
        safe[key] = []
        for row in rows:
            if row["id"] in visible and row["id"] not in seen:
                seen.add(row["id"])
                safe[key].append(
                    {k: row[k] for k in ("id", "title", "due_at", "priority") if k in row}
                )
    return {"date": body.get("date"), "counts": {k: len(v) for k, v in safe.items()}, **safe}


def event_snapshots(session: Session | None, ev: Event) -> tuple[Any, Any]:
    if ev.type != "account.digest" or session is None:
        return ev.before_json, ev.after_json
    caller = current(session)
    return (
        digest_snapshot(session, ev.before_json, caller) if ev.before_json is not None else None,
        digest_snapshot(session, ev.after_json, caller),
    )


# --- Live events ---------------------------------------------------------------------------

# Event types after which an open stream recomputes its caller's access.
_REACH_TYPES = ("share.", "account.role_changed", "list.", "section.")


class EventGate:
    """Decides, event by event, whether a live payload may go to one caller (SSE stream,
    webhook owner, push target). Same rules as the read filter; remembers item -> list so a
    busy stream costs one lookup per new item. Recomputes itself when shares, roles, privacy
    or the list tree change."""

    def __init__(self, account_id: str, token_id: str | None = None) -> None:
        self.account_id = account_id
        self.token_id = token_id
        self.access: Access | None = None
        self.denied = False
        self.narrowed = False
        self._loaded = False
        self._connection_manager = False
        self._item_list: dict[str, str | None] = {}
        self._hidden_message: dict[str, bool] = {}
        self._hidden_comment: dict[str, bool] = {}
        self._agents: set[str] | None = None
        self._agent_handles: set[str] = set()
        self._doc_place: dict[str, tuple[str | None, str | None]] = {}

    def load(self, session: Session) -> Access | None:
        previous = self.access
        account = session.get(Account, self.account_id)
        token = session.get(Token, self.token_id) if self.token_id else None
        self.denied = bool(
            account is None
            or account.disabled_at
            or (
                self.token_id
                and (
                    token is None
                    or token.revoked_at
                    or (token.expires_at and token.expires_at <= utcnow())
                )
            )
        )
        if self.denied:
            self.access = Access(
                self.account_id, Role.guest, VIEW, lists={}, sections={}, general=False
            )
            self._admin = False
        else:
            with unrestricted(session):
                found = compute(session, account, token)
            self.access = None if found.unrestricted else found
            self._admin = found.is_admin
        now = self.access
        self.narrowed = bool(
            self._loaded
            and now is not None
            and (
                previous is None
                or (
                    previous is not None
                    and (
                        any(now.list_level(k) < v for k, v in (previous.lists or {}).items())
                        or any(
                            now.section_level(k) < v for k, v in (previous.sections or {}).items()
                        )
                        or (previous.general and not now.general)
                        or any(
                            now.channel_level(f"agent:{k}") < v
                            for k, v in (previous.agents or {}).items()
                        )
                    )
                )
            )
        )
        self._loaded = True
        return self.access

    def needs_reload(self, payload: dict[str, Any]) -> bool:
        return str(payload.get("type", "")).startswith(_REACH_TYPES)

    def _list_of(self, session: Session, item_id: str) -> str | None:
        if item_id not in self._item_list:
            with unrestricted(session):
                self._item_list[item_id] = session.scalar(
                    select(_items.c.list_id).where(_items.c.id == item_id)
                )
        return self._item_list[item_id]

    def _members_only(self, session: Session, payload: dict[str, Any]) -> bool:
        after = payload.get("after") if isinstance(payload.get("after"), dict) else {}
        if after.get("members_only"):
            return True
        subject = payload.get("subject_id")
        if not subject or not str(payload.get("type", "")).startswith("message."):
            return False
        if subject not in self._hidden_message:
            with unrestricted(session):
                self._hidden_message[subject] = bool(
                    session.scalar(
                        select(_messages.c.members_only).where(_messages.c.id == subject)
                    )
                )
        return self._hidden_message[subject]

    def _place_of(self, session: Session, doc_id: str) -> tuple[str | None, str | None]:
        if doc_id not in self._doc_place:
            self._doc_place[doc_id] = doc_place(session, doc_id)
        return self._doc_place[doc_id]

    def project(self, session: Session, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self._allows_outer(session, payload):
            return None
        if self.access is not None and self.access.role == Role.guest:
            payload = self._for_guest(session, payload)
            if payload is None:
                return None
        if payload.get("type") != "account.digest":
            return payload
        after = digest_snapshot(session, payload.get("after"), self.access)
        if not any((after.get("counts") or {}).values()):
            return None
        before = payload.get("before")
        return {
            **payload,
            "after": after,
            "before": digest_snapshot(session, before, self.access) if before is not None else None,
        }

    # What a workspace agent says or does as an agent, which a guest never hears of; its plain
    # edits to a card (status, title, a new card) still reach the guest, anonymously.
    _AGENT_TALK = (
        "item.commented",
        "item.mentioned",
        "item.reported",
        "item.work_",
        "item.run_requested",
        "item.watched",
        "item.unwatched",
        "comment.",
        "doc.commented",
        "doc.comment_assigned",
        "doc.comment_edited",
        "doc.comment_resolved",
        "doc.mentioned",
        "doc.agent_turn",
        "message.",
        "inbox.",
    )

    def _for_guest(self, session: Session, payload: dict[str, Any]) -> dict[str, Any] | None:
        """A guest sees no trace of a workspace agent (service/guests.py)."""
        if self._agents is None:
            with unrestricted(session):
                self._agents = set(session.scalars(_workspace_agents()))
                self._agent_handles = set(
                    session.scalars(
                        select(_accounts.c.handle).where(_accounts.c.id.in_(self._agents))
                    )
                )
        type_ = str(payload.get("type", ""))
        by_agent = payload.get("actor_id") in self._agents
        if by_agent and type_.startswith(self._AGENT_TALK):
            return None
        subject = payload.get("subject_id")
        if subject and type_.startswith(
            ("item.commented", "comment.", "doc.commented", "doc.comment_")
        ):
            if subject not in self._hidden_comment:
                with unrestricted(session):
                    self._hidden_comment[subject] = bool(
                        session.scalar(
                            select(_comments.c.id).where(
                                _comments.c.id == subject,
                                _comments.c.assignee_id.in_(self._agents),
                            )
                        )
                        or session.scalar(
                            select(_doc_comments.c.id).where(
                                _doc_comments.c.id == subject,
                                _doc_comments.c.assignee_id.in_(self._agents),
                            )
                        )
                    )
            if self._hidden_comment[subject]:
                return None
        before, after = payload.get("before"), payload.get("after")
        if (
            type_ == "comment.deleted"
            and isinstance(before, dict)
            and {before.get("author_id"), before.get("assignee_id")} & self._agents
        ):
            return None  # a deleted comment by it or addressed to it
        if isinstance(after, dict) and after.get("assignee_id") in self._agents:
            return None  # an Ask addressed to it
        out = payload
        if by_agent:
            out = {**out, "actor_id": None, "session_ref": None}
        if type_ == "item.assigned":
            shown = {
                k: [h for h in out[k] if h not in self._agent_handles]
                if isinstance(out.get(k), list)
                else out.get(k)
                for k in ("before", "after")
            }
            if shown["before"] == shown["after"]:
                return None
            out = {**out, **shown}
        for k in ("before", "after"):
            snap = out.get(k)
            if not isinstance(snap, dict):
                continue
            if snap.get("created_by") in self._agents:
                snap = {**snap, "created_by": None}
            for field_ in ("reply_to", "thread_followers", "for", "queued"):
                if isinstance(snap.get(field_), list):
                    snap = {**snap, field_: [a for a in snap[field_] if a not in self._agents]}
            out = {**out, k: snap}
        return out

    def allows(self, session: Session, payload: dict[str, Any]) -> bool:
        # Boolean-only consumers may send the original bytes: fail closed if projection
        # would redact anything. Projection-aware push/SSE retain the authorized subset.
        return self.project(session, payload) == payload

    def _allows_outer(self, session: Session, payload: dict[str, Any]) -> bool:
        if self.denied:
            return False
        type_ = str(payload.get("type", ""))
        if is_personal(type_) and not self._admin:
            return self._owns(session, payload)
        if type_.startswith("view.") and not self._sees_view(session, payload.get("subject_id")):
            return False
        a = self.access
        if a is None or a.lists is None:
            return True
        if payload.get("subject_id") == a.account_id:
            return True
        type_ = str(payload.get("type", ""))
        before = payload.get("before") if isinstance(payload.get("before"), dict) else {}
        after = payload.get("after") if isinstance(payload.get("after"), dict) else {}
        item_id = payload.get("item_id")
        if item_id:
            if payload.get("field") == "list_id":
                self._item_list[item_id] = payload.get("after")
                return bool(
                    a.list_level(payload.get("before")) or a.list_level(payload.get("after"))
                )
            list_id = after.get("list_id") if type_ == "item.created" else None
            list_id = list_id or self._list_of(session, item_id)
            return a.list_level(list_id) > 0
        if type_.startswith("message.") or type_.startswith("channel."):
            channel = after.get("channel") or before.get("channel")
            if not channel or a.channel_level(channel) <= 0:
                return False
            # A project agent's chat is readable from its share on, never the history before.
            since = a.agent_chat_since(channel)
            at = _parse_at(payload.get("at"))
            if since is not None and at is not None and at < since:
                return False
            # A guest never hears about a members-only message (its post, edits, reactions,
            # the agent's turn on it): every message.* event's subject is the message.
            return a.role != Role.guest or not self._members_only(session, payload)
        subject = payload.get("subject_id")
        if type_.startswith(_WORKSPACE_TYPES):
            return a.general
        if type_.startswith("doc."):
            if not subject:
                return False
            if type_ == "doc.moved":
                # Seen leaving or arriving; its subpages' places are read again on demand.
                self._doc_place.clear()
                snaps = [before, after]
            elif type_ == "doc.deleted":
                cached = self._doc_place.get(subject)
                if cached is not None:
                    return a.doc_level(cached) > 0
                snaps = [before]
            else:
                return a.doc_level(self._place_of(session, subject)) > 0
            places = [doc_place_of(s) for s in snaps]
            return any(
                a.doc_level(self._place_of(session, p) if isinstance(p, str) else p) > 0
                for p in places
            )
        if type_.startswith("list."):
            return a.list_level(subject) > 0 or a.section_level(after.get("section_id")) > 0
        if type_.startswith(("section.", "status.")):
            return a.section_level(subject) > 0 or a.list_level(after.get("list_id")) > 0
        if type_.startswith("share."):
            if (after or before).get("account_id") == a.account_id:
                return True
            row = session.get(Share, subject) if subject else None
            return bool(row and row.account_id == a.account_id)
        if type_.startswith("tag."):
            return a.general
        if type_.startswith("account."):
            return a.general
        return False

    def _sees_view(self, session: Session, view_id: Any) -> bool:
        """A saved view that is not shared is its owner's alone."""
        if not view_id:
            return True
        t = View.__table__
        row = session.execute(select(t.c.shared, t.c.owner_id).where(t.c.id == view_id)).first()
        return row is None or bool(row.shared) or row.owner_id == self.account_id

    def _owns(self, session: Session, payload: dict[str, Any]) -> bool:
        """A personal event is this caller's: about its account, or its token, device, hook."""
        me = self.account_id
        subject = payload.get("subject_id")
        if subject == me:
            return True
        for key in ("after", "before"):
            snap = payload.get(key)
            if isinstance(snap, dict) and snap.get("account_id") == me:
                return True
        if not subject:
            return False
        return any(
            session.execute(select(t.c.account_id).where(t.c.id == subject)).scalar() == me
            for t in _owner_tables()
        )


def _parse_at(value: Any) -> datetime | None:
    """A payload's `at` as an aware UTC datetime (None when absent or unreadable)."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def doc_place_of(snapshot: dict[str, Any]) -> tuple[str | None, str | None] | str:
    """A doc event's place snapshot as a top place, or the parent's id to look up."""
    if snapshot.get("parent_id"):
        return str(snapshot["parent_id"])
    return snapshot.get("section_id"), snapshot.get("list_id")


# --- Helpers for callers ------------------------------------------------------------------


def participant_ids(session: Session) -> set[str] | None:
    """Directory identities visible through this caller's actual sections/lists, not telemetry."""
    a = current(session)
    if a is None or a.lists is None:
        return None
    from slate.models import ItemAssignee

    ids = {a.account_id}
    if not a.lists and not a.sections:
        return ids
    with unrestricted(session):
        ids.update(
            session.scalars(
                select(Account.id).where(
                    Account.role.in_([Role.owner, Role.admin]), Account.disabled_at.is_(None)
                )
            )
        )
        ids.update(
            session.scalars(
                select(Share.account_id).where(
                    or_(
                        and_(Share.scope_kind == "section", Share.scope_id.in_(a.doc_sections())),
                        and_(Share.scope_kind == "list", Share.scope_id.in_(list(a.lists))),
                    )
                )
            )
        )
        ids.update(
            session.scalars(
                select(ItemAssignee.account_id)
                .join(Item, Item.id == ItemAssignee.item_id)
                .where(Item.list_id.in_(list(a.lists)))
            )
        )
        ids.update(session.scalars(select(Item.created_by).where(Item.list_id.in_(list(a.lists)))))
        public = session.scalar(
            select(Section.id)
            .where(Section.id.in_(a.doc_sections()), Section.private.is_(False))
            .limit(1)
        )
        if public:
            ids.update(session.scalars(select(Account.id).where(Account.role == Role.member)))
        if a.role == Role.guest:
            # A guest knows no workspace agent (service/guests.py), on any roster or plan.
            ids -= set(session.scalars(_workspace_agents()))
    return ids


def can_see_list(session: Session, list_id: str) -> bool:
    access = current(session)
    return access is None or access.list_level(list_id) > 0


def check_scope(session: Session, list_id: str | None, section_id: str | None, what: str) -> None:
    """Raise `Forbidden` unless the caller may edit the scope `(list_id, section_id)`: the
    list, else the section, else (neither: the whole workspace) only an unrestricted caller."""
    access = current(session)
    if access is None or access.lists is None:
        return
    if list_id is not None:
        if access.list_level(list_id) < EDIT:
            raise _deny(EDIT, f"make {what} for this list")
    elif section_id is not None:
        if access.section_level(section_id) < EDIT:
            raise _deny(EDIT, f"make {what} for this section")
    else:
        raise Forbidden(f"{what} for the whole workspace need access to every list")


def require_list_level(session: Session, list_id: str | None, need: int, what: str) -> None:
    """Raise `Forbidden` unless the caller has `need` on `list_id` (no `Access`: allowed)."""
    access = current(session)
    if access is None:
        return
    if access.cap < need or access.list_level(list_id) < need:
        raise _deny(need, what)


def require_admin(session: Session, what: str = "do this") -> None:
    if not is_admin(session):
        raise Forbidden(f"only an owner or admin can {what}")


__all__ = [
    "ACCESS_KEY",
    "CALLER_KEY",
    "COMMENT",
    "EDIT",
    "VIEW",
    "Access",
    "attach",
    "check_object",
    "check_record",
    "compute",
    "current",
    "read_criteria",
]
