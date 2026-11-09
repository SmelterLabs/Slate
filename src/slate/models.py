"""ORM models for every table.

Only `slate.service` writes these; see `slate/service/core.py` for the guard that enforces it.
Database-level rules that SQLite cannot express as column constraints (item depth <= 4,
append-only events) are triggers attached below and repeated verbatim in the migration.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from slate.db import Base
from slate.ids import new_event_id, new_id

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Aware UTC datetime stored as ISO 8601 text (`2026-10-23T17:00:00.000000Z`).

    Fixed width, so text order is time order. Naive datetimes are refused rather than guessed.
    """

    impl = String(27)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; Slate stores UTC-aware datetimes only")
        return value.astimezone(UTC).strftime(ISO_FORMAT)

    def process_result_value(self, value: str | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        # fromisoformat is ~10x faster than strptime and reads the stored `...Z` form exactly;
        # a list of 2,000 items parses 14,000 of these.
        return datetime.fromisoformat(value)


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
        length=16,
    )


class AccountKind(enum.StrEnum):
    person = "person"
    agent = "agent"


class Role(enum.StrEnum):
    """Workspace role (service/access.py). Owner and admin see and manage everything; a member
    sees every section and list that is not private; a guest sees only what is shared with them."""

    owner = "owner"
    admin = "admin"
    member = "member"
    guest = "guest"


class ShareLevel(enum.StrEnum):
    """How much a share grants on a section or list: view < comment < edit."""

    view = "view"
    comment = "comment"
    edit = "edit"


class Audience(enum.StrEnum):
    personal = "personal"
    ai = "ai"
    work = "work"
    family = "family"


class StatusKind(enum.StrEnum):
    open = "open"
    active = "active"
    done = "done"
    cancelled = "cancelled"


class AgentState(enum.StrEnum):
    """What an agent says it is doing on an item: a flag, not a status (AGENTS.md rule 4)."""

    working = "working"
    blocked = "blocked"
    review = "review"


class ItemKind(enum.StrEnum):
    task = "task"
    doc = "doc"


class Priority(enum.StrEnum):
    urgent = "urgent"
    high = "high"
    normal = "normal"
    low = "low"


class Source(enum.StrEnum):
    ui = "ui"
    api = "api"
    mcp = "mcp"
    system = "system"


class MissedPolicy(enum.StrEnum):
    spawn_always = "spawn_always"
    roll_forward = "roll_forward"
    skip_if_open = "skip_if_open"


class RecurTrigger(enum.StrEnum):
    """When a rule makes its next instance: on its dates, or when the current one is done."""

    schedule = "schedule"
    on_done = "on_done"


def _pk() -> Mapped[str]:
    return mapped_column(String(12), primary_key=True, default=new_id)


def _created() -> Mapped[datetime]:
    return mapped_column(UTCDateTime, default=utcnow)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = _pk()
    handle: Mapped[str] = mapped_column(String(32), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[AccountKind] = mapped_column(_enum(AccountKind, "account_kind"))
    created_at: Mapped[datetime] = _created()
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # How the board looks for this account (theme, folded columns per view). Kept here, not in
    # the browser, so every device shows the same board. See service.accounts.set_prefs.
    prefs_json: Mapped[Any] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    role: Mapped[Role] = mapped_column(
        _enum(Role, "account_role"), default=Role.member, server_default=Role.member.value
    )
    # How an agent is woken for a task (service/accounts.WAKE_MODES): `push` (a runner holds
    # its stream: Run now sends a hint), `pull` (its own product's scheduler polls Slate: Run
    # now nudges its owner), `manual` (a person carries it: Copy brief). Only the board reads it.
    wake: Mapped[str] = mapped_column(String(8), default="push", server_default="push")


class Token(Base):
    __tablename__ = "tokens"

    id: Mapped[str] = _pk()
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))  # sha256 hex of the full plaintext
    scopes: Mapped[str] = mapped_column(String(64))  # space-separated: read write admin
    label: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Optional narrowing on top of the account's own access (service/access.py):
    # `{"section_ids": [...], "list_ids": [...], "level": "view|comment|edit"}`. Null = the
    # account's full access. A token can never reach further than its account.
    limit_json: Mapped[Any | None] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    browser_session: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    # When the "your access ends soon" notice went out for the current `expires_at`
    # (service/sessions.py); an extension moves `expires_at` past it, so the notice re-arms.
    expiry_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[str] = _pk()
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    section_id: Mapped[str] = mapped_column(ForeignKey("sections.id"))
    secret_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    token_id: Mapped[str | None] = mapped_column(ForeignKey("tokens.id"))


class Section(Base):
    __tablename__ = "sections"

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    audience: Mapped[Audience] = mapped_column(_enum(Audience, "audience"))
    created_at: Mapped[datetime] = _created()
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    whiteboard_url: Mapped[str | None] = mapped_column(String(2048))
    # Private: members no longer see it by default; only owners, admins and `shares`.
    private: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))


