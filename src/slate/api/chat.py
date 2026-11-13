"""/channels and /messages: chat per list and section, General, and each agent's own chat
(service/chat.py), and the files on messages (/channels/{key}/files, /message-files/{id};
service/chat_files.py).

A file is uploaded like an attachment: the raw file as the request body (`Content-Type` is the
file's type) with the name in `?filename=`, or base64 in JSON for clients that can only send
JSON. The answer's `id` then goes in a message's `file_ids`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from slate.api.attachments import Base64In
from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import ItemOut, ReactionIn, ReactionOut
from slate.api.uploads import file_response, read_body
from slate.service import attachments, chat, chat_files, items

router = APIRouter(tags=["chat"])

KEY_DOC = "`general`, `list:<list id>`, `section:<section id>` or `agent:<agent account id>`"


class AgentTurnOut(BaseModel):
    agent_id: str = Field(description="The agent account answering")
    thread_id: str | None = Field(description="The thread's first message; null for the channel")
    state: Literal["waiting", "working", "expired"] = Field(
        description="`waiting`: mentioned, its runner has not taken the turn yet; `working`: "
        "taken (`message.agent_turn` started) and its lease is live; `expired`: the lease ran "
        "out and the turn waits for its runner to reconcile, or for a person's "
        "`POST /work/chat/clear`"
    )
    since: datetime = Field(description="When the mention woke it, or when the turn was taken")


class ChannelOut(BaseModel):
    key: str = Field(description=f"The channel: {KEY_DOC}")
    kind: Literal["general", "list", "section", "agent"]
    name: str
    list_id: str | None
    section_id: str | None
    account_id: str | None = Field(
        default=None,
        description="The channel agent. Workspace agents are implicitly addressed; guest-role "
        "project agents require an explicit @mention.",
    )
    mention_required: bool = Field(
        default=True,
        description="True for shared project-agent and ordinary channels: explicitly @mention "
        "an agent to address it. False for workspace-agent direct chats (implicit addressing).",
    )
    position: int = Field(
        description="Board order: General, each agent's chat, then each section and its lists"
    )
    unread: int = Field(description="Messages by others you have not read, replies included")
    mentions: int = Field(description="Unread messages that @mention you")
    last_message_id: str | None
    last_message_at: datetime | None
    first_unread_id: str | None = None
    first_unread_thread_id: str | None = None
    unread_threads: dict[str, int] = Field(default_factory=dict)
    agent_turns: list[AgentTurnOut] = Field(
        default_factory=list,
        description="Agents mentioned here whose answer is still to come, so a board that loads "
        "mid-turn shows who is working; kept current by `message.mentioned_agent` and "
        "`message.agent_turn`",
    )


class LinkedItemOut(BaseModel):
    id: str
    title: str
    list_id: str
    due_at: datetime | None
    status: str | None
    done: bool


class MessageFileOut(BaseModel):
    id: str = Field(description="Read the bytes at `GET /message-files/{id}`")
    filename: str
    content_type: str
    size: int


class MessageOut(BaseModel):
    id: str = Field(description="Time-ordered: also the read cursor")
    channel: str
    parent_id: str | None = Field(description="The thread's first message, for a reply")
    author_id: str
    body_md: str = Field(description="Empty once deleted, or when the message is files alone")
    mentions: list[str] = Field(description="Handles @mentioned")
    item_id: str | None = Field(description="The task made from this message")
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None
    members_only: bool = Field(
        False,
        description="Hidden from guests: a workspace agent's post, a member's post that "
        "@mentions one, or a reply in such a thread. A guest never receives one.",
    )
    reactions: list[ReactionOut]
    reply_count: int
    last_reply_at: datetime | None
    reply_author_ids: list[str] = Field(description="Who replied, latest first (at most 5)")
    linked_items: list[LinkedItemOut] = Field(
        description="Items the message names (`?task=<id>` links, `#<id>`) or was made into"
    )
    files: list[MessageFileOut] = Field(
        default_factory=list,
        description="Files sent with the message (a screenshot, a PDF): read each at "
        "`GET /message-files/{id}`",
    )


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_md: str = Field(
        default="", max_length=chat.MAX_BODY, description="The text; may be empty with file_ids"
    )
    parent_id: str | None = Field(default=None, description="Reply in this message's thread")
    file_ids: list[str] = Field(
        default_factory=list,
        max_length=chat_files.MAX_FILES,
        description="Your unsent uploads to this channel (`POST /channels/{key}/files`) to "
        "send with the message",
    )


class MessagePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_md: str = Field(
        max_length=chat.MAX_BODY, description="Empty only for a message that has files"
    )


class ReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    through: str | None = Field(default=None, description="Message id; default the newest")
    message_ids: list[str] | None = Field(
        default=None,
        max_length=200,
        description="Exact exposed messages only; empty means no change. Exclusive with through.",
    )


class ReadOut(BaseModel):
    channel: str
    through: str | None
    changed: bool


class ReadAllOut(BaseModel):
    channels: list[str]


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    list_id: str | None = Field(
        default=None, description="Where the task goes; default the channel's own list"
    )
    title: str | None = Field(default=None, description="Default: the message's first line")
    due_at: str | None = None
    priority: str | None = None
    status: str | None = None
    assignees: list[str] | None = Field(default=None, description="Handles or account ids")
    tags: list[str] | None = None


def _views(session: DbSession, msgs: list) -> list[dict]:
    return chat.message_views(session, msgs)


def _one(session: DbSession, msg) -> dict:
    return chat.message_views(session, [msg])[0]


@router.get("/channels", response_model=list[ChannelOut])
def list_channels(auth: ReadAuth, session: DbSession) -> list[dict]:
    """Every channel, in board order, with your unread and mention counts."""
    return chat.list_channels(session, auth.account)


@router.post("/channels/read-all", response_model=ReadAllOut)
def read_all(auth: WriteAuth, session: DbSession) -> dict:
    """Mark every channel read up to its newest message."""
    return chat.mark_all_read(session, auth.actor)


@router.get("/channels/{key}", response_model=ChannelOut)
def get_channel(key: str, auth: ReadAuth, session: DbSession) -> dict:
    """One channel by key (`general`, `list:<id>`, `section:<id>`, `agent:<id>`) with your
    counts."""
    return chat.channel_view(session, auth.account, key)


@router.get("/channels/{key}/messages", response_model=list[MessageOut])
def list_messages(
    key: str,
    _: ReadAuth,
    session: DbSession,
    before: Annotated[str | None, Query(description="Older top-level messages")] = None,
    after: Annotated[
        str | None, Query(description="Every message after this id, replies too, oldest first")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=chat.MAX_PAGE)] = chat.PAGE,
) -> list[dict]:
    """A page of the channel, oldest first. Default: the newest top-level messages, each with
    its reply count (read a thread with `GET /messages/{id}/thread`). `after` follows the
    channel on a cursor, replies included."""
    return _views(session, chat.messages(session, key, before=before, after=after, limit=limit))


@router.post(
    "/channels/{key}/messages", status_code=status.HTTP_201_CREATED, response_model=MessageOut
)
def post_message(key: str, body: MessageIn, auth: WriteAuth, session: DbSession) -> dict:
    """Post to the channel; `parent_id` replies in that message's thread. `@handle` mentions
    an account (its inbox, check-in and phone hear it); a board task link or `#<item id>`
    shows the task as a card. `file_ids` sends files you uploaded to this channel with it."""
    msg = chat.post(
        session,
        auth.actor,
        key,
        body_md=body.body_md,
        parent_id=body.parent_id,
        file_ids=body.file_ids,
    )
    return _one(session, msg)


@router.post(
    "/channels/{key}/files",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageFileOut,
    openapi_extra={
        "requestBody": {"content": {"application/octet-stream": {"schema": {"type": "string"}}}}
    },
)
async def upload_file(
    key: str,
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    auth: WriteAuth,
    session: DbSession,
) -> dict:
    """Upload a file to send with a message (raw body, up to 25 MB). Until you post a message
    with its `id` in `file_ids`, only you can read it."""
    limit = attachments.MAX_BYTES
    data = await read_body(request, limit)
    return chat_files.view(
        chat_files.upload(
            session,
            auth.actor,
            key,
            filename=filename,
            data=data,
            content_type=request.headers.get("content-type"),
        )
    )


@router.post(
    "/channels/{key}/files/base64",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageFileOut,
)
def upload_file_base64(key: str, body: Base64In, auth: WriteAuth, session: DbSession) -> dict:
    """The same as the raw upload, for clients that can only send JSON (the MCP stdio bridge)."""
    return chat_files.view(
        chat_files.upload(
            session,
            auth.actor,
            key,
            filename=body.filename,
            data=attachments.decode_base64(body.content_base64),
            content_type=body.content_type,
        )
    )


@router.get("/message-files/{file_id}", response_class=Response)
def download_file(
    file_id: str, auth: ReadAuth, session: DbSession, download: bool = False
) -> Response:
    """A chat file's bytes, for whoever can read its message. Images and PDFs show inline
    unless `?download=true`."""
    f, data = chat_files.read_bytes(session, auth.account.id, file_id)
    return file_response(f.filename, f.content_type, data, download=download)


@router.delete("/message-files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def discard_file(file_id: str, auth: WriteAuth, session: DbSession) -> Response:
    """Take back an upload you have not sent. A sent file goes when its message is deleted."""
    chat_files.discard(session, auth.actor, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/channels/{key}/read", response_model=ReadOut)
def mark_read(key: str, body: ReadIn, auth: WriteAuth, session: DbSession) -> dict:
    """Read exact `message_ids`, or intentionally read through a cursor (default newest).
    Exact exposure never reads unopened replies or skipped older messages."""
    return chat.mark_read(
        session, auth.actor, key, through=body.through, message_ids=body.message_ids
    )


@router.get("/messages/{message_id}", response_model=MessageOut)
def get_message(message_id: str, _: ReadAuth, session: DbSession) -> dict:
    """One message."""
    return _one(session, chat.get_message(session, message_id))


@router.get("/messages/{message_id}/thread", response_model=list[MessageOut])
def get_thread(message_id: str, _: ReadAuth, session: DbSession) -> list[dict]:
    """The thread a message is in: its first message, then every reply, oldest first."""
    return _views(session, chat.thread(session, message_id))


@router.patch("/messages/{message_id}", response_model=MessageOut)
def edit_message(message_id: str, body: MessagePatch, auth: WriteAuth, session: DbSession) -> dict:
    """Edit your own message (403 for anyone else's)."""
    return _one(session, chat.edit(session, auth.actor, message_id, body_md=body.body_md))


@router.delete("/messages/{message_id}", response_model=MessageOut)
def delete_message(message_id: str, auth: WriteAuth, session: DbSession) -> dict:
    """Delete your own message: its text goes, its thread stays."""
    return _one(session, chat.delete(session, auth.actor, message_id))


@router.post("/messages/{message_id}/reactions", response_model=MessageOut)
def react(message_id: str, body: ReactionIn, auth: WriteAuth, session: DbSession) -> dict:
    """Add your emoji reaction (idempotent)."""
    return _one(session, chat.react(session, auth.actor, message_id, emoji=body.emoji))


@router.delete("/messages/{message_id}/reactions/{emoji}", response_model=MessageOut)
def unreact(message_id: str, emoji: str, auth: WriteAuth, session: DbSession) -> dict:
    """Take back your emoji reaction (idempotent)."""
    return _one(session, chat.unreact(session, auth.actor, message_id, emoji=emoji))


@router.post(
    "/messages/{message_id}/task", status_code=status.HTTP_201_CREATED, response_model=ItemOut
)
def make_task(message_id: str, body: TaskIn, auth: WriteAuth, session: DbSession) -> dict:
    """Turn a message into one task, in the channel's list or `list_id`. The task quotes the
    message and the message shows the task as a card."""
    fields = body.model_dump(exclude_none=True)
    item = chat.make_task(session, auth.actor, message_id, **fields)
    return items.views(session, [item])[0]
