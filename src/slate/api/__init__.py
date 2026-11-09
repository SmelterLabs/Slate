"""REST API under /api/v1. Routers are thin: validate, call a service function, shape output."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from slate.api import (
    accounts,
    agents,
    attachments,
    automations,
    batch,
    chat,
    checkin,
    checklists,
    collaboration,
    comments,
    connections,
    data,
    doc_comments,
    docs,
    favorites,
    health,
    inbox,
    items,
    links,
    lists,
    permissions,
    reminders,
    search,
    sections,
    settings,
    stats,
    tags,
    today,
    views,
    webhooks,
    work,
    workload,
)
from slate.service import errors

router = APIRouter(prefix="/api/v1")
# Mounted in this order; slate.docgen lists the routes in the same order.
MODULES = (
    accounts,
    connections,
    collaboration,
    permissions,
    sections,
    lists,
    items,
    comments,
    checklists,
    attachments,
    tags,
    today,
    views,
    favorites,
    inbox,
    chat,
    webhooks,
    links,
    stats,
    batch,
    agents,
    checkin,
    work,
    automations,
    reminders,
    docs,
    doc_comments,
    workload,
    data,
    health,
    search,
    settings,
)
for _module in MODULES:
    router.include_router(_module.router)

_STATUS = {
    errors.NotFound: 404,
    errors.Conflict: 409,
    errors.Invalid: 422,
    errors.Forbidden: 403,
    errors.Gone: 410,
    errors.Unavailable: 503,
}


def status_of(exc: errors.ServiceError) -> int:
    from slate.batch import BatchFailed

    if isinstance(exc, BatchFailed):
        return status_of(exc.cause) if isinstance(exc.cause, errors.ServiceError) else 400
    return next((c for cls, c in _STATUS.items() if isinstance(exc, cls)), 400)


def install(app: FastAPI) -> None:
    app.include_router(router)

    @app.exception_handler(errors.ServiceError)
    def _service_error(_: Request, exc: errors.ServiceError) -> JSONResponse:
        body: dict = {"detail": str(exc)}
        failed_op = getattr(exc, "index", None)
        if failed_op is not None:
            body["failed_op"] = failed_op
        taken = exc if hasattr(exc, "existing_id") else getattr(exc, "cause", None)
        if getattr(taken, "existing_id", None) is not None:
            body["existing_id"] = taken.existing_id
            body["existing_state"] = taken.existing_state
        return JSONResponse(body, status_code=status_of(exc))
