"""Comments on items. Authors may edit their own; authors delete their own, admins anyone's.

A comment's `@handle` mentions are resolved to accounts when it is written and carried in the
`item.commented` / `comment.edited` event as `mentions` (handles), so webhooks can route a
mention to the account named (see `deliveries.matches`). Reactions are one emoji per account per
comment, toggled with `react` / `unreact`.

Assigned comments ("Ask"): a comment can be addressed to one account (`assignee_id`), a
question or request that stays open until explicitly resolved. It is
independent of the item's assignees and of `waiting_on`. Events: `comment.assigned`
(`after = {assignee, assignee_id, comment_id}`; `assignee` null when taken off) and
`comment.resolved` (`after = {comment_id, by, reason}`). Comments never resolve Asks;
`keep_open` remains accepted for compatibility. `item.commented.after.reply_to` carries only
earlier authors/Ask recipients' account ids, notifying participants without auto-watching.
Resolving is for the assignee, the author or an admin; (re)assigning for the author or an admin.
`open_asks()` lists the open ones; check-in news `asked`, Today "Asked of you", the agent
stream's `work` hint and `/work/next` (why `asked`) read them.

Replies (card conversation): when an agent has reported the item `blocked` and it waits on the
commenter, a plain comment by that person is the agent's next turn. It is addressed to the
agent as an Ask (`comment.assigned` with `after.reply = true`, a wake for the agent's runner),
and the item stops waiting on the person (an ordinary `waiting_on_account_id` change). The Ask
still needs explicit resolution. A comment by anyone else, or one addressed to someone other
than that agent, is not a reply; one the person addresses to that agent explicitly (the
board's Ask picker starts on it) is.

Comment views carry `author_handle` and `author_kind` (`person`, `agent` or `guest`): an
adapter passes only a person's comment to its agent as the user's turn, and quotes anything
else as data.

The `SLATE:` closing line (service/brief.py): with `closing_line=True` (REST and MCP
replies; never a comment Slate writes itself, like a report's note) a comment whose last line
is `SLATE: done | review | blocked waiting_on <handle>` also reports back on the task, so an
agent working from inside another product's chat (or the person pasting its answer) can finish
it. From an agent account it is that agent's report (service/reports.py: done completes the
task when the agent is assigned; review and blocked set its badge). From anyone else, `done`
completes the task and `blocked waiting_on` sets who it waits on; `review` changes nothing.
A line that cannot apply (no done status, an unknown handle) leaves the comment as it is.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.ids import new_id
from slate.models import Account, Comment, CommentReaction, Event, Role, utcnow
from slate.service import guests
from slate.service.attachments import attach_pasted, split_pasted
from slate.service.core import Actor, apply_change, snapshot
from slate.service.errors import Forbidden, Invalid, NotFound
from slate.service.items import get_item, resolve_account

MENTION_RE = re.compile(r"(?<![\w@])@([a-z][a-z0-9_-]{1,31})", re.IGNORECASE)
MAX_EMOJI = 32


def _body(value: str) -> str:
    if not (value or "").strip():
        raise Invalid("comment body is empty")
    return value


def mentions_in(session: Session, body_md: str) -> list[str]:
    """Handles of real accounts `@mentioned` in `body_md`, in first-mention order."""
    wanted = list(dict.fromkeys(m.lower() for m in MENTION_RE.findall(body_md or "")))
    if not wanted:
        return []
    known = set(session.scalars(select(Account.handle).where(Account.handle.in_(wanted))))
    return [h for h in wanted if h in known]


def get_comment(session: Session, comment_id: str) -> Comment:
    comment = session.get(Comment, comment_id)
    if comment is None:
        raise NotFound(f"comment {comment_id} not found")
    return comment


def reactions_of(session: Session, comment_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """{comment_id: [{emoji, account_ids}]} with emoji in first-used order."""
    grouped: dict[str, dict[str, list[str]]] = defaultdict(dict)
    if comment_ids:
        for r in session.scalars(
            select(CommentReaction)
            .where(CommentReaction.comment_id.in_(comment_ids))
            .order_by(CommentReaction.created_at)
        ):
            grouped[r.comment_id].setdefault(r.emoji, []).append(r.account_id)
    return {
        cid: [{"emoji": e, "account_ids": ids} for e, ids in by_emoji.items()]
        for cid, by_emoji in grouped.items()
    }


def authors_of(session: Session, account_ids: Sequence[str]) -> dict[str, tuple[str, str]]:
    """{account_id: (handle, person | agent | guest)}: a guest is a person with the guest role."""
    out: dict[str, tuple[str, str]] = {}
    if account_ids:
        for aid, handle, kind, role in session.execute(
            select(Account.id, Account.handle, Account.kind, Account.role).where(
                Account.id.in_(set(account_ids))
            )
        ):
            kind = getattr(kind, "value", kind)
            out[aid] = (handle, "guest" if kind == "person" and role == Role.guest else str(kind))
    return out


def comment_views(session: Session, comments: Sequence[Comment]) -> list[dict[str, Any]]:
    """Comments as dicts with their reactions, mentions and author handle and kind."""
    reactions = reactions_of(session, [c.id for c in comments])
    authors = authors_of(session, [c.author_id for c in comments])
    return [
        {
            **snapshot(c),
            "created_at": c.created_at,
            "edited_at": c.edited_at,
            "reactions": reactions.get(c.id, []),
            "mentions": mentions_in(session, c.body_md),
            "author_handle": authors.get(c.author_id, (None, None))[0],
            "author_kind": authors.get(c.author_id, (None, None))[1],
        }
        for c in comments
    ]


def reply_target(session: Session, item: Any, author_id: str) -> Account | None:
    """The agent a comment by `author_id` answers (module doc, Replies), or None. The agent is
    the one whose blocked report names the author; once that flag is cleared (the person stopped
    the run) while the card still waits on them, it is whoever last set the wait, if an agent."""
    if item.waiting_on_account_id != author_id:
        return None
    blocked = (
        item.agent_state is not None
        and getattr(item.agent_state, "value", item.agent_state) == "blocked"
    )
    if blocked:
        agent_id = item.agent_state_by
    elif item.agent_state is None:
        agent_id = session.scalar(
            select(Event.actor_id)
            .where(
                Event.item_id == item.id,
                Event.field == "waiting_on_account_id",
                func.json_extract(Event.after_json, "$") == author_id,
            )
            .order_by(Event.id.desc())
            .limit(1)
        )
    else:
        return None  # an agent is working or reported something else: not waiting on a reply
    if agent_id in (None, author_id):
        return None
    author = session.get(Account, author_id)
    agent = session.get(Account, agent_id)
    if author is None or agent is None or agent.disabled_at is not None:
        return None
    if getattr(author.kind, "value", author.kind) == "agent":
        return None
    return agent if getattr(agent.kind, "value", agent.kind) == "agent" else None


def add_comment(
    session: Session,
    actor: Actor,
    item_id: str,
    *,
    body_md: str,
    assignee: str | None = None,
    keep_open: bool = False,
    closing_line: bool = False,
) -> Comment:
    """Post a comment. With `assignee` (a handle or id) it is an Ask for that account.
    Regardless of `keep_open`, leave every Ask open; callers explicitly resolve completed Asks.
    With `closing_line`, a last line `SLATE: …` also reports back on the task (module doc)."""
    item = get_item(session, item_id)
    body, pasted = split_pasted(_body(body_md))
    # A project's guests never reach a workspace agent (service/guests.py).
    mentions = guests.reachable_handles(session, actor.account_id, mentions_in(session, body))
    target = resolve_account(session, assignee) if assignee else None
    guests.refuse(session, actor.account_id, target)
    replying = reply_target(session, item, actor.account_id)
    if replying is not None and not guests.may_reach(session, actor.account_id, replying):
        replying = None
    if target is not None and (replying is None or replying.id != target.id):
        replying = None  # addressed to someone other than the agent waiting on the author
    participants = set(
        session.scalars(select(Comment.author_id).where(Comment.item_id == item.id))
    ) | set(
        session.scalars(
            select(Comment.assignee_id).where(
                Comment.item_id == item.id, Comment.assignee_id.is_not(None)
            )
        )
    )
    addressed = replying or target
    # The Ask has its own notification; do not also send a mention/reply to its target.
    if addressed is not None:
        mentions = [h for h in mentions if h != addressed.handle]
        participants.discard(addressed.id)
    participants.discard(actor.account_id)
    if guests.is_guest(session, actor.account_id):
        participants = {
            p
            for p in participants
            if guests.may_reach(session, actor.account_id, session.get(Account, p))
        }
    with apply_change(session, actor) as cx:
        comment = Comment(
            id=new_id(),
            item_id=item.id,
            author_id=actor.account_id,
            body_md=body,
            created_at=utcnow(),
        )
        cx.session.add(comment)
        cx.record(
            "item.commented",
            item_id=item.id,
            subject_id=comment.id,
            after={**snapshot(comment), "mentions": mentions, "reply_to": sorted(participants)},
            covers=[comment],
        )
        from slate.service.watchers import auto_watch  # watchers imports items, like us

        auto_watch(cx, item.id, [actor.account_id], "commented")
        attach_pasted(cx, item, pasted)
        if replying is not None:
            _assign(cx, comment, replying, reply=True)
            from slate.service.items import update_item

            update_item(session, actor, item.id, {"waiting_on_account_id": None})
        elif target is not None:
            _assign(cx, comment, target)
    if closing_line:
        _apply_closing_line(session, actor, item.id, body)
    return comment


def _apply_closing_line(session: Session, actor: Actor, item_id: str, body: str) -> None:
    from slate.models import AccountKind, ItemAssignee
    from slate.service import reports
    from slate.service.brief import closing_line
    from slate.service.errors import Conflict
    from slate.service.items import update_item

    line = closing_line(body)
    if line is None:
        return
    who = line.waiting_on
    if who is not None and session.scalar(select(Account.id).where(Account.handle == who)) is None:
        who = None
    account = session.get(Account, actor.account_id)
    item = get_item(session, item_id)
    try:
        is_agent = account is not None and account.kind == AccountKind.agent
        if is_agent:
            assigned = session.get(ItemAssignee, (item.id, account.id)) is not None
            reports.report(
                session,
                actor,
                item.id,
                state=line.outcome,
                comment=False,
                complete=assigned,
                waiting_on=who if line.outcome == "blocked" else None,
            )
        elif line.outcome == "done":
            status = reports.done_status(session, item)
            if status is not None:
                update_item(session, actor, item.id, {"status": status.id})
        elif line.outcome == "blocked" and who is not None:
            update_item(session, actor, item.id, {"waiting_on_account_id": who})
    except (Invalid, Conflict):
        return  # the comment stands; the line could not apply


def _assign(cx: Any, comment: Comment, target: Account | None, *, reply: bool = False) -> None:
    before = comment.assignee_id
    comment.assignee_id = target.id if target else None
    if target is not None:
        comment.resolved_at = comment.resolved_by_id = None
    cx.record(
        "comment.assigned",
        item_id=comment.item_id,
        subject_id=comment.id,
        field="assignee_id",
        before=before,
        after={
            "assignee": target.handle if target else None,
            "assignee_id": target.id if target else None,
            "comment_id": comment.id,
            **({"reply": True} if reply else {}),
        },
        covers=[comment],
    )


def _resolve(cx: Any, comment: Comment, by: str, reason: str) -> None:
    comment.resolved_at = utcnow()
    comment.resolved_by_id = by
    cx.record(
        "comment.resolved",
        item_id=comment.item_id,
        subject_id=comment.id,
        field="resolved_at",
        after={"comment_id": comment.id, "by": by, "reason": reason},
        covers=[comment],
    )


def _is_admin(session: Session, account_id: str) -> bool:
    role = session.scalar(select(Account.role).where(Account.id == account_id))
    return role in (Role.owner, Role.admin)


def assign_comment(
    session: Session, actor: Actor, comment_id: str, assignee: str | None
) -> Comment:
    """Address a comment to an account (a handle or id), or take it off with None. For the
    comment's author or an admin. Assigning reopens a resolved comment."""
    comment = get_comment(session, comment_id)
    if comment.author_id != actor.account_id and not _is_admin(session, actor.account_id):
        raise Forbidden("only the comment's author or an admin can assign it")
    target = resolve_account(session, assignee) if assignee else None
    guests.refuse(session, actor.account_id, target)
    if (target.id if target else None) == comment.assignee_id and comment.resolved_at is None:
        return comment
    with apply_change(session, actor) as cx:
        _assign(cx, comment, target)
    return comment


