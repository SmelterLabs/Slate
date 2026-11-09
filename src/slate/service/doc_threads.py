"""Discussion and review threads on docs (pages and files).

Two surfaces, one table (`doc_comments`):

- A **review thread** is "edit and comment": its root is anchored to a span of a page's text
  (`anchor_start`/`anchor_end`/`anchor_quote`), a page of a PDF (`anchor_page`) or a spot on an
  image or a PDF page (`anchor_x`/`anchor_y`, 0-1). It can be resolved and reopened. On a page a
  review comment may carry `suggestion_md`, a replacement for the quoted span; `accept` applies
  it as an ordinary page save and resolves the thread.
- **Discussion** is the unanchored conversation along the side of the document.

Replies carry `parent_id` (one level) and inherit the root's surface and anchor. Every comment is
stamped with `anchor_revision`: the page-history version (`docs.version_no`) or the file revision
current when it was written, so the board can say "on rev 3" and fold a thread whose revision
is no longer current (`outdated` in the view). Page identity also uses `anchor_page_revision`
(the monotonic save counter, independent of folded history). Replies inherit root identity.
`viewed_revision` explicitly pins a new comment to a viewed file revision or page save.

Mentions and Asks: `@handle`s in the body, plus the `assignee` of an Ask, go out as one
`doc.mentioned` event (`after = {doc_id, title, mentions, excerpt, comment_id, asked}`), the
same event a page mention records, so the inbox, check-in, push and webhooks route it without
new rules; a push agent mentioned or asked also gets a doc turn (service/docwake.py). Replies
notify the root author, assignee and earlier repliers (deduped, excluding
the sender), but never resolve an Ask: completion requires Resolve or Accept.

Events (subject = the doc, `item_id` empty): `doc.commented` (after = the comment view),
`doc.comment_edited`, `doc.comment_resolved` (`{comment_id, by, reason}`; reason `reopened`
clears it), `doc.comment_assigned`, `doc.suggestion_accepted`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from slate.ids import new_id
from slate.models import Account, Doc, DocComment, DocFile, Event, Role, utcnow
from slate.service import docs as docs_svc
from slate.service import guests
from slate.service.comments import authors_of, mentions_in
from slate.service.core import Actor, Change, apply_change, event_payload, snapshot
from slate.service.errors import Forbidden, Invalid, NotFound
from slate.service.items import resolve_account

MAX_BODY = 20_000
MAX_QUOTE = 2_000
ANCHOR_KEYS = frozenset({"start", "end", "quote", "page", "x", "y"})
# Events the side panel folds into the discussion as small activity lines. Comment bodies are
# not among them: they come from `comments`, not the event log.
ACTIVITY_TYPES = (
    "doc.created",
    "doc.updated",
    "doc.moved",
    "doc.archived",
    "doc.unarchived",
    "doc.version_restored",
    "doc.revision_added",
    "doc.revision_pinned",
    "doc.revision_unpinned",
    "doc.comment_resolved",
    "doc.suggestion_accepted",
)


# --- reads -----------------------------------------------------------------------------------


def get_comment(session: Session, comment_id: str) -> DocComment:
    c = session.get(DocComment, comment_id)
    if c is None:
        raise NotFound(f"doc comment {comment_id} not found")
    return c


def comments_of(session: Session, doc_id: str) -> list[DocComment]:
    """Every comment on the doc, oldest first (threads are rebuilt from `parent_id`)."""
    docs_svc.get_doc(session, doc_id)
    return list(
        session.scalars(
            select(DocComment)
            .where(DocComment.doc_id == doc_id)
            .order_by(DocComment.created_at, DocComment.id)
        )
    )


def current_revision(session: Session, doc: Doc) -> int:
    """What a comment written now is stamped with: the page version, or the file revision
    (0 before the first upload)."""
    if doc.kind != "file":
        return doc.version_no
    return (
        session.scalar(
            select(func.max(DocFile.revision_no)).where(
                DocFile.doc_id == doc.id, DocFile.revision_no.is_not(None)
            )
        )
        or 0
    )


def views(session: Session, doc: Doc, comments: Sequence[DocComment]) -> list[dict[str, Any]]:
    """Comments as dicts with mentions, author handle and kind, and `outdated` (the revision
    they were written on is no longer the doc's current one)."""
    authors = authors_of(session, [c.author_id for c in comments])
    now = current_revision(session, doc)
    out = []
    for c in comments:
        handle, kind = authors.get(c.author_id, (None, None))
        out.append(
            {
                **snapshot(c),
                "created_at": c.created_at,
                "edited_at": c.edited_at,
                "accepted_at": c.accepted_at,
                "resolved_at": c.resolved_at,
                "mentions": mentions_in(session, c.body_md),
                "author_handle": handle,
                "author_kind": kind,
                "outdated": (
                    c.anchor_revision < now
                    if doc.kind == "file"
                    else c.anchor_page_revision != doc.revision
                ),
                "current_revision": now,
            }
        )
    return out


def view(session: Session, c: DocComment) -> dict[str, Any]:
    return views(session, docs_svc.get_doc(session, c.doc_id), [c])[0]


def counts(session: Session, doc_ids: Sequence[str]) -> dict[str, tuple[int, int]]:
    """{doc_id: (comments, open review threads)} for the given docs (one query each)."""
    if not doc_ids:
        return {}
    total = dict(
        session.execute(
            select(DocComment.doc_id, func.count())
            .where(DocComment.doc_id.in_(doc_ids))
            .group_by(DocComment.doc_id)
        ).all()
    )
    open_threads = dict(
        session.execute(
            select(DocComment.doc_id, func.count())
            .where(
                DocComment.doc_id.in_(doc_ids),
                DocComment.parent_id.is_(None),
                DocComment.surface == "review",
                DocComment.resolved_at.is_(None),
            )
            .group_by(DocComment.doc_id)
        ).all()
    )
    return {d: (total.get(d, 0), open_threads.get(d, 0)) for d in doc_ids}


def activity(session: Session, doc_id: str, *, limit: int = 200) -> dict[str, Any]:
    """What the side panel shows: the doc's comments and the events it folds into the
    conversation as activity lines (revisions, title and body saves, moves, resolved threads),
    each newest last."""
    doc = docs_svc.get_doc(session, doc_id)
    rows = comments_of(session, doc_id)
    events = list(
        session.scalars(
            select(Event)
            .where(Event.subject_id == doc_id, Event.type.in_(ACTIVITY_TYPES))
            .order_by(Event.id.desc())
            .limit(limit)
        )
    )
    events.reverse()
    return {"comments": views(session, doc, rows), "events": [event_payload(e) for e in events]}


# --- writes ----------------------------------------------------------------------------------


def _body(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise Invalid("comment body is empty")
    if len(text) > MAX_BODY:
        raise Invalid(f"comment is longer than {MAX_BODY} characters")
    return text


def _anchor(spec: Mapping[str, Any] | None, doc: Doc) -> dict[str, Any]:
    """Validate an anchor: a span (`quote`, optionally `start`/`end`), a `page`, a spot
    (`x`, `y` in 0-1), or several together. Empty = no anchor (discussion)."""
    out: dict[str, Any] = {}
    if not spec:
        return out
    unknown = set(spec) - ANCHOR_KEYS
    if unknown:
        raise Invalid(f"unknown anchor keys {sorted(unknown)}")
    quote = str(spec.get("quote") or "").strip()
    if quote:
        if len(quote) > MAX_QUOTE:
            raise Invalid(f"anchor quote is longer than {MAX_QUOTE} characters")
        out["anchor_quote"] = quote
        for k in ("start", "end"):
            v = spec.get(k)
            if v is not None:
                if not isinstance(v, int) or v < 0:
                    raise Invalid(f"anchor {k} must be a non-negative integer")
                out[f"anchor_{k}"] = v
        if (
            "anchor_start" in out
            and "anchor_end" in out
            and out["anchor_end"] < out["anchor_start"]
        ):
            raise Invalid("anchor end is before its start")
    if spec.get("page") is not None:
        page = spec["page"]
        if not isinstance(page, int) or page < 1:
            raise Invalid("anchor page must be 1 or more")
        out["anchor_page"] = page
    x, y = spec.get("x"), spec.get("y")
    if (x is None) != (y is None):
        raise Invalid("an anchor spot needs both x and y")
    if x is not None:
        try:
            fx, fy = float(x), float(y)
        except (TypeError, ValueError) as e:
            raise Invalid("anchor x and y must be numbers between 0 and 1") from e
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            raise Invalid("anchor x and y must be between 0 and 1")
        out["anchor_x"], out["anchor_y"] = fx, fy
    if ("anchor_quote" in out or "anchor_start" in out) and doc.kind == "file":
        raise Invalid("a file has no text to quote; anchor a page or a spot instead")
    return out


def _is_admin(session: Session, account_id: str) -> bool:
    role = session.scalar(select(Account.role).where(Account.id == account_id))
    return role in (Role.owner, Role.admin)


def _record_mentions(
    session: Session,
    cx: Change,
    doc: Doc,
    comment: DocComment,
    handles: Sequence[str],
    *,
    asked: str | None,
    reply_to: Sequence[str] = (),
) -> None:
    if not handles and not reply_to:
        return
    line = next((ln for ln in comment.body_md.splitlines() if ln.strip()), "")
    excerpt = " ".join(line.split())[:200]
    cx.record(
        "doc.mentioned",
        subject_id=doc.id,
        after={
            "doc_id": doc.id,
            "title": doc.title,
            "mentions": list(handles),
            "excerpt": excerpt,
            "comment_id": comment.id,
            **({"asked": asked} if asked else {}),
            **({"reply_to": list(reply_to)} if reply_to else {}),
        },
    )
    # A push agent mentioned (or asked) here gets a turn to answer in this thread.
    from slate.service import docwake  # noqa: PLC0415 (docwake reads this module)

    docwake.on_mentioned(cx, session, doc, list(handles), comment=comment, excerpt=excerpt)


def _resolve(cx: Change, c: DocComment, by: str, reason: str) -> None:
    if reason == "reopened":
        c.resolved_at = c.resolved_by_id = None
    else:
        c.resolved_at = utcnow()
        c.resolved_by_id = by
    cx.record(
        "doc.comment_resolved",
        subject_id=c.doc_id,
        field="resolved_at",
        after={"comment_id": c.id, "by": by, "reason": reason},
        covers=[c],
    )


def add_comment(
    session: Session,
    actor: Actor,
    doc_id: str,
    *,
    body_md: str,
    parent_id: str | None = None,
    anchor: Mapping[str, Any] | None = None,
    suggestion_md: str | None = None,
    assignee: str | None = None,
    viewed_revision: int | None = None,
) -> DocComment:
    """Post a comment. With an `anchor` it starts a review thread; without one it is
    discussion. `parent_id` makes it a reply in that thread (one level). `suggestion_md` (pages
    only, with a quoted anchor) proposes a replacement for the quote. `assignee` (handle or id)
    makes it an Ask for that account. Replies notify participants; resolution is explicit."""
    doc = docs_svc.get_doc(session, doc_id)
    body = _body(body_md)
    parent: DocComment | None = None
    cols: dict[str, Any] = {}
    if parent_id is not None:
        parent = get_comment(session, parent_id)
        if parent.doc_id != doc.id:
            raise Invalid("the parent comment is on another doc")
        if parent.parent_id is not None:
            raise Invalid("a reply to a reply is not allowed; reply to the thread's first comment")
        if anchor:
            raise Invalid("a reply takes the thread's anchor; leave anchor out")
        cols.update(
            surface=parent.surface,
            anchor_start=parent.anchor_start,
            anchor_end=parent.anchor_end,
            anchor_quote=parent.anchor_quote,
            anchor_page=parent.anchor_page,
            anchor_x=parent.anchor_x,
            anchor_y=parent.anchor_y,
        )
    else:
        cols.update(_anchor(anchor, doc))
        cols["surface"] = "review" if cols else "discussion"
    suggestion = (suggestion_md or "").strip() or None
    if suggestion is not None:
        if doc.kind == "file":
            raise Invalid("a file takes no text suggestion; upload the next revision instead")
        if not (cols.get("anchor_quote") or (parent and parent.anchor_quote)):
            raise Invalid("a suggestion needs a quoted anchor to replace")
        if len(suggestion) > MAX_BODY:
            raise Invalid("suggestion is too long")
    target = resolve_account(session, assignee) if assignee else None
    guests.refuse(session, actor.account_id, target)
    mentions = guests.reachable_handles(session, actor.account_id, mentions_in(session, body))
    asked = None
    if target is not None and target.handle not in mentions:
        mentions = [*mentions, target.handle]
    if target is not None:
        asked = target.handle
    revision = current_revision(session, doc)
    if viewed_revision is not None and parent is None:
        if doc.kind == "file":
            if viewed_revision < 1 or not session.scalar(
                select(DocFile.id).where(
                    DocFile.doc_id == doc.id, DocFile.revision_no == viewed_revision
                )
            ):
                raise Invalid("the viewed file revision does not exist")
            revision = viewed_revision
        elif viewed_revision != doc.revision:
            raise Invalid("the page changed; reload before starting a thread")
    participant_ids: set[str] = set()
    if parent is not None:
        participant_ids = {parent.author_id, parent.assignee_id} | set(
            session.scalars(select(DocComment.author_id).where(DocComment.parent_id == parent.id))
        )
        participant_ids.discard(None)
        participant_ids.discard(actor.account_id)
    comment = DocComment(
        id=new_id(),
        doc_id=doc.id,
        parent_id=parent.id if parent else None,
        author_id=actor.account_id,
        body_md=body,
        created_at=utcnow(),
        anchor_revision=parent.anchor_revision if parent else revision,
        anchor_page_revision=(parent.anchor_page_revision if parent else doc.revision)
        if doc.kind != "file"
        else None,
        suggestion_md=suggestion,
        assignee_id=target.id if target else None,
        **cols,
    )
    with apply_change(session, actor) as cx:
        session.add(comment)
        cx.record(
            "doc.commented",
            subject_id=doc.id,
            after={
                **snapshot(comment),
                "comment_id": comment.id,
                "mentions": mentions,
                "doc_title": doc.title,
            },
            covers=[comment],
        )
        _record_mentions(
            session, cx, doc, comment, mentions, asked=asked, reply_to=sorted(participant_ids)
        )
        if target is not None:
            cx.record(
                "doc.comment_assigned",
                subject_id=doc.id,
                field="assignee_id",
                after={
                    "comment_id": comment.id,
                    "assignee": target.handle,
                    "assignee_id": target.id,
                },
            )
        # A reply is not agreement or completion. Resolution is an explicit action.
    return comment


def edit_comment(session: Session, actor: Actor, comment_id: str, *, body_md: str) -> DocComment:
    """Only the author edits; `edited_at` is stamped. New `@mentions` are news, old ones not."""
    c = get_comment(session, comment_id)
    if c.author_id != actor.account_id:
        raise Forbidden("only a comment's author can edit it")
    body = _body(body_md)
    if body == c.body_md:
        return c
    doc = docs_svc.get_doc(session, c.doc_id)
    before = c.body_md
    old = set(mentions_in(session, before))
    added = guests.reachable_handles(
        session, actor.account_id, [h for h in mentions_in(session, body) if h not in old]
    )
    with apply_change(session, actor) as cx:
        c.body_md = body
        c.edited_at = utcnow()
        cx.record(
            "doc.comment_edited",
            subject_id=c.doc_id,
            field="body_md",
            before=before,
            after=body,
            covers=[c],
        )
        _record_mentions(session, cx, doc, c, added, asked=None)
    return c


def _root(c: DocComment) -> DocComment:
    if c.parent_id is not None:
        raise Invalid("resolve the thread's first comment, not a reply")
    return c


def resolve_comment(session: Session, actor: Actor, comment_id: str) -> DocComment:
    """Close a review thread or an Ask (its author, its assignee, an admin, or anyone who can
    edit the doc). Already resolved: no change."""
    c = _root(get_comment(session, comment_id))
    if c.resolved_at is not None:
        return c
    with apply_change(session, actor) as cx:
        _resolve(cx, c, actor.account_id, "resolved")
    return c


def reopen_comment(session: Session, actor: Actor, comment_id: str) -> DocComment:
    c = _root(get_comment(session, comment_id))
    if c.resolved_at is None:
        return c
    with apply_change(session, actor) as cx:
        _resolve(cx, c, actor.account_id, "reopened")
    return c


def assign_comment(
    session: Session, actor: Actor, comment_id: str, assignee: str | None
) -> DocComment:
    """Address a comment to an account (an Ask), or take it off with None: for the author or
    an admin. Assigning reopens a resolved thread."""
    c = get_comment(session, comment_id)
    if c.author_id != actor.account_id and not _is_admin(session, actor.account_id):
        raise Forbidden("only the comment's author or an admin can assign it")
    target = resolve_account(session, assignee) if assignee else None
    guests.refuse(session, actor.account_id, target)
    if (target.id if target else None) == c.assignee_id and (
        target is None or c.resolved_at is None
    ):
        return c
    doc = docs_svc.get_doc(session, c.doc_id)
    with apply_change(session, actor) as cx:
        before = c.assignee_id
        c.assignee_id = target.id if target else None
        if target is not None:
            c.resolved_at = c.resolved_by_id = None
        cx.record(
            "doc.comment_assigned",
            subject_id=c.doc_id,
            field="assignee_id",
            before=before,
            after={
                "comment_id": c.id,
                "assignee": target.handle if target else None,
                "assignee_id": target.id if target else None,
            },
            covers=[c],
        )
        if target is not None:
            _record_mentions(session, cx, doc, c, [target.handle], asked=target.handle)
    return c


def accept_suggestion(session: Session, actor: Actor, comment_id: str) -> tuple[DocComment, Doc]:
    """Apply a review comment's `suggestion_md` to the page: the quoted span (the first
    occurrence from `anchor_start`, else anywhere) becomes the suggestion, as an ordinary page
    save, and the thread is resolved (`reason: accepted`)."""
    c = get_comment(session, comment_id)
    if not c.suggestion_md:
        raise Invalid("this comment carries no suggestion")
    if c.accepted_at is not None:
        raise Invalid("this suggestion was already accepted")
    doc = docs_svc.get_doc(session, c.doc_id)
    if doc.kind == "file" or not c.anchor_quote:
        raise Invalid("a suggestion applies to quoted page text only")
    if c.anchor_page_revision != doc.revision:
        raise Invalid("the page changed since this suggestion; re-anchor it or edit by hand")
    body = doc.body_md
    if c.anchor_start is not None:
        at = c.anchor_start
        end = c.anchor_end if c.anchor_end is not None else at + len(c.anchor_quote)
        if body[at:end] != c.anchor_quote:
            raise Invalid("the exact quoted span no longer matches; re-anchor it")
    else:
        at = body.find(c.anchor_quote)
        if at < 0 or body.find(c.anchor_quote, at + 1) >= 0:
            raise Invalid("the quoted text is missing or ambiguous; re-anchor it")
    new_body = body[:at] + c.suggestion_md + body[at + len(c.anchor_quote) :]
    docs_svc.update_doc(
        session, actor, doc.id, {"body_md": new_body, "base_revision": doc.revision}
    )
    root = c if c.parent_id is None else get_comment(session, c.parent_id)
    with apply_change(session, actor) as cx:
        c.accepted_at = utcnow()
        cx.record(
            "doc.suggestion_accepted",
            subject_id=doc.id,
            after={
                "comment_id": c.id,
                "quote": c.anchor_quote[:200],
                "suggestion": c.suggestion_md[:200],
            },
            covers=[c],
        )
        if root.resolved_at is None:
            _resolve(cx, root, actor.account_id, "accepted")
    return c, docs_svc.get_doc(session, doc.id)
