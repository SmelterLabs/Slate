"""/items/{id}/attachments and /attachments/{id}.

Upload is the raw file as the request body (`Content-Type` is the file's type) with the name in
`?filename=`: `curl --data-binary @scan.pdf -H 'Content-Type: application/pdf'
'.../items/X/attachments?filename=scan.pdf'`. No multipart, so no extra dependency and agents
need nothing special. Downloads need the same bearer token as every other read.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import AttachmentOut
from slate.api.uploads import BASE64_MAX_CHARS, file_response, read_body
from slate.api.uploads import CSP as CSP  # api/docs.py serves doc files under it too
from slate.models import Attachment
from slate.service import attachments
from slate.service.items import get_item

router = APIRouter(tags=["attachments"])


@router.get("/items/{item_id}/attachments", response_model=list[AttachmentOut])
def list_attachments(item_id: str, _: ReadAuth, session: DbSession) -> list[Attachment]:
    return attachments.attachments_of(session, get_item(session, item_id).id)


@router.post(
    "/items/{item_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    response_model=AttachmentOut,
    openapi_extra={
        "requestBody": {"content": {"application/octet-stream": {"schema": {"type": "string"}}}}
    },
)
async def add_attachment(
    item_id: str,
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    auth: WriteAuth,
    session: DbSession,
) -> Attachment:
    """Attach a file (raw body, up to 25 MB)."""
    limit = attachments.MAX_BYTES
    data = await read_body(request, limit, f"attachment is larger than {limit // 2**20} MB")
    # The body arrives on the event loop; the write (and its wait for the write lock) runs in
    # the threadpool, so a busy writer never stops every other request.
    return await run_in_threadpool(
        attachments.add_attachment,
        session,
        auth.actor,
        item_id,
        filename=filename,
        data=data,
        content_type=request.headers.get("content-type"),
    )


class Base64In(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(
        max_length=BASE64_MAX_CHARS, description="The file, base64-encoded (up to 25 MB decoded)"
    )
    content_type: str | None = Field(default=None, description="MIME type; guessed if omitted")


@router.post(
    "/items/{item_id}/attachments/base64",
    status_code=status.HTTP_201_CREATED,
    response_model=AttachmentOut,
)
def add_attachment_base64(
    item_id: str, body: Base64In, auth: WriteAuth, session: DbSession
) -> Attachment:
    """The same as the raw upload, for clients that can only send JSON (the MCP stdio bridge)."""
    return attachments.add_attachment(
        session,
        auth.actor,
        item_id,
        filename=body.filename,
        data=attachments.decode_base64(body.content_base64),
        content_type=body.content_type,
    )


@router.get("/attachments/{attachment_id}", response_class=Response)
def download(
    attachment_id: str,
    _: ReadAuth,
    session: DbSession,
    download: bool = False,
) -> Response:
    """The file's bytes. Images and PDFs show inline unless `?download=true`."""
    att, data = attachments.read_bytes(session, attachment_id)
    return file_response(att.filename, att.content_type, data, download=download)


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(attachment_id: str, auth: WriteAuth, session: DbSession) -> Response:
    attachments.remove_attachment(session, auth.actor, attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
