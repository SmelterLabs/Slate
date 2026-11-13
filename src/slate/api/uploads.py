"""Request body caps, raw uploads and the response a stored file is served with.

`BodyLimit` caps every request body in the app at `MAX_REQUEST_BYTES` (a declared
`Content-Length` over it gets 413 before any byte is read; a body with no length, or one
that lies, is counted as it arrives and refused with 413 the moment it passes the cap). The
cap fits the largest upload Slate takes, a 25 MB file sent as base64 JSON.

Reading a body under a size cap without trusting `Content-Length`: a chunked request carries
no length, and `await request.body()` would buffer the whole thing before the service's own
check saw it. Attachments, chat files and the two doc upload routes read their bodies through
`read_body`. A route that reads its body this way is `async`, so it must then run its
(synchronous) service write with `run_in_threadpool`: on the event loop, the write's wait for
the write lock would stop every other request. `file_response` serves an attachment or a chat
file.
"""

from __future__ import annotations

import math
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from slate.service import attachments
from slate.service.errors import Invalid

# A 25 MB file as base64 (the JSON upload routes), and the whole request with room to spare.
BASE64_MAX_CHARS = math.ceil(attachments.MAX_BYTES / 3) * 4
MAX_REQUEST_BYTES = 36 * 1024 * 1024


def _over(limit: int) -> str:
    return f"request body is larger than {limit // (1024 * 1024)} MB"


class BodyTooLarge(HTTPException):
    def __init__(self, limit: int) -> None:
        super().__init__(status_code=413, detail=_over(limit))


class BodyLimit:
    """Refuse a request body over `MAX_REQUEST_BYTES` with 413, declared or streamed.

    A streamed body raises `BodyTooLarge` out of `receive`, wherever the app is reading it
    (FastAPI's JSON parsing, `read_body`, the MCP transport); FastAPI renders it as 413,
    and a caller that swallowed it still gets the 413 here if nothing was sent yet."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = MAX_REQUEST_BYTES
        for name, value in scope.get("headers") or ():
            if name == b"content-length" and value.isdigit() and int(value) > limit:
                await JSONResponse({"detail": _over(limit)}, 413)(scope, receive, send)
                return
        seen = 0
        started = False

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise BodyTooLarge(limit)
            return message

        async def watched(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, watched)
        except BodyTooLarge:
            if started:
                raise
            await JSONResponse({"detail": _over(limit)}, 413)(scope, receive, send)


def install(app: FastAPI) -> None:
    app.add_middleware(BodyLimit)


def too_large(limit: int) -> str:
    return f"file is larger than {limit // (1024 * 1024)} MB"


async def read_body(request: Request, limit: int, message: str | None = None) -> bytes:
    """The request body, at most `limit` bytes. A declared length over the cap is refused
    before any byte is read; otherwise the stream is counted as it arrives and dropped the
    moment it passes the cap, with the same 422 the service gives an oversized file."""
    message = message or too_large(limit)
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise Invalid(message)
        except ValueError:
            raise Invalid("content-length is not a number") from None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise Invalid(message)
    return bytes(buf)


# Uploaded HTML or SVG must never run scripts as this origin.
CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"


def file_response(filename: str, content_type: str, data: bytes, *, download: bool) -> Response:
    """A stored file's bytes. Images, PDFs and text show inline unless `download`; nothing is
    sniffed, cached or allowed to run a script."""
    inline = not download and content_type.startswith(("image/", "application/pdf", "text/"))
    disposition = "inline" if inline else "attachment"
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": CSP,
        },
    )