def resolve_comment(session: Session, actor: Actor, comment_id: str) -> Comment:
    """Close an Ask: for its assignee, its author or an admin. Already resolved: no change."""
    comment = get_comment(session, comment_id)
    if comment.assignee_id is None:
        raise Invalid("this comment is not addressed to anyone")
    if comment.resolved_at is not None:
        return comment
    if actor.account_id not in (comment.assignee_id, comment.author_id) and not _is_admin(
        session, actor.account_id
    ):
        raise Forbidden("only the assignee, the author or an admin can resolve it")
    with apply_change(session, actor) as cx:
        _resolve(cx, comment, actor.account_id, "resolved")
    return comment


def open_asks(
    session: Session, account_id: str | None = None, *, item_id: str | None = None
) -> list[Comment]:
    """Open assigned comments, oldest first: for an account, on an item, or both."""
    q = select(Comment).where(Comment.assignee_id.is_not(None), Comment.resolved_at.is_(None))
    if account_id is not None:
        q = q.where(Comment.assignee_id == account_id)
    if item_id is not None:
        q = q.where(Comment.item_id == item_id)
    return list(session.scalars(q.order_by(Comment.created_at, Comment.id)))


def edit_comment(session: Session, actor: Actor, comment_id: str, *, body_md: str) -> Comment:
    """Only the author edits a comment. Stamps `edited_at`; one `comment.edited` event."""
    comment = get_comment(session, comment_id)
    if comment.author_id != actor.account_id:
        raise Forbidden("only a comment's author can edit it")
    body, pasted = split_pasted(_body(body_md))
    if body == comment.body_md:
        return comment
    before = comment.body_md
    # Only newly added mentions are news; the ones already there were delivered with the comment.
    old = set(mentions_in(session, before))
    added = [h for h in mentions_in(session, body) if h not in old]
    with apply_change(session, actor) as cx:
        attach_pasted(cx, get_item(session, comment.item_id), pasted)
        comment.body_md = body
        comment.edited_at = utcnow()
        cx.record(
            "comment.edited",
            item_id=comment.item_id,
            subject_id=comment.id,
            field="body_md",
            before=before,
            after=body,
            covers=[comment],
        )
        if added:
            cx.record(
                "item.mentioned",
                item_id=comment.item_id,
                subject_id=comment.id,
                after={"comment_id": comment.id, "mentions": added},
            )
    return comment


