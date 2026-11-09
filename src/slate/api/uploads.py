"""Reading a raw upload body under a size cap without trusting `Content-Length`.

A chunked request carries no length, and `await request.body()` would buffer the whole thing
before the service's own check saw it. Attachments and the two doc upload routes read their
bodies through this helper.
"""

from __future__ import annotations

from fastapi import Request

from slate.service.errors import Invalid


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
