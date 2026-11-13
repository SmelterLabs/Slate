"""Your data: export (JSON or CSV), the admin backup download, and emptying the trash."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from slate.api.auth import AdminAuth, DbSession, ReadAuth
from slate.models import utcnow
from slate.service import export, trash

router = APIRouter(tags=["data"])
log = logging.getLogger(__name__)


class EmptiedOut(BaseModel):
    purged: int


def _attachment(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"'}


@router.get("/export", responses={200: {"content": {"application/json": {}, "text/csv": {}}}})
def export_data(
    _: ReadAuth,
    session: DbSession,
    format: Literal["json", "csv"] = "json",
    list_id: str | None = None,
    section_id: str | None = None,
    include_archived: Annotated[bool, Query(description="Archived tasks too (default)")] = True,
) -> Response:
    """Download a list (`list_id`), a section (`section_id`) or everything. JSON holds every
    task with its comments, checklists, time and links, docs pages, the scope's chat channels
    (messages, thread replies, reactions), plus the structure around them; CSV is one row per
    task for a spreadsheet. Trashed tasks are never included."""
    scope = export.scope_of(session, list_id=list_id, section_id=section_id)
    args = {"list_id": list_id, "section_id": section_id, "include_archived": include_archived}
    if format == "csv":
        body = export.export_csv(session, **args)
        return Response(
            body.encode("utf-8-sig"),  # the BOM makes Excel read UTF-8
            media_type="text/csv; charset=utf-8",
            headers=_attachment(export.filename(scope, "csv")),
        )
    import json

    data = export.export_json(session, **args)
    return Response(
        json.dumps(data, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers=_attachment(export.filename(scope, "json")),
    )


@router.get(
    "/admin/backup",
    response_class=FileResponse,
    responses={200: {"content": {"application/vnd.sqlite3": {}}}},
)
def backup(auth: AdminAuth, session: DbSession, tasks: BackgroundTasks) -> FileResponse:
    """A consistent copy of the whole database (SQLite), taken while Slate keeps running.
    Holds everything, token fingerprints and encrypted secrets included: admins only."""
    folder = Path(tempfile.mkdtemp(prefix="slate-backup-"))
    name = f"slate-backup-{utcnow().strftime('%Y-%m-%d-%H%M')}.db"
    path = folder / name

    def cleanup() -> None:
        shutil.rmtree(folder, ignore_errors=True)

    try:
        size = export.backup_to(session, path)
    except BaseException:
        cleanup()  # a disk-full or refused copy leaves no partial file behind
        raise
    log.info("backup downloaded by %s (%d bytes)", auth.principal.account.handle, size)

    tasks.add_task(cleanup)
    return FileResponse(path, media_type="application/vnd.sqlite3", filename=name)


@router.post("/trash/empty", response_model=EmptiedOut)
def empty_trash(auth: AdminAuth, session: DbSession) -> dict:
    """Delete everything in the trash for good (admins). History stays."""
    return {"purged": trash.empty_trash(session, auth.actor)}