def delete_comment(session: Session, actor: Actor, comment_id: str) -> None:
    """Delete a comment for good: its author may delete their own, an owner or admin anyone's.
    Its reactions go with it, and an open Ask it carried closes with it. One `comment.deleted`
    event keeps what it said (`before` = the comment and its reactions), so the history and the
    audit log still hold it; inboxes and check-in news drop every entry about it."""
    comment = get_comment(session, comment_id)
    if comment.author_id != actor.account_id and not _is_admin(session, actor.account_id):
        raise Forbidden("only a comment's author or an admin can delete it")
    reactions = list(
        session.scalars(select(CommentReaction).where(CommentReaction.comment_id == comment.id))
    )
    with apply_change(session, actor) as cx:
        cx.record(
            "comment.deleted",
            item_id=comment.item_id,
            subject_id=comment.id,
            before={
                **snapshot(comment),
                "reactions": [{"emoji": r.emoji, "account_id": r.account_id} for r in reactions],
            },
            covers=[comment, *reactions],
        )
        for r in reactions:
            cx.session.delete(r)
        cx.session.flush()  # the reactions first: the comment's ON DELETE CASCADE would beat them
        cx.session.delete(comment)


def not_about_deleted_comment() -> Any:
    """An events filter: not about a deleted comment (one whose `comment.deleted` event exists).
    Inboxes and check-in news use it, so a deleted comment is no longer anyone's news."""
    gone = select(Event.subject_id).where(Event.type == "comment.deleted")
    return or_(Event.subject_id.is_(None), Event.subject_id.not_in(gone))