class List(Base):
    __tablename__ = "lists"
    __table_args__ = (UniqueConstraint("section_id", "slug"),)

    id: Mapped[str] = _pk()
    section_id: Mapped[str] = mapped_column(ForeignKey("sections.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # lists <-> statuses is circular; SQLite checks FKs on DML, so creation order is free.
    default_status_id: Mapped[str | None] = mapped_column(
        ForeignKey("statuses.id", use_alter=True, name="fk_lists_default_status_id")
    )
    created_at: Mapped[datetime] = _created()
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Private: the section's access no longer reaches it; only owners, admins and `shares`.
    private: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    # `tasks` (a board of items) or `drawer` (a list of documents).
    # A drawer takes no items; its statuses exist but mean nothing yet.
    kind: Mapped[str] = mapped_column(String(8), default="tasks", server_default="tasks")


class Status(Base):
    __tablename__ = "statuses"
    __table_args__ = (UniqueConstraint("list_id", "name"),)

    id: Mapped[str] = _pk()
    list_id: Mapped[str] = mapped_column(ForeignKey("lists.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    kind: Mapped[StatusKind] = mapped_column(_enum(StatusKind, "status_kind"))
    color: Mapped[str | None] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, default=0)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("recurrence_id", "occurrence_at"),
        CheckConstraint("parent_id IS NULL OR parent_id != id", name="ck_items_not_own_parent"),
        Index(
            "ux_items_external_ref",
            "external_ref",
            unique=True,
            sqlite_where=text("external_ref IS NOT NULL"),
        ),
    )

    id: Mapped[str] = _pk()
    list_id: Mapped[str] = mapped_column(ForeignKey("lists.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("items.id"), index=True)
    kind: Mapped[ItemKind] = mapped_column(_enum(ItemKind, "item_kind"), default=ItemKind.task)
    title: Mapped[str] = mapped_column(String(500))
    body_md: Mapped[str] = mapped_column(Text, default="")
    status_id: Mapped[str | None] = mapped_column(ForeignKey("statuses.id"), index=True)
    priority: Mapped[Priority | None] = mapped_column(_enum(Priority, "priority"))
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    # The due date was given without a time (a bare YYYY-MM-DD). It is still stored as 17:00
    # workspace time that day, but the flag says so: the board hides the time, and an agent's
    # task runs at 06:00 that morning (dates.run_at). service/items.py sets it with due_at.
    due_date_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    waiting_on_account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    acceptance_md: Mapped[str | None] = mapped_column(Text)
    recurrence_id: Mapped[str | None] = mapped_column(ForeignKey("recurrences.id"))
    occurrence_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Unique when set (ux_items_external_ref): one card per thing a filer tracks.
    external_ref: Mapped[str | None] = mapped_column(String(200))
    # Due events: the last due_soon/due/overdue emitted, so a restart never repeats it.
    last_due_event: Mapped[str | None] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # ClickUp's "time estimate": planned effort in minutes. Not time tracking.
    estimate_minutes: Mapped[int | None] = mapped_column(Integer)
    # The latest agent report (`POST /items/{id}/report`): who is on it and how it is going.
    # A flag over the history, never a column of the board; every report is an `item.reported`
    # event. Cleared by a `done` or `idle` report.
    agent_state: Mapped[AgentState | None] = mapped_column(_enum(AgentState, "agent_state"))
    agent_state_by: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    agent_state_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    agent_note: Mapped[str | None] = mapped_column(String(280))
    agent_progress: Mapped[int | None] = mapped_column(Integer)
    # Where the agent is working (its chat, session or PR), shown as "Open" on the badge.
    # Set by a report or heartbeat with `url`, cleared with the flag.
    agent_url: Mapped[str | None] = mapped_column(String(2000))
    # The last "Run now" (item.run_requested) not yet taken: lets an agent take a task whose run
    # time has not come. Cleared by the take (service/work.py).
    run_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Since when it has waited on an open blocker (service.links.settle); null = not blocked.
    # Derived from links and the blockers' statuses, kept only so the change that clears or
    # sets it can record `item.unblocked` / `item.blocked`. Read `blocked` from the view.
    blocked_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # In the trash since then (service.trash). Trashing also archives (when not archived
    # already), so everything that hides archived items hides trashed ones; auto-purged after
    # `settings.trash_days`.
    trashed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)


class ItemAssignee(Base):
    __tablename__ = "item_assignees"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True, index=True)


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (CheckConstraint("name = lower(name)", name="ck_tags_name_lowercase"),)

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(60), unique=True)
    color: Mapped[str | None] = mapped_column(String(16))


class ItemTag(Base):
    __tablename__ = "item_tags"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[str] = _pk()
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    body_md: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # An assigned comment ("Ask"): a question or request for one account that stays open until
    # it answers (a reply on the item) or someone resolves it. service/comments.py.
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_by_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    # Doc groundwork (phase 2): anchor a comment to a span of a specific body version.
    anchor_version_id: Mapped[str | None] = mapped_column(ForeignKey("body_versions.id"))
    anchor_start: Mapped[int | None] = mapped_column(Integer)
    anchor_end: Mapped[int | None] = mapped_column(Integer)
    anchor_quote: Mapped[str | None] = mapped_column(Text)


class BodyVersion(Base):
    __tablename__ = "body_versions"
    __table_args__ = (UniqueConstraint("item_id", "version_no"),)

    id: Mapped[str] = _pk()
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    body_md: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()
    note: Mapped[str | None] = mapped_column(String(500))


class Event(Base):
    """Append-only change log. Written only by `slate.service.core`; never updated or deleted.

    `item_id` deliberately has no foreign key: history outlives its subject (an undo hard-delete
    of an item still leaves its `item.deleted` event), and a cascading FK would have to rewrite
    this table, which the append-only triggers forbid.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_item_id_at", "item_id", "at"),
        # Per-account audit and the Agents page: "what did this account do, newest first".
        Index("ix_events_actor_id_id", "actor_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_event_id)
    item_id: Mapped[str | None] = mapped_column(String(12))
    # The id of the row the event is about (account, token, list, section...;
    # equals item_id for item events). Without it an `account.updated` event cannot say which
    # account. The type prefix (`account.`, `token.`, `list.`) names the table. No FK, as above.
    subject_id: Mapped[str | None] = mapped_column(String(12), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    source: Mapped[Source] = mapped_column(_enum(Source, "event_source"))
    type: Mapped[str] = mapped_column(String(40), index=True)
    field: Mapped[str | None] = mapped_column(String(60))
    before_json: Mapped[Any | None] = mapped_column(JSON)
    after_json: Mapped[Any | None] = mapped_column(JSON)
    session_ref: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Recurrence(Base):
    __tablename__ = "recurrences"

    id: Mapped[str] = _pk()
    list_id: Mapped[str] = mapped_column(ForeignKey("lists.id"), index=True)
    title_template: Mapped[str] = mapped_column(String(500))
    body_template_md: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[Priority | None] = mapped_column(_enum(Priority, "priority"))
    tags_json: Mapped[Any | None] = mapped_column(JSON)
    assignee_ids_json: Mapped[Any | None] = mapped_column(JSON)
    rrule: Mapped[str] = mapped_column(Text)
    dtstart: Mapped[datetime] = mapped_column(UTCDateTime)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    lead_days: Mapped[int] = mapped_column(Integer, default=14)
    missed_policy: Mapped[MissedPolicy] = mapped_column(
        _enum(MissedPolicy, "missed_policy"), default=MissedPolicy.spawn_always
    )
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    paused_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # ClickUp's options: recur when done (vs on schedule), move weekend dates to Monday,
    # reopen the same task instead of making a new one, carry checklists over (unticked).
    trigger: Mapped[RecurTrigger] = mapped_column(
        _enum(RecurTrigger, "recur_trigger"), default=RecurTrigger.schedule
    )
    skip_weekends: Mapped[bool] = mapped_column(Boolean, default=False)
    reopen: Mapped[bool] = mapped_column(Boolean, default=False)
    copy_checklists: Mapped[bool] = mapped_column(Boolean, default=True)


class ScopeKind(enum.StrEnum):
    everything = "everything"
    section = "section"
    list = "list"


class View(Base):
    """A saved lens over items (docs/views.md). `config` is validated by api.views.ViewConfig
    before it is stored; the server keeps it opaque otherwise."""

    __tablename__ = "views"

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(100))
    scope_kind: Mapped[ScopeKind] = mapped_column(_enum(ScopeKind, "view_scope_kind"))
    scope_id: Mapped[str | None] = mapped_column(String(12))
    config_json: Mapped[Any] = mapped_column(JSON)
    position: Mapped[int] = mapped_column(Integer, default=0)
    owner_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    shared: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class LinkKind(enum.StrEnum):
    blocks = "blocks"
    relates = "relates"
    duplicates = "duplicates"


class ItemLink(Base):
    """`from_item` blocks (relates to, or is a duplicate of) `to_item`. Stored once; `relates`
    is symmetric. For `duplicates`, `from_item` is the copy and `to_item` the original."""

    __tablename__ = "item_links"
    __table_args__ = (
        UniqueConstraint("from_item_id", "to_item_id", "kind"),
        CheckConstraint("from_item_id != to_item_id", name="ck_item_links_not_self"),
    )

    id: Mapped[str] = _pk()
    from_item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), index=True
    )
    to_item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[LinkKind] = mapped_column(_enum(LinkKind, "link_kind"))
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()


class FavoriteKind(enum.StrEnum):
    list = "list"
    section = "section"
    view = "view"
    item = "item"
    doc = "doc"


class Favorite(Base):
    """One account's pinned list, section, saved view or item: a chip on the board's favorites
    bar. Per account, ordered by `position`; removing a favorite deletes the row (the event keeps
    the history). No FK to the target: it may be any of four tables, and an archived target keeps
    its favorite so a restore brings the chip back."""

    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("account_id", "kind", "target_id"),)

    id: Mapped[str] = _pk()
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    kind: Mapped[FavoriteKind] = mapped_column(_enum(FavoriteKind, "favorite_kind"))
    target_id: Mapped[str] = mapped_column(String(12))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[str] = _pk()
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    # Encrypted, not hashed: Slate signs deliveries with it (slate.crypto; plaintext shown once).
    secret_enc: Mapped[str] = mapped_column(Text)
    events_json: Mapped[Any] = mapped_column(JSON)
    filter_json: Mapped[Any | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[str] = _pk()
    webhook_id: Mapped[str] = mapped_column(
        ForeignKey("webhooks.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = _created()
    # One row per attempt. `pending` rows are the durable queue (sent at `next_at`);
    # `sending` is claimed; then `ok`, `retry` (a later attempt row exists), `failed` (final) or
    # `skipped` (subscription disabled before sending). `body` is the exact signed JSON.
    state: Mapped[str] = mapped_column(String(10), default="pending")
    next_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    body: Mapped[str] = mapped_column(Text, default="")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = _pk()
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    size: Mapped[int] = mapped_column(Integer)
    path: Mapped[str] = mapped_column(String(1000))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()


class AttachmentBlob(Base):
    """The bytes of an attachment, kept in the database so the nightly SQLite backup covers them
    (one file is simpler to back up and restore than files on disk). `Attachment.path` is
    `db:<id>` for these."""

    __tablename__ = "attachment_blobs"

    attachment_id: Mapped[str] = mapped_column(
        ForeignKey("attachments.id", ondelete="CASCADE"), primary_key=True
    )
    data: Mapped[bytes] = mapped_column(LargeBinary)


class CommentReaction(Base):
    """One emoji from one account on one comment (ClickUp-style reactions)."""

    __tablename__ = "comment_reactions"

    comment_id: Mapped[str] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    emoji: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = _created()


class Checklist(Base):
    """A named checklist inside an item: lightweight to-dos that are not subtasks."""

    __tablename__ = "checklists"

    id: Mapped[str] = _pk()
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[str] = _pk()
    checklist_id: Mapped[str] = mapped_column(
        ForeignKey("checklists.id", ondelete="CASCADE"), index=True
    )
    # Denormalised from the checklist so events and counts need no join.
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()


class Watcher(Base):
    """An account following an item: webhooks with `watching: true` hear about its changes."""

    __tablename__ = "item_watchers"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True, index=True)


class ItemRead(Base):
    """When one account last opened an item. Comments after it are new to that account: the
    board card's comment dot (service/item_reads.py). No row reads as the account's creation."""

    __tablename__ = "item_reads"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    read_at: Mapped[datetime] = mapped_column(UTCDateTime)


class InboxState(Base):
    """One account's notifications read state. Every inbox entry up to `read_through` (a
    time-ordered event id) is read and up to `cleared_through` is cleared; later ones are read
    or cleared one at a time in `inbox_marks`. The inbox itself is derived from `events`."""

    __tablename__ = "inbox_state"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    read_through: Mapped[str | None] = mapped_column(String(12))
    cleared_through: Mapped[str | None] = mapped_column(String(12))


class InboxMark(Base):
    """One inbox entry (an event) read or cleared by one account."""

    __tablename__ = "inbox_marks"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    cleared: Mapped[bool] = mapped_column(Boolean, default=False)


class Automation(Base):
    """A ClickUp-style automation rule: when `trigger` happens to an item in scope and every
    condition holds, run `actions` (service/automations.py has the vocabulary).

    Scope: `list_id`, else `section_id`, else every list. `trigger_json` is `{type, ...}`,
    `conditions_json` and `actions_json` are lists of `{type|field, ...}` dicts."""

    __tablename__ = "automations"

    id: Mapped[str] = _pk()
    name: Mapped[str] = mapped_column(String(120))
    list_id: Mapped[str | None] = mapped_column(ForeignKey("lists.id"), index=True)
    section_id: Mapped[str | None] = mapped_column(ForeignKey("sections.id"), index=True)
    trigger_json: Mapped[Any] = mapped_column(JSON)
    conditions_json: Mapped[Any] = mapped_column(JSON, default=list)
    actions_json: Mapped[Any] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Signs the `webhook` action's POSTs (X-Slate-Signature), encrypted like webhook secrets.
    # Made when the rule first gets a webhook action; shown once (create, update, rotate).
    secret_enc: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class AutomationRun(Base):
    """One run of a rule on one item: the run log. Bookkeeping (`core.BOOKKEEPING_MODELS`):
    the changes a run makes are ordinary events (actor `slate`, `session_ref automation:<id>`);
    this row only says the rule fired and how each action went."""

    __tablename__ = "automation_runs"
    __table_args__ = (Index("ix_automation_runs_automation_id_at", "automation_id", "at"),)

    id: Mapped[str] = _pk()
    automation_id: Mapped[str] = mapped_column(ForeignKey("automations.id", ondelete="CASCADE"))
    event_id: Mapped[str | None] = mapped_column(String(12))
    item_id: Mapped[str | None] = mapped_column(String(12), index=True)
    # The account whose change fired the rule, or who pressed "Run now".
    triggered_by: Mapped[str | None] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10))  # ok | partial | failed
    results_json: Mapped[Any] = mapped_column(JSON, default=list)
    at: Mapped[datetime] = _created()


class EngineCursor(Base):
    """Where a background engine (the automations runner) has read the event log up to, so a
    restart resumes rather than replays or skips. Bookkeeping, like deliveries."""

    __tablename__ = "engine_cursors"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(String(12))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Reminder(Base):
    """Remind one account about one item: at a fixed time (`remind_at`) or a number of minutes
    before the item's due date (`before_minutes`; follows the date when it moves). Firing records
    an `item.reminder` event (inbox, push, check-in, webhooks); `fired_for` is the instant it
    fired for, so it fires once per time and re-arms when a moved due date puts it ahead again."""

    __tablename__ = "reminders"

    id: Mapped[str] = _pk()
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    remind_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    before_minutes: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(280))
    fired_for: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()


class PushSubscription(Base):
    """One browser or installed app that receives Web Push for an account. `p256dh`/`auth` are
    the browser's keys for encrypting payloads (never in an event). `last_ok_at` and `failures`
    are bookkeeping (core.BOOKKEEPING)."""

    __tablename__ = "push_subscriptions"

    id: Mapped[str] = _pk()
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(2000), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    label: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created()
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failures: Mapped[int] = mapped_column(Integer, default=0)


class PushDelivery(Base):
    """Durable, bounded delivery state, without payloads or endpoint credentials.

    Subscription id is deliberately not a foreign key: terminal outcomes survive endpoint
    removal until retention cleanup. Account id fences a subscription moved to another login.
    Bookkeeping (`core.BOOKKEEPING_MODELS`): the table is the delivery log and the retry
    state, so service.push writes it without events (like webhook `deliveries`).
    """

    __tablename__ = "push_deliveries"

    subscription_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True, default="notification")
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class EmailDelivery(Base):
    """One event waiting to be emailed to one account (service/email.py), or the outcome.

    `send_after` is the event plus the unseen delay: at that point the event is emailed only if
    the account still has not seen it on the board. Bookkeeping (`core.BOOKKEEPING_MODELS`):
    the table is the delivery log and the retry state, like `push_deliveries`. No address,
    subject or body is stored; the address is read from the account's prefs at send time.
    """

    __tablename__ = "email_deliveries"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    send_after: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)


class Doc(Base):
    """A ClickUp-style doc page (service/docs.py). Pages nest without a depth limit beyond
    `docs.MAX_DEPTH`; only a top page (no parent) carries a location: a list, a section, or
    neither (the whole workspace). Subpages inherit their top page's location.

    `revision` goes up on every title or body change, so a writer can say which text it edited
    (`base_revision`) and get a 409 instead of overwriting someone else. `version_no` is the
    newest `doc_versions` row: autosaves by the same account within a few minutes fold into one
    version, so page history reads as sittings, not keystrokes. The event log keeps every save."""

    __tablename__ = "docs"
    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id != id", name="ck_docs_not_own_parent"),
        CheckConstraint("kind IN ('page', 'file', 'folder')", name="ck_docs_kind"),
    )

    id: Mapped[str] = _pk()
    # `page`: Markdown written in Slate. `file`: uploaded bytes (a PDF, a spreadsheet, a photo)
    # whose `doc_files` rows with a `revision_no` are its revisions; `body_md` is then a
    # description shown above the file. `folder`: a container in a drawer, nothing else. All
    # kinds sit in one tree.
    kind: Mapped[str] = mapped_column(String(8), default="page", server_default="page")
    # A file doc's pinned revision, the one in use, so a newer upload is never
    # mistaken for what is in use. Null = nothing pinned.
    pinned_revision_no: Mapped[int | None] = mapped_column(Integer)
    pinned_note: Mapped[str | None] = mapped_column(String(200))
    # Pinned to the top of its drawer or folder listing (service/docs.py `pin_to_top`): shared,
    # not per account. Null = not pinned. Distinct from the revision pin above.
    top_pinned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    top_pinned_by: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    title: Mapped[str] = mapped_column(String(200))
    icon: Mapped[str | None] = mapped_column(String(16))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("docs.id"), index=True)
    section_id: Mapped[str | None] = mapped_column(ForeignKey("sections.id"), index=True)
    list_id: Mapped[str | None] = mapped_column(ForeignKey("lists.id"), index=True)
    body_md: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()
    updated_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DocVersion(Base):
    """One sitting of edits to a doc: the title and body as they stood at its end."""

    __tablename__ = "doc_versions"
    __table_args__ = (UniqueConstraint("doc_id", "version_no"),)

    id: Mapped[str] = _pk()
    doc_id: Mapped[str] = mapped_column(ForeignKey("docs.id", ondelete="CASCADE"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    body_md: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    note: Mapped[str | None] = mapped_column(String(200))


class DocFile(Base):
    """An image (or any file) uploaded into a doc, served at `/doc-files/{id}`. The bytes stay in
    the database like attachment blobs, so the nightly backup covers them. Events about a file
    carry its metadata, never `data`.

    On a `file` doc the rows with a `revision_no` are its revisions, 1 upward: each upload adds
    the next one and stamps the previous `superseded_at`; nothing is deleted. `note` says what
    changed (required on upload). `restored_from` names the revision a restore copied."""

    __tablename__ = "doc_files"
    __table_args__ = (UniqueConstraint("doc_id", "revision_no"),)

    id: Mapped[str] = _pk()
    doc_id: Mapped[str] = mapped_column(ForeignKey("docs.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()
    revision_no: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(200))
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    restored_from: Mapped[int | None] = mapped_column(Integer)
    # Text extracted once on upload (text files as they are, spreadsheets cell by cell, PDFs
    # through pypdf): what search matches and what a PDF compare diffs. None when the type has
    # no text (images, archives, CAD).
    text: Mapped[str | None] = mapped_column(Text, deferred=True)
    # A CSV/TSV/XLSX's tables as `[[sheet name, rows], ...]`, read once on upload beside the
    # text, so comparing two revisions never parses the files again. None for other types,
    # for an upload over `SLATE_DOC_EXTRACT_MAX_BYTES`, and for revisions uploaded before the
    # column existed (`slate backfill-doc-text` fills those).
    tables_json: Mapped[Any | None] = mapped_column(JSON, deferred=True)


class DocTag(Base):
    """A tag on a doc (page or file): the same `tags` tasks use, so one word cuts across both."""

    __tablename__ = "doc_tags"

    doc_id: Mapped[str] = mapped_column(ForeignKey("docs.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class DocComment(Base):
    """A comment on a doc (service/doc_threads.py): a page or a file. Two surfaces in one table:
    a *review* thread is anchored (a span of the page's text, a page number, a spot on an image
    or a PDF page) and can be resolved; *discussion* is the unanchored conversation along the
    side. Replies carry `parent_id` (one level). Every comment is stamped with
    `anchor_revision`, the page-history version or the file revision current when it was
    written, so it is never read against text or a drawing it was not about. `suggestion_md`
    on a review comment proposes a replacement for the quoted span; Accept applies it.
    `assignee_id` makes it an Ask for that account, open until they reply or it is resolved."""

    __tablename__ = "doc_comments"
    __table_args__ = (
        CheckConstraint("surface IN ('review', 'discussion')", name="ck_doc_comments_surface"),
    )

    id: Mapped[str] = _pk()
    doc_id: Mapped[str] = mapped_column(ForeignKey("docs.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("doc_comments.id", ondelete="CASCADE"), index=True
    )
    surface: Mapped[str] = mapped_column(String(10))
    author_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    body_md: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    anchor_revision: Mapped[int] = mapped_column(Integer)
    anchor_page_revision: Mapped[int | None] = mapped_column(Integer)
    anchor_start: Mapped[int | None] = mapped_column(Integer)
    anchor_end: Mapped[int | None] = mapped_column(Integer)
    anchor_quote: Mapped[str | None] = mapped_column(Text)
    anchor_page: Mapped[int | None] = mapped_column(Integer)
    anchor_x: Mapped[float | None] = mapped_column(Float)
    anchor_y: Mapped[float | None] = mapped_column(Float)
    suggestion_md: Mapped[str | None] = mapped_column(Text)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_by_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))


# --- Triggers: rules SQLite cannot hold as column constraints. -----------------------------
class Message(Base):
    """One chat message (service/chat.py). `channel` names where it was posted: `general`,
    `list:<list id>` or `section:<section id>`; there is no channels table, a list's channel is
    the list. Ids are time-ordered (like event ids), so `ORDER BY id` is posting order and the id
    is the read cursor. A reply has `parent_id` (the thread's first message; threads are one
    level deep). `mentions_json` holds the handles `@mentioned` in the body, kept in step with
    edits. `item_id` is the task made from the message ("turn into task"). Deleting blanks the
    body and stamps `deleted_at`; the row stays so its thread holds together. `members_only`:
    hidden from guests (service/chat.py `members_only_post`): a workspace agent's post, a
    member's post that @mentions one, and every reply in such a thread."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_channel_id", "channel", "id"),
        Index("ix_messages_parent_id", "parent_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=new_event_id)
    channel: Mapped[str] = mapped_column(String(40))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    author_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    body_md: Mapped[str] = mapped_column(Text)
    mentions_json: Mapped[Any] = mapped_column(JSON, default=list)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    members_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("0"))


