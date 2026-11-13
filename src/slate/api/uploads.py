"""Raw uploads and the response a stored file is served with.

Reading a body under a size cap without trusting `Content-Length`: a chunked request carries
no length, and `await request.body()` would buffer the whole thing before the service's own
check saw it. Attachments, chat files and the two doc upload routes read their bodies through
`read_body`. `file_response` serves every stored file: an attachment, a chat file, a doc file.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Request, Response

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


# Uploaded HTML or SVG must never run scripts as this origin.
CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
# Types a browser renders as a page of its own (markup, scripts aside): always a download, so
# nobody's upload opens as a page under Slate's address.
_NEVER_INLINE = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "text/xml",
    "application/xml",
}


def file_response(filename: str, content_type: str, data: bytes, *, download: bool) -> Response:
    """A stored file's bytes: task attachments, chat files and doc files alike. Images, PDFs
    and text show inline unless `download`; HTML, SVG and XML always download; nothing is
    sniffed, cached or allowed to run a script."""
    inline = (
        not download
        and content_type.startswith(("image/", "application/pdf", "text/"))
        and content_type.split(";")[0].strip().lower() not in _NEVER_INLINE
    )
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