def _emoji(value: str) -> str:
    emoji = (value or "").strip()
    if not emoji or len(emoji) > MAX_EMOJI or any(c.isspace() for c in emoji):
        raise Invalid(f"emoji must be 1-{MAX_EMOJI} characters with no spaces")
    return emoji


def react(session: Session, actor: Actor, comment_id: str, *, emoji: str) -> Comment:
    """Add the caller's `emoji` to a comment. Reacting twice with the same emoji changes nothing
    and records nothing."""
    comment = get_comment(session, comment_id)
    emoji = _emoji(emoji)
    key = (comment.id, actor.account_id, emoji)
    if session.get(CommentReaction, key) is not None:
        return comment
    with apply_change(session, actor) as cx:
        reaction = CommentReaction(comment_id=comment.id, account_id=actor.account_id, emoji=emoji)
        cx.session.add(reaction)
        cx.record(
            "comment.reacted",
            item_id=comment.item_id,
            subject_id=comment.id,
            after={"comment_id": comment.id, "emoji": emoji},
            covers=[reaction],
        )
    return comment


def unreact(session: Session, actor: Actor, comment_id: str, *, emoji: str) -> Comment:
    """Take back the caller's `emoji`. Absent is fine (nothing recorded)."""
    comment = get_comment(session, comment_id)
    reaction = session.get(CommentReaction, (comment.id, actor.account_id, _emoji(emoji)))
    if reaction is None:
        return comment
    with apply_change(session, actor) as cx:
        cx.record(
            "comment.unreacted",
            item_id=comment.item_id,
            subject_id=comment.id,
            before={"comment_id": comment.id, "emoji": reaction.emoji},
            covers=[reaction],
        )
        cx.session.delete(reaction)
    return comment
