"""/lists and their statuses."""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy.orm import Session

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import (
    ListIn,
    ListOut,
    ListPatch,
    StatusesPut,
    StatusOut,
)
from slate.models import List
from slate.service import guests, items, lists, permissions, tags
from slate.service.errors import Invalid

router = APIRouter(tags=["lists"])


def _filing_fields(session: Session, auth: WriteAuth, changes: dict) -> dict:
    fields = {name: changes.pop(name) for name in lists.FILING_FIELDS if name in changes}
    if fields.get("default_assignee_id") is not None:
        error = "the default assignee must be an enabled work account you can assign"
        try:
            account = items.resolve_account(session, fields["default_assignee_id"])
        except Invalid:
            raise Invalid(error) from None
        if not lists.valid_default_assignee(account) or not guests.may_reach(
            session, auth.actor.account_id, account
        ):
            raise Invalid(error)
        fields["default_assignee_id"] = account.id
    if "default_tags" in fields:
        fields["default_tags"] = list(
            dict.fromkeys(tags.normalize(tag) for tag in (fields["default_tags"] or []))
        )
    return fields


def list_out(session: Session, lst: List) -> ListOut:
    fields = {name: getattr(lst, name) for name in ListOut.model_fields if name != "statuses"}
    if (
        fields["default_assignee_id"] is not None
        and guests.viewer_is_guest(session)
        and fields["default_assignee_id"] in guests.workspace_agent_ids(session)
    ):
        fields["default_assignee_id"] = None
    statuses = [StatusOut.model_validate(s) for s in lists.statuses_of(session, lst.id)]
    return ListOut(**fields, statuses=statuses)


@router.get("/lists", response_model=list[ListOut])
def list_lists(
    _: ReadAuth,
    session: DbSession,
    section_id: str | None = None,
    include_archived: bool = False,
) -> list[ListOut]:
    found = lists.list_lists(session, section_id=section_id, include_archived=include_archived)
    return [list_out(session, lst) for lst in found]


@router.get("/lists/{list_id}", response_model=ListOut)
def get_list(list_id: str, _: ReadAuth, session: DbSession) -> ListOut:
    return list_out(session, lists.get_list(session, list_id))


@router.post("/lists", status_code=status.HTTP_201_CREATED, response_model=ListOut)
def create_list(body: ListIn, auth: WriteAuth, session: DbSession) -> ListOut:
    """Create a list in a section. Without `statuses` it gets To do -> In progress -> Done."""
    filing = _filing_fields(session, auth, body.model_dump(exclude_unset=True))
    lst = lists.create_list(
        session,
        auth.actor,
        section_id=body.section_id,
        name=body.name,
        position=body.position,
        statuses=[s.model_dump() for s in body.statuses] if body.statuses is not None else None,
        kind=body.kind,
        filing=filing,
    )
    return list_out(session, lst)


@router.patch("/lists/{list_id}", response_model=ListOut)
def update_list(list_id: str, body: ListPatch, auth: WriteAuth, session: DbSession) -> ListOut:
    """Rename, reorder, move to another section, set filing defaults, change the note or
    reference flag, set the default status, archive
    (`archived: true`) / restore, or make it private (owner/admin; `private: true`)."""
    changes = body.model_dump(exclude_unset=True)
    filing = _filing_fields(session, auth, changes)
    private = changes.pop("private", None)
    if private is not None:
        permissions.set_private(
            session,
            auth.actor,
            by=auth.principal,
            scope_kind="list",
            scope_id=list_id,
            private=private,
        )
    lst = (
        lists.update_list(session, auth.actor, list_id, filing=filing, **changes)
        if changes or filing
        else (lists.get_list(session, list_id))
    )
    return list_out(session, lst)


@router.put("/lists/{list_id}/statuses", response_model=ListOut)
def set_statuses(list_id: str, body: StatusesPut, auth: WriteAuth, session: DbSession) -> ListOut:
    """Replace the list's statuses, in order, in one change.

    An existing status is kept when its `id` is given, else when its name matches; the rest are
    removed and their items move to `fallback` (required if any item would be stranded).
    """
    lists.set_statuses(
        session,
        auth.actor,
        list_id,
        [s.model_dump() for s in body.statuses],
        fallback=body.fallback,
    )
    return list_out(session, lists.get_list(session, list_id))