class MessageReaction(Base):
    """One emoji from one account on one chat message."""

    __tablename__ = "message_reactions"

    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    emoji: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = _created()


class ChannelRead(Base):
    """Private per-account read state: intentional cursor plus exact exposures beyond it.
    An empty cursor reads no prefix; no row means nothing read yet."""

    __tablename__ = "channel_reads"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), primary_key=True)
    read_through: Mapped[str] = mapped_column(String(12))
    exposed_ids: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")


class Share(Base):
    """One account's access to one section or list (service/access.py). A share only ever adds
    access: a list share reaches into a private list; a section share covers the section's
    lists that are not private themselves."""

    __tablename__ = "shares"
    __table_args__ = (UniqueConstraint("account_id", "scope_kind", "scope_id"),)

    id: Mapped[str] = _pk()
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    scope_kind: Mapped[str] = mapped_column(String(16))  # section | list
    scope_id: Mapped[str] = mapped_column(String(12), index=True)
    level: Mapped[ShareLevel] = mapped_column(_enum(ShareLevel, "share_level"))
    created_by: Mapped[str] = mapped_column(ForeignKey("accounts.id"))
    created_at: Mapped[datetime] = _created()


# Kept as plain SQL so the migration repeats them verbatim (alembic does not autogenerate
# triggers). If you change one here, change it in the migration too.

