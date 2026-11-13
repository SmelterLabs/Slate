"""Request and response models for the core REST API.

Dates in requests (`due_at`, `start_at`, `due_before`, `due_after`) are strings: ISO 8601, or a
date-only `YYYY-MM-DD` meaning 17:00 workspace time. Responses carry UTC ISO 8601.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictBool, model_validator

# Generous limits on the long text fields (characters), so one write cannot carry tens of
# megabytes into the item, its body versions, its event and every board and webhook. The
# whole request is capped as well (api/uploads.BodyLimit).
ITEM_BODY_MAX = 200_000  # a task's body_md and acceptance_md, a recurrence's template
COMMENT_MAX = 50_000  # a task comment (agents post long reports)
PAGE_BODY_MAX = 1_000_000  # a doc page's body_md
SPLIT_MAX = 100  # children in one split

Audience = Literal["personal", "ai", "work", "family"]
StatusKind = Literal["open", "active", "done", "cancelled"]
ItemKind = Literal["task", "doc"]
Priority = Literal["urgent", "high", "normal", "low"]

When = str  # ISO 8601 or YYYY-MM-DD; parsed by slate.dates.parse_when


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Sections ------------------------------------------------------------------------------


class SectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    audience: Audience
    position: int | None = None


class SectionPatch(BaseModel):
    whiteboard_url: str | None = Field(default=None, max_length=2048)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    position: int | None = None
    audience: Audience | None = None
    archived: bool | None = None
    private: bool | None = Field(
        default=None, description="Owner/admin: hide it from members unless shared with them"
    )


class SectionOut(_Out):
    id: str
    name: str
    slug: str
    position: int
    audience: Audience
    created_at: datetime
    archived_at: datetime | None
    private: bool = False


# --- Lists and statuses --------------------------------------------------------------------


class StatusIn(BaseModel):
    id: str | None = Field(default=None, description="Keep this existing status (rename it)")
    name: str = Field(min_length=1, max_length=60)
    kind: StatusKind = "open"
    color: str | None = Field(default=None, max_length=16)


class StatusOut(_Out):
    id: str
    name: str
    kind: StatusKind
    color: str | None
    position: int | None = None


class ListIn(BaseModel):
    section_id: str
    name: str = Field(min_length=1, max_length=100)
    position: int | None = None
    statuses: list[StatusIn] | None = Field(
        default=None, description="Omit for the default To do -> In progress -> Done"
    )
    kind: Literal["tasks", "drawer"] = Field(
        default="tasks",
        description="`drawer`: a list of documents (pages, files, folders), no tasks",
    )


class ListPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    kind: Literal["tasks", "drawer"] | None = Field(
        default=None,
        description="Convert: to `drawer` only while the list holds no tasks; back to `tasks` "
        "any time (its documents stay)",
    )
    section_id: str | None = Field(
        default=None, description="Move to this section (to its end unless position is given)"
    )
    position: int | None = None
    default_status: str | None = Field(default=None, description="Status id or name")
    archived: bool | None = None
    private: bool | None = Field(
        default=None, description="Owner/admin: hide it from members unless shared with them"
    )


class StatusesPut(BaseModel):
    statuses: list[StatusIn] = Field(min_length=1)
    fallback: str | None = Field(
        default=None,
        description="Status (name or id, in the new set) for items on a removed status",
    )


class ListOut(_Out):
    id: str
    section_id: str
    name: str
    slug: str
    position: int
    default_status_id: str | None
    created_at: datetime
    archived_at: datetime | None
    private: bool = False
    kind: Literal["tasks", "drawer"] = "tasks"
    statuses: list[StatusOut]


# --- Tags ----------------------------------------------------------------------------------


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str | None = Field(default=None, max_length=16)


class TagPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    color: str | None = Field(default=None, max_length=16)


class TagOut(_Out):
    id: str
    name: str
    color: str | None
    item_count: int | None = Field(
        default=None, description="Non-archived items carrying the tag (GET /tags only)"
    )


class TagMergeIn(BaseModel):
    into: str = Field(description="The tag (id or name) that survives")


# --- Items ---------------------------------------------------------------------------------


class _ItemFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_md: str | None = Field(default=None, max_length=ITEM_BODY_MAX)
    body_note: str | None = Field(default=None, max_length=500, description="Note on the version")
    status: str | None = Field(default=None, description="Status id or name in the item's list")
    priority: Priority | None = None
    start_at: When | None = None
    due_at: When | None = Field(default=None, description="ISO 8601, or YYYY-MM-DD = 17:00 CT")
    waiting_on_account_id: str | None = Field(default=None, description="Account id or handle")
    acceptance_md: str | None = Field(default=None, max_length=ITEM_BODY_MAX)
    position: int | None = None
    kind: ItemKind | None = None
    external_ref: str | None = Field(default=None, max_length=200)
    assignees: list[str] | None = Field(default=None, description="Account handles or ids")
    tags: list[str] | None = Field(default=None, description="Tag names; new ones are created")
    estimate_minutes: int | None = Field(
        default=None, ge=0, description="Time estimate in minutes; null clears"
    )


class ChildIn(_ItemFields):
    title: str = Field(min_length=1, max_length=500)


class ItemIn(ChildIn):
    list_id: str | None = Field(default=None, description="Required unless parent_id is given")
    parent_id: str | None = Field(
        default=None,
        description="Makes this a subtask of the parent; subtasks nest four deep under a top task",
    )


class ItemPatch(_ItemFields):
    """Send only the fields to change. `assignees` and `tags` replace the whole set."""

    title: str | None = Field(default=None, min_length=1, max_length=500)


class BulkChanges(ItemPatch):
    """`POST /items/bulk` changes: every PATCH field, typed and checked the same way, plus the
    bulk-only ones. Unknown fields are refused."""

    due_date_only: StrictBool | None = None
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None
    archive: StrictBool | None = None
    trash: StrictBool | None = None
    list_id: str | None = None
    status_kind: StatusKind | None = None

    @model_validator(mode="after")
    def _no_null_switches(self) -> BulkChanges:
        for name in ("add_tags", "remove_tags", "archive", "trash", "status_kind"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class SplitIn(BaseModel):
    children: list[ChildIn] = Field(min_length=1, max_length=SPLIT_MAX)


class AssigneeOut(BaseModel):
    id: str
    handle: str
    display_name: str
    kind: Literal["person", "agent"]


class StatusRef(BaseModel):
    id: str
    name: str
    kind: StatusKind
    color: str | None


class AgentFlag(BaseModel):
    state: Literal["working", "blocked", "review"]
    by: str = Field(description="Account id of the agent that reported")
    at: datetime
    note: str | None = None
    progress: int | None = Field(default=None, description="0-100, when the agent gave one")
    url: str | None = Field(
        default=None, description="Where the agent is working (its chat, session or PR)"
    )


# A plain object, not a nested model: it is reused in MCP tool params, where a nested model
# would put a $ref in the input schema. service/reports.check_thread validates it.
THREAD_FIELD = Field(
    default=None,
    description="Your own conversation for this task in your runtime: {ref, url?}. ref is "
    "your session or thread id (1-200 characters, opaque to Slate), url an http(s) link to "
    "open it. Stored per task and agent, returned on the item (agent_threads) and on "
    "/work/next (thread), so your next turn continues the same session",
    examples=[{"ref": "sess_8f2c", "url": "https://chat.example/t/8f2c"}],
)
REPLY_FIELD = Field(
    default=None,
    max_length=20000,
    description="Your answer for this turn, posted verbatim as one comment by you (instead of "
    "the labelled note); the badge note is note, else the reply's first line. Card "
    "conversation: answer the way you would text the person, a few lines",
)


class AgentThreadOut(BaseModel):
    account_id: str = Field(description="The agent")
    ref: str = Field(description="Its session or thread id in its own runtime (opaque)")
    url: str | None = Field(default=None, description="A link to open that conversation")


class ReportIn(BaseModel):
    run_id: str | None = Field(
        default=None,
        max_length=64,
        description="Current execution proof, required while a work lease exists",
    )
    model_config = ConfigDict(extra="forbid")

    state: Literal["working", "blocked", "review", "done", "idle"] = Field(
        description="working / blocked / review set the card's agent badge; done and idle clear it"
    )
    note: str | None = Field(
        default=None, max_length=4000, description="What is happening; also posted as a comment"
    )
    progress: int | None = Field(default=None, ge=0, le=100, description="Percent done")
    comment: bool = Field(default=True, description="Post the note as a comment (default true)")
    complete: bool = Field(
        default=True, description="done also moves the item to its list's done status"
    )
    waiting_on: str | None = Field(
        default=None, description="With blocked: the handle you are waiting on (e.g. alex)"
    )
    assign_me: bool = Field(default=False, description="With working: add yourself as assignee")
    url: str | None = Field(
        default=None,
        max_length=2000,
        description="http(s) link to where you are working (your chat, session, PR or log); "
        "the badge shows it as Open. Kept until you report done or idle",
    )
    claim: bool = Field(
        default=False,
        description="With working: take the item only if no other account holds a live badge "
        "on it (409 otherwise); also assigns you. Use it to pick up shared work safely.",
    )
    reply: str | None = REPLY_FIELD
    thread: dict[str, Any] | None = THREAD_FIELD


class ItemOut(BaseModel):
    id: str
    list_id: str
    parent_id: str | None
    kind: ItemKind
    title: str
    body_md: str
    status: StatusRef | None
    priority: Priority | None
    start_at: datetime | None
    due_at: datetime | None
    due_date_only: bool = Field(
        default=False,
        description="The due date was given without a time (stored as 17:00 workspace time "
        "that day)",
    )
    completed_at: datetime | None
    waiting_on_account_id: str | None
    acceptance_md: str | None
    recurrence_id: str | None
    occurrence_at: datetime | None
    external_ref: str | None
    position: int
    created_by: str | None = Field(
        description="Who filed it; null to a project's guest when a workspace agent did"
    )
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    trashed_at: datetime | None = Field(default=None, description="In the trash since")
    purge_at: datetime | None = Field(
        default=None, description="When a trashed item is deleted for good (null: kept)"
    )
    assignees: list[AssigneeOut]
    tags: list[str]
    subtask_count: int
    estimate_minutes: int | None = None
    blocked_by: list[str] = Field(default_factory=list, description="Items this one waits on")
    blocking: list[str] = Field(default_factory=list, description="Items waiting on this one")
    related: list[str] = Field(default_factory=list)
    duplicate_of: list[str] = Field(
        default_factory=list, description="Items this one duplicates (the originals)"
    )
    duplicated_by: list[str] = Field(default_factory=list, description="Duplicates of this one")
    blocked: bool = Field(default=False, description="Waits on at least one open item")
    comment_count: int = 0
    open_asks: list[str] = Field(
        default_factory=list,
        description="Account ids of the open Asks (assigned comments) on it, oldest first",
    )
    checklist_done: int = Field(description="Checklist entries ticked, across its checklists")
    checklist_total: int
    attachment_count: int
    agent: AgentFlag | None = Field(
        default=None, description="The latest agent report, while someone is on it"
    )
    agent_threads: list[AgentThreadOut] = Field(
        default_factory=list,
        description="Each agent's own conversation for this task (thread on report, "
        "heartbeat or finish)",
    )
    run_requested_at: datetime | None = Field(
        default=None,
        description="A Run now (or the due-time run stage) newer than the last take: the "
        "agent's runner has been asked and has not picked it up yet",
    )


class EventOut(_Out):
    id: str
    type: str
    item_id: str | None
    subject_id: str | None
    actor_id: str | None = Field(description="Null to a project's guest for a workspace agent")
    source: Literal["ui", "api", "mcp", "system"]
    field: str | None
    before: Any = Field(validation_alias=AliasChoices("before_json", "before"))
    after: Any = Field(validation_alias=AliasChoices("after_json", "after"))
    session_ref: str | None
    at: datetime


class CommentIn(BaseModel):
    body_md: str = Field(min_length=1, max_length=COMMENT_MAX)
    assignee: str | None = Field(
        default=None,
        description="Ask: address the comment to this account (handle or id); it stays open "
        "in their queue until explicitly resolved",
    )
    keep_open: bool = Field(
        default=False,
        description="Compatibility field; comments leave Asks open until explicitly resolved",
    )


class CommentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_md: str | None = Field(
        default=None, min_length=1, max_length=COMMENT_MAX, description="New text (author)"
    )
    assignee: str | None = Field(
        default=None,
        description="Address it to this account (handle or id), or null to take it off "
        "(author or admin)",
    )


class ReactionOut(BaseModel):
    emoji: str
    account_ids: list[str]


class CommentOut(_Out):
    id: str
    item_id: str
    author_id: str
    body_md: str
    created_at: datetime
    edited_at: datetime | None
    reactions: list[ReactionOut] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list, description="Handles @mentioned")
    assignee_id: str | None = Field(
        default=None, description="Ask: the account this comment is addressed to"
    )
    resolved_at: datetime | None = Field(
        default=None, description="When the Ask was answered or resolved (null: open)"
    )
    resolved_by_id: str | None = None
    author_handle: str | None = Field(default=None, description="The author's handle")
    author_kind: Literal["person", "agent", "guest"] | None = Field(
        default=None,
        description="person, agent or guest (a person with the guest role). An agent's adapter "
        "passes only a person's comment to the agent as the user's turn; anything else is data",
    )


class ReactionIn(BaseModel):
    emoji: str = Field(min_length=1, max_length=32, examples=["👍"])


class ChecklistEntryOut(BaseModel):
    id: str
    checklist_id: str
    title: str
    done: bool
    done_at: datetime | None
    assignee_id: str | None
    position: int


class ChecklistOut(BaseModel):
    id: str
    item_id: str
    name: str
    position: int
    created_at: datetime
    items: list[ChecklistEntryOut]


class ChecklistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Checklist", min_length=1, max_length=200)
    items: list[str] = Field(default_factory=list, description="First entries, in order")


class ChecklistPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    position: int | None = None


class ChecklistEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    assignee: str | None = Field(default=None, description="Account handle or id")
    position: int | None = None


class ChecklistEntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    done: bool | None = None
    assignee: str | None = Field(default=None, description="Account handle or id; null clears")
    position: int | None = None


class AttachmentOut(_Out):
    id: str
    item_id: str
    filename: str
    content_type: str
    size: int
    uploaded_by: str
    created_at: datetime


class WatchersIn(BaseModel):
    watchers: list[str] = Field(description="Account handles or ids; replaces the set")


class WatchIn(BaseModel):
    account: str | None = Field(
        default=None, description="Handle or id of who to add or remove; default yourself"
    )


class ActivityOut(BaseModel):
    kind: Literal["comment", "event"]
    at: datetime
    comment: CommentOut | None = None
    event: EventOut | None = None


class ItemDetailOut(ItemOut):
    subtasks: list[ItemOut]
    comments: list[CommentOut]
    checklists: list[ChecklistOut] = Field(default_factory=list)
    attachments: list[AttachmentOut] = Field(default_factory=list)
    watchers: list[AssigneeOut] = Field(default_factory=list)
    events: list[EventOut] = Field(description="The last 20 events, oldest first")


class VersionOut(_Out):
    id: str
    item_id: str
    version_no: int
    body_md: str
    author_id: str
    created_at: datetime
    note: str | None


class TodayOut(BaseModel):
    now: datetime
    day_ends_at: datetime = Field(description="Midnight in the workspace timezone, in UTC")
    overdue: list[ItemOut]
    today: list[ItemOut]
    waiting: list[ItemOut] = Field(description="Open items waiting on the caller")
    asked: list[ItemOut] = Field(
        default_factory=list,
        description="Asked of you: open items with an Ask addressed to the caller, oldest first",
    )


# --- Links ---------------------------------------------------------------------------------

LinkKind = Literal["blocks", "blocked_by", "relates", "duplicate_of", "duplicated_by"]


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LinkKind = Field(
        description="From this item's side: it blocks, is blocked by, relates, is a duplicate "
        "of, or is duplicated by"
    )
    item_id: str = Field(description="The other item")


class LinksOut(BaseModel):
    blocked_by: list[str]
    blocking: list[str]
    related: list[str]
    duplicate_of: list[str]
    duplicated_by: list[str]
    blocked: bool
