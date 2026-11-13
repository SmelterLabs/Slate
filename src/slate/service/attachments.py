"""Files attached to an item. Bytes live in `attachment_blobs` (the same SQLite file, so the
nightly online backup covers them); the `attachments` row is the metadata. Anyone who can write
may attach or remove; removal is recorded with the file's metadata (the bytes go).

Pasted media: an image pasted into a comment or a body as a base64 `data:` URI cannot be shown
there (the card renderer draws no images), so `split_pasted` cuts it out of the text, leaving a
short note, and `attach_pasted` saves it on the item like any upload."""

from __future__ import annotations

import base64
import binascii
import mimetypes
import re
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.ids import new_id
from slate.models import Attachment, AttachmentBlob, Item, utcnow
from slate.service.core import Actor, Change, apply_change
from slate.service.errors import Invalid, NotFound
from slate.service.items import get_item

_CTYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")

MAX_BYTES = 25 * 1024 * 1024
_UNSAFE = re.compile(r'[\x00-\x1f/\\"]+')
# `![alt](data:type;base64,...)`, `[alt](data:...)` or a bare `data:type;base64,...`.
_PASTED = re.compile(
    r"(?:!?\[(?P<alt>[^\]\n]*)\]\(\s*)?"
    r"data:(?P<ctype>[a-z]+/[a-z0-9.+-]+);base64,(?P<data>[A-Za-z0-9+/=\s]+?)"
    r"(?(alt)\s*\)|(?=[^A-Za-z0-9+/=]|$))"
)
_NAME_JUNK = re.compile(r"[^A-Za-z0-9._-]+")


class Pasted(NamedTuple):
    filename: str
    content_type: str
    data: bytes


def _filename(value: str | None) -> str:
    name = _UNSAFE.sub("_", (value or "").strip()).strip(". ")
    if not name:
        raise Invalid("filename is empty")
    return name[:255]


def decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as e:
        raise Invalid("content_base64 is not valid base64") from e


def get_attachment(session: Session, attachment_id: str) -> Attachment:
    att = session.get(Attachment, attachment_id)
    if att is None:
        raise NotFound(f"attachment {attachment_id} not found")
    return att


def attachments_of(session: Session, item_id: str) -> list[Attachment]:
    return list(
        session.scalars(
            select(Attachment).where(Attachment.item_id == item_id).order_by(Attachment.created_at)
        )
    )


def read_bytes(session: Session, attachment_id: str) -> tuple[Attachment, bytes]:
    att = get_attachment(session, attachment_id)
    blob = session.get(AttachmentBlob, att.id)
    if blob is None:
        raise NotFound(f"attachment {attachment_id} has no stored content")
    return att, blob.data


def add_attachment(
    session: Session,
    actor: Actor,
    item_id: str,
    *,
    filename: str,
    data: bytes,
    content_type: str | None = None,
) -> Attachment:
    item = get_item(session, item_id)
    with apply_change(session, actor) as cx:
        return _attach(cx, item, filename, data, content_type)


def clean(filename: str | None, data: bytes, content_type: str | None) -> tuple[str, str]:
    """The stored name and type of an upload, or `Invalid` (no name, empty, over the cap).
    Chat files (service/chat_files.py) are checked by the same rules."""
    name = _filename(filename)
    if not data:
        raise Invalid("attachment is empty")
    if len(data) > MAX_BYTES:
        raise Invalid(f"attachment is larger than {MAX_BYTES // (1024 * 1024)} MB")
    ctype = (content_type or "").split(";")[0].strip().lower()
    if not _CTYPE.match(ctype) or ctype == "application/octet-stream":
        # Anything but a `type/subtype` token (a CR/LF, say) is replaced, never served.
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return name, ctype[:127]


def _attach(
    cx: Change, item: Item, filename: str, data: bytes, content_type: str | None
) -> Attachment:
    name, ctype = clean(filename, data, content_type)
    att_id = new_id()
    att = Attachment(
        id=att_id,
        item_id=item.id,
        filename=name,
        content_type=ctype,
        size=len(data),
        uploaded_by=cx.actor.account_id,
        created_at=utcnow(),
    )
    cx.session.add(att)
    blob = AttachmentBlob(attachment_id=att.id, data=data)
    cx.session.add(blob)
    cx.record(
        "item.attachment_added",
        item_id=item.id,
        subject_id=att.id,
        after={
            "id": att.id,
            "filename": name,
            "content_type": att.content_type,
            "size": att.size,
        },
        covers=[att, blob],
    )
    return att


def split_pasted(text: str) -> tuple[str, list[Pasted]]:
    """Cut base64 `data:` media out of markdown. Returns the text with a `📎 name (attached)`
    note in each one's place, and the files. Anything that does not decode is left as written."""
    files: list[Pasted] = []
    if "base64," not in (text or ""):
        return text, files

    def cut(m: re.Match[str]) -> str:
        try:
            data = decode_base64(re.sub(r"\s+", "", m["data"]))
        except Invalid:
            return m[0]
        if not data:
            return m[0]
        ctype = m["ctype"]
        ext = mimetypes.guess_extension(ctype) or ""
        stem = _NAME_JUNK.sub("-", (m["alt"] or "").strip()).strip("-.")
        if not stem:
            kind = "image" if ctype.startswith("image/") else "file"
            stem = f"{kind}-{len(files) + 1}"
        name = stem if ext and stem.lower().endswith(ext) else f"{stem}{ext}"
        files.append(Pasted(name, ctype, data))
        return f"📎 {name} (attached)"

    return _PASTED.sub(cut, text), files


def attach_pasted(cx: Change, item: Item, files: list[Pasted]) -> None:
    """Save what `split_pasted` cut out as attachments of `item`, inside the caller's change."""
    if files:
        cx.session.flush()  # a new item is in its table before its files point at it
    for f in files:
        _attach(cx, item, f.filename, f.data, f.content_type)


def remove_attachment(session: Session, actor: Actor, attachment_id: str) -> None:
    att = get_attachment(session, attachment_id)
    with apply_change(session, actor) as cx:
        cx.record(
            "item.attachment_removed",
            item_id=att.item_id,
            subject_id=att.id,
            before={"id": att.id, "filename": att.filename, "size": att.size},
            covers=[att],
        )
        session.delete(att)  # the blob goes by ON DELETE CASCADE
