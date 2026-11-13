"""Files on chat messages: a pasted screenshot, a dropped PDF.

A file is uploaded to a channel first (`upload`). Until it is sent it is its uploader's alone:
nobody else can read it, and its `message.file_uploaded` event names no channel, so no board,
agent or webhook hears of it. `chat.post(file_ids=...)` then sends it with a message, in the
message's own change, so whoever hears `message.posted` (an agent woken by the mention
included) finds the files on the message. From then on a file is read exactly where its
message is (`read_bytes` goes through the caller's read filter: channel, share date,
members-only), and it is deleted with the message.

Bytes stay in the database (`message_files.data`), like attachment blobs, so the one backup
covers them. Uploads are checked by the attachment rules (`attachments.clean`: a name, not
empty, at most 25 MB, a plain content type). One account holds at most `MAX_UNSENT_FILES`
unsent uploads, `MAX_UNSENT_BYTES` in all; past that an upload is refused until one is sent or
taken back. An upload that is never sent is swept once it is `UNSENT_KEEP` old: on its
uploader's next upload, and by the scheduler (`sweep_unsent`) for everyone.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.ids import new_id
from slate.models import Message, MessageFile, utcnow
from slate.service import attachments
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Invalid, NotFound

MAX_FILES = 10  # on one message
UNSENT_KEEP = timedelta(hours=24)
MAX_UNSENT_FILES = 20  # waiting to be sent, per account
MAX_UNSENT_BYTES = 100 * 1024 * 1024
SWEEP_BATCH = 200


def view(f: MessageFile) -> dict[str, Any]:
    """A file as messages and events carry it. Read the bytes at `/message-files/{id}`."""
    return {
        "id": f.id,
        "filename": f.filename,
        "content_type": f.content_type,
        "size": f.size,
    }


def files_of(session: Session, message_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """{message id: its files, in upload order} for the messages that have any."""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if message_ids:
        for f in session.scalars(
            select(MessageFile)
            .where(MessageFile.message_id.in_(message_ids))
            .order_by(MessageFile.created_at, MessageFile.id)
        ):
            out[f.message_id].append(view(f))
    return out


def unsent(
    session: Session, account_id: str, channel: str, file_ids: Sequence[str]
) -> list[MessageFile]:
    """The caller's own unsent uploads to `channel` named by `file_ids`, in that order. Anything
    else (someone else's, another channel's, already on a message, unknown) is refused."""
    ids = list(dict.fromkeys(file_ids))
    if len(ids) > MAX_FILES:
        raise Invalid(f"a message carries at most {MAX_FILES} files")
    found = {
        f.id: f
        for f in session.scalars(
            select(MessageFile).where(
                MessageFile.id.in_(ids),
                MessageFile.uploaded_by == account_id,
                MessageFile.channel == channel,
                MessageFile.message_id.is_(None),
            )
        )
    }
    missing = [i for i in ids if i not in found]
    if missing:
        raise Invalid(
            f"file {missing[0]} is not an unsent upload of yours in this channel: "
            "upload it with POST /channels/{key}/files first"
        )
    return [found[i] for i in ids]


def read_bytes(session: Session, account_id: str, file_id: str) -> tuple[MessageFile, bytes]:
    """A file and its bytes, for a caller who can read its message (or who uploaded it, while
    it is unsent). Anyone else gets the same NotFound as for a file that does not exist."""
    f = session.get(MessageFile, file_id)
    if f is None:
        raise NotFound(f"file {file_id} not found")
    if f.message_id is None:
        readable = f.uploaded_by == account_id
    else:
        # Through the caller's read filter (service/access.py): the channel, a project
        # agent's share date and members-only all apply to the file as to the message.
        readable = session.scalar(select(Message).where(Message.id == f.message_id)) is not None
    if not readable:
        raise NotFound(f"file {file_id} not found")
    return f, f.data


def upload(
    session: Session,
    actor: Actor,
    key: str,
    *,
    filename: str,
    data: bytes,
    content_type: str | None = None,
) -> MessageFile:
    """Upload a file to a channel, to send with a message (`chat.post(file_ids=[...])`)."""
    from slate.service import chat  # noqa: PLC0415 (chat reads this module)

    ch = chat.writable(session, key)
    name, ctype = attachments.clean(filename, data, content_type)
    stale = list(
        session.scalars(
            select(MessageFile).where(
                MessageFile.uploaded_by == actor.account_id,
                MessageFile.message_id.is_(None),
                MessageFile.created_at < utcnow() - UNSENT_KEEP,
            )
        )
    )
    gone = {old.id for old in stale}
    count, size = session.execute(
        select(func.count(), func.coalesce(func.sum(MessageFile.size), 0)).where(
            MessageFile.uploaded_by == actor.account_id,
            MessageFile.message_id.is_(None),
            MessageFile.id.not_in(gone),
        )
    ).one()
    if count >= MAX_UNSENT_FILES or size + len(data) > MAX_UNSENT_BYTES:
        raise Invalid(
            f"you have {count} unsent uploads; send or take back some first (at most "
            f"{MAX_UNSENT_FILES} files and {MAX_UNSENT_BYTES // 2**20} MB wait to be sent)"
        )
    with apply_change(session, actor) as cx:
        for old in stale:
            _discard(cx, old)
        f = MessageFile(
            id=new_id(),
            channel=ch.key,
            message_id=None,
            filename=name,
            content_type=ctype,
            size=len(data),
            data=data,
            uploaded_by=actor.account_id,
            created_at=utcnow(),
        )
        session.add(f)
        cx.record("message.file_uploaded", subject_id=actor.account_id, after=view(f), covers=[f])
    return f


def _discard(cx: Change, f: MessageFile) -> None:
    cx.record("message.file_discarded", subject_id=f.uploaded_by, before=view(f), covers=[f])
    cx.session.delete(f)


def sweep_unsent(session: Session, now: datetime) -> int:
    """Scheduler hook: delete uploads nobody sent within `UNSENT_KEEP`, whoever made them,
    as Slate's own account. Returns how many went."""
    stale = list(
        session.scalars(
            select(MessageFile)
            .where(MessageFile.message_id.is_(None), MessageFile.created_at < now - UNSENT_KEEP)
            .order_by(MessageFile.created_at)
            .limit(SWEEP_BATCH)
        )
    )
    if not stale:
        return 0
    from slate.service.due import system_actor  # noqa: PLC0415

    with apply_change(session, system_actor(session)) as cx:
        for f in stale:
            _discard(cx, f)
    return len(stale)


def discard(session: Session, actor: Actor, file_id: str) -> None:
    """Take back an upload that was not sent. A sent file goes with its message."""
    f = session.get(MessageFile, file_id)
    if f is None or f.uploaded_by != actor.account_id:
        raise NotFound(f"file {file_id} not found")
    if f.message_id is not None:
        raise Invalid("that file was sent; delete its message to remove it")
    with apply_change(session, actor) as cx:
        _discard(cx, f)
