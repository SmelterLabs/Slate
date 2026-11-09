"""/tags."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from slate.api.auth import DbSession, ReadAuth, WriteAuth
from slate.api.schemas import TagIn, TagMergeIn, TagOut, TagPatch
from slate.models import Tag
from slate.service import tags

router = APIRouter(tags=["tags"])


@router.get("/tags", response_model=list[TagOut])
def list_tags(_: ReadAuth, session: DbSession) -> list[TagOut]:
    """Every tag with `item_count` (non-archived items carrying it). A guest or list-limited
    token sees the tags on items and docs it can reach, plus the ones it created."""
    counts = tags.usage_counts(session)
    return [
        TagOut(id=t.id, name=t.name, color=t.color, item_count=counts.get(t.id, 0))
        for t in tags.list_tags(session)
    ]


@router.post("/tags", status_code=status.HTTP_201_CREATED, response_model=TagOut)
def create_tag(body: TagIn, auth: WriteAuth, session: DbSession) -> Tag:
    """Create a tag. Names are stored lowercase; a duplicate is 409."""
    return tags.create_tag(session, auth.actor, name=body.name, color=body.color)


@router.patch("/tags/{tag}", response_model=TagOut)
def update_tag(tag: str, body: TagPatch, auth: WriteAuth, session: DbSession) -> Tag:
    """Rename or recolor a tag (by id or name). Items keep it. Owners and admins only."""
    return tags.update_tag(session, auth.actor, tag, **body.model_dump(exclude_unset=True))


@router.post("/tags/{tag}/merge", response_model=TagOut)
def merge_tag(tag: str, body: TagMergeIn, auth: WriteAuth, session: DbSession) -> Tag:
    """Fold this tag into `into`: its items (and recurrence rules) carry `into` instead, then
    this tag is deleted. Returns the surviving tag. Owners and admins only."""
    return tags.merge_tag(session, auth.actor, tag, into=body.into)


@router.delete("/tags/{tag}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(tag: str, auth: WriteAuth, session: DbSession) -> Response:
    """Remove the tag from every item and rule, then delete it. One event per item. Owners
    and admins only."""
    tags.delete_tag(session, auth.actor, tag)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
