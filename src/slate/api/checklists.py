"""/items/{id}/checklists, /checklists/{id}, /checklists/{id}/items, /checklist-items/{id}."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import (
    ChecklistEntryIn,
    ChecklistEntryPatch,
    ChecklistIn,
    ChecklistOut,
    ChecklistPatch,
)
from slate.service import checklists
from slate.service.items import get_item

router = APIRouter(tags=["checklists"])


def _one(session: DbSession, item_id: str, checklist_id: str) -> dict:
    return next(c for c in checklists.checklists_of(session, item_id) if c["id"] == checklist_id)


@router.get("/items/{item_id}/checklists", response_model=list[ChecklistOut])
def list_checklists(item_id: str, _: ReadAuth, session: DbSession) -> list[dict]:
    return checklists.checklists_of(session, get_item(session, item_id).id)


@router.post(
    "/items/{item_id}/checklists", status_code=status.HTTP_201_CREATED, response_model=ChecklistOut
)
def create_checklist(item_id: str, body: ChecklistIn, auth: WriteAuth, session: DbSession) -> dict:
    """A checklist inside the item (not subtasks), optionally with its first entries."""
    cl = checklists.create_checklist(session, auth.actor, item_id, name=body.name, items=body.items)
    return _one(session, cl.item_id, cl.id)


@router.patch("/checklists/{checklist_id}", response_model=ChecklistOut)
def update_checklist(
    checklist_id: str, body: ChecklistPatch, auth: WriteAuth, session: DbSession
) -> dict:
    cl = checklists.update_checklist(
        session, auth.actor, checklist_id, body.model_dump(exclude_unset=True)
    )
    return _one(session, cl.item_id, cl.id)


@router.delete("/checklists/{checklist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_checklist(checklist_id: str, auth: WriteAuth, session: DbSession) -> Response:
    checklists.delete_checklist(session, auth.actor, checklist_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/checklists/{checklist_id}/items",
    status_code=status.HTTP_201_CREATED,
    response_model=ChecklistOut,
)
def add_entry(
    checklist_id: str, body: ChecklistEntryIn, auth: WriteAuth, session: DbSession
) -> dict:
    """Add an entry; returns the whole checklist."""
    e = checklists.add_entry(session, auth.actor, checklist_id, **body.model_dump())
    return _one(session, e.item_id, e.checklist_id)


@router.patch("/checklist-items/{entry_id}", response_model=ChecklistOut)
def update_entry(
    entry_id: str, body: ChecklistEntryPatch, auth: WriteAuth, session: DbSession
) -> dict:
    """Tick (`done: true`), untick, rename, assign or reorder an entry."""
    e = checklists.update_entry(session, auth.actor, entry_id, body.model_dump(exclude_unset=True))
    return _one(session, e.item_id, e.checklist_id)


@router.delete("/checklist-items/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(entry_id: str, auth: WriteAuth, session: DbSession) -> Response:
    checklists.delete_entry(session, auth.actor, entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