TRIGGERS: dict[str, list[str]] = {
    "events": [
        """CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
        """CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END""",
    ],
    "items": [
        """CREATE TRIGGER items_depth_insert BEFORE INSERT ON items
WHEN NEW.parent_id IS NOT NULL AND (
  (SELECT p4.parent_id FROM items p1
     JOIN items p2 ON p2.id = p1.parent_id
     JOIN items p3 ON p3.id = p2.parent_id
     JOIN items p4 ON p4.id = p3.parent_id
    WHERE p1.id = NEW.parent_id) IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'item depth exceeds 4: the parent is already four levels down'); END""",
        """CREATE TRIGGER items_depth_update BEFORE UPDATE OF parent_id ON items
WHEN NEW.parent_id IS NOT NULL AND (
  NEW.id IN (
    SELECT id FROM items WHERE id = NEW.parent_id
    UNION SELECT a2.id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
      WHERE a1.id = NEW.parent_id
    UNION SELECT a3.id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
      JOIN items a3 ON a3.id = a2.parent_id WHERE a1.id = NEW.parent_id
    UNION SELECT a4.id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
      JOIN items a3 ON a3.id = a2.parent_id JOIN items a4 ON a4.id = a3.parent_id
      WHERE a1.id = NEW.parent_id)
  OR (SELECT a4.parent_id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
        JOIN items a3 ON a3.id = a2.parent_id JOIN items a4 ON a4.id = a3.parent_id
       WHERE a1.id = NEW.parent_id) IS NOT NULL
  OR ((SELECT a3.parent_id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
         JOIN items a3 ON a3.id = a2.parent_id WHERE a1.id = NEW.parent_id) IS NOT NULL
      AND EXISTS (SELECT 1 FROM items WHERE parent_id = NEW.id))
  OR ((SELECT a2.parent_id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id
        WHERE a1.id = NEW.parent_id) IS NOT NULL
      AND EXISTS (SELECT 1 FROM items c1 JOIN items c2 ON c2.parent_id = c1.id
                   WHERE c1.parent_id = NEW.id))
  OR ((SELECT parent_id FROM items WHERE id = NEW.parent_id) IS NOT NULL
      AND EXISTS (SELECT 1 FROM items c1 JOIN items c2 ON c2.parent_id = c1.id
                   JOIN items c3 ON c3.parent_id = c2.id WHERE c1.parent_id = NEW.id))
  OR EXISTS (SELECT 1 FROM items c1 JOIN items c2 ON c2.parent_id = c1.id
              JOIN items c3 ON c3.parent_id = c2.id JOIN items c4 ON c4.parent_id = c3.id
             WHERE c1.parent_id = NEW.id))
BEGIN SELECT RAISE(ABORT, 'item depth exceeds 4: a cycle, or a tree nesting deeper than four');
END""",
    ],
}

for _table, _statements in TRIGGERS.items():
    for _sql in _statements:
        event.listen(
            Base.metadata.tables[_table], "after_create", DDL(_sql).execute_if(dialect="sqlite")
        )


class Capacity(Base):
    """How much one account (a person or an agent) can take on, for the Box view
    (service/workload.py). One row per account, made the first time it is set; until then
    `workload.DEFAULTS` apply.

    `hours_per_day` measures time estimates, `tasks_per_day` plain task counts; null means no
    limit in that unit.
    `work_days` lists the weekdays it works, 0 = Monday, as `"0,1,2,3,4"`.
    """

    __tablename__ = "capacities"

    id: Mapped[str] = _pk()
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), unique=True)
    hours_per_day: Mapped[float | None] = mapped_column(Float)
    tasks_per_day: Mapped[int | None] = mapped_column(Integer)
    work_days: Mapped[str] = mapped_column(String(13), default="0,1,2,3,4")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class WorkLease(Base):
    """An agent's hold on a task it took with `POST /work/next` (service/work.py): who, since
    when, until when. The take itself is an `item.reported` (working) event; the lease is the
    timer that lets an abandoned take go back. A heartbeat moves `until` with no event, like
    `Token.last_used_at` (core.BOOKKEEPING_MODELS); a scheduler sweep releases expired leases
    with an `item.reported` idle event by `system`. `from_pool`: the take also assigned the
    agent (the task had no assignee), so a release un-assigns it again."""

    __tablename__ = "work_leases"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    claimed_at: Mapped[datetime] = _created()
    until: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    from_pool: Mapped[bool] = mapped_column(Boolean, default=False)
    run_id: Mapped[str | None] = mapped_column(String(64))
    asked_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class ChatWake(Base):
    """An agent's pending or open turn in one chat channel or thread (service/chatwake.py): a
    person @mentioned it there, so its runner has work to take. One row per (agent, channel,
    thread); `thread_id` is "" for the channel itself (NULL cannot be part of the key).
    `message_id` is the newest mention not yet answered and `since_id` the oldest; a take sets
    `taken_id` (the mentions up to it are this turn's), `claimed_at` and `until`, the lease a
    heartbeat moves as bookkeeping (core.BOOKKEEPING). Made, taken and ended inside events
    (`message.mentioned_agent`, `message.agent_turn`), never a status on anything."""

    __tablename__ = "chat_wakes"
    run_id: Mapped[str | None] = mapped_column(String(64))

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(12), primary_key=True, default="")
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    since_id: Mapped[str] = mapped_column(String(12))
    taken_id: Mapped[str | None] = mapped_column(String(12))
    created_at: Mapped[datetime] = _created()
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    until: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)


class DocWake(Base):
    """An agent's pending or open turn on one doc (service/docwake.py): a person @mentioned it
    on the page or in one of its comment threads, so its runner has work to take. One row per
    (agent, doc, thread); `thread_id` is the thread's first comment, or "" for a mention in the
    page body (the answer then starts a new discussion thread). `mention_no` counts the
    mentions so far and a take sets `taken_no` (the mentions up to it are this turn's);
    `comment_id`, `excerpt` and `by_id` are the newest mention's. `claimed_at`, `until` and
    `run_id` are the lease, as on `ChatWake`; a heartbeat moves `until` as bookkeeping. Made,
    taken and ended inside events (`doc.mentioned_agent`, `doc.agent_turn`)."""

    __tablename__ = "doc_wakes"

    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("docs.id", ondelete="CASCADE"), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(12), primary_key=True, default="")
    comment_id: Mapped[str | None] = mapped_column(String(12))
    by_id: Mapped[str] = mapped_column(String(12))
    excerpt: Mapped[str] = mapped_column(String(200), default="")
    mention_no: Mapped[int] = mapped_column(Integer, default=1)
    taken_no: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    until: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)


class AgentThread(Base):
    """An agent's own conversation for one item, as the agent's runtime knows it: an opaque
    `ref` (its session or thread id) and an optional `url` to open it. One per (item, agent).
    Slate attaches no meaning to either; the agent's adapter stores it with a report, heartbeat
    or finish (`thread: {ref, url}`) and reads it back from `/work/next` to continue the same
    session on the next turn. Written inside the `item.reported` change that carries it."""

    __tablename__ = "agent_threads"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    ref: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(2000))
    updated_at: Mapped[datetime] = _created()


class WorkspaceSetting(Base):
    """One workspace-wide setting, by key, as JSON. The only key so far is `agents.denied`:
    the actions agent accounts may never take, whatever their token (service/agent_rules.py).
    Changed only through `apply_change()`, as a `settings.changed` event."""

    __tablename__ = "workspace_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
