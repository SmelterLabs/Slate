"""Comments on items. Authors may edit their own; authors delete their own, admins anyone's.

A comment's `@handle` mentions are resolved to accounts when it is written and carried in the
`item.commented` / `comment.edited` event as `mentions` (handles), so the inbox and
check-in can surface the mention to the account named. Reactions are one emoji per account per
comment, toggled with `react` / `unreact`.

Assigned comments ("Ask"): a comment can be addressed to one account (`assignee_id`), a
question or request that stays open until explicitly resolved. It is
independent of the item's assignees and of `waiting_on`. Events: `comment.assigned`
(`after = {assignee, assignee_id, comment_id}`; `assignee` null when taken off) and
`comment.resolved` (`after = {comment_id, by, reason}`). Comments never resolve Asks;
`keep_open` remains accepted for compatibility. `item.commented.after.reply_to` carries only
earlier authors/Ask recipients' account ids, notifying participants without auto-watching.
Resolving is for the assignee, the author or an admin; (re)assigning for the author or an admin.
A comment view carries `seen_at` / `seen_by_id` when the Ask reached its agent while the agent
was already working on the card (`comment.seen`, service/work.py).
`open_asks()` lists the open ones; check-in news `asked`, Today "Asked of you", the agent
stream's `work` hint and `/work/next` (why `asked`) read them.

An agent's ask (service/reports.py) is an Ask addressed to a person that carries `ask_json =
{questions}` (`ask` on the comment view; its `comment.assigned` has `after.ask = true`,
and the person hears it as `waiting`, not also as a mention). `asks_of()` gives each card's
open ask while it waits on that person (the item view's `ask`).

Replies (card conversation): when an agent has reported the item `blocked` and it waits on the
commenter, a plain comment by that person is the agent's next turn. It is addressed to the
agent as an Ask (`comment.assigned` with `after.reply = true`, a wake for the agent's runner),
the item stops waiting on the person (an ordinary `waiting_on_account_id` change), and the
agent's open asks of that person on the card are resolved (`reason: "answered"`). The Ask the
reply makes still needs explicit resolution. A comment by anyone else, or one addressed to
someone other than that agent, is not a reply; one the person addresses to that agent
explicitly (the board's Ask picker starts on it) is. `answer()` is the same reply with one of
the ask's options as its text, for the person waited on or an admin.

Comment views carry `author_handle` and `author_kind` (`person`, `agent` or `guest`): an
adapter passes only a person's comment to its agent as the user's turn, and quotes anything
else as data.

The `SLATE:` closing line (service/brief.py): with `closing_line=True` (REST and MCP
replies; never a comment Slate writes itself, like a report's note) a comment whose last line
is `SLATE: done | review | later | answered | ask <handle>: <question> [a | b]` also reports
back on the task, so an agent working from inside another product's chat (or the person
pasting its answer) can finish it. From an agent account it is that agent's report
(service/reports.py: done completes the task when the agent is assigned; review sets its
badge; later clears its badge and leaves the task for its due time, which must still be to
come; answered clears its own badge; ask makes this comment the ask). From anyone else,
`done` completes the task and the rest change nothing. A malformed agent ask is refused before
posting. An ending that cannot apply leaves the body as is.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from slate.ids import new_id
from slate.models import (
    Account,
    AccountKind,
    Comment,
    CommentReaction,
    CommentRun,
    Event,
    Item,
    Role,
    utcnow,
)
from slate.service import access, guests
from slate.service.attachments import attach_pasted, split_pasted
from slate.service.core import Actor, apply_change, snapshot
from slate.service.errors import Conflict, Forbidden, Invalid, NotFound
from slate.service.items import get_item, resolve_account

MENTION_RE = re.compile(r"(?<![\w@])@([a-z][a-z0-9_-]{1,31})", re.IGNORECASE)
MAX_EMOJI = 32


def _body(value: str) -> str:
    if not (value or "").strip():
        raise Invalid("comment body is empty")
    return value


def mentions_in(session: Session, body_md: str) -> list[str]:
    """Handles of real accounts `@mentioned` in `body_md`, in first-mention order. For a
    restricted caller, only accounts it can see (`access.participant_ids`): a mention must not
    tell it which other handles exist."""
    wanted = list(dict.fromkeys(m.lower() for m in MENTION_RE.findall(body_md or "")))
    if not wanted:
        return []
    q = select(Account.handle).where(Account.handle.in_(wanted))
    visible = access.participant_ids(session)
    if visible is not None:
        q = q.where(Account.id.in_(visible))
    known = set(session.scalars(q))
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


def ask_headline(ask: dict[str, Any]) -> str:
    questions = ask["questions"]
    first = questions[0]["question"]
    return first if len(questions) == 1 else f"{len(questions)} questions: {first}"


def ask_view(ask: dict[str, Any]) -> dict[str, Any]:
    questions = ask["questions"]
    return {
        "questions": questions,
        "question": ask_headline(ask),
        "options": questions[0]["options"] if len(questions) == 1 else [],
    }


def comment_views(session: Session, comments: Sequence[Comment]) -> list[dict[str, Any]]:
    """Comments as dicts with their reactions, mentions and author handle and kind."""
    reactions = reactions_of(session, [c.id for c in comments])
    authors = authors_of(session, [c.author_id for c in comments])
    seen = seen_of(session, [c.id for c in comments if c.assignee_id])
    steps = run_steps_of(session, [c.id for c in comments])
    return [
        {
            **snapshot(c),
            "created_at": c.created_at,
            "edited_at": c.edited_at,
            "seen_at": seen.get(c.id, (None, None))[0],
            "seen_by_id": seen.get(c.id, (None, None))[1],
            "run_steps": steps.get(c.id),
            "ask": ask_view(c.ask_json) if c.ask_json is not None else None,
            "reactions": reactions.get(c.id, []),
            "mentions": mentions_in(session, c.body_md),
            "author_handle": authors.get(c.author_id, (None, None))[0],
            "author_kind": authors.get(c.author_id, (None, None))[1],
        }
        for c in comments
    ]


def seen_of(session: Session, comment_ids: Sequence[str]) -> dict[str, tuple[Any, str]]:
    """`{comment id: (when, account id)}` for the Asks their agent read while it was already
    working (`comment.seen`, recorded by the run's heartbeat: service/work.py)."""
    if not comment_ids:
        return {}
    rows = session.execute(
        select(Event.subject_id, Event.at, Event.actor_id)
        .where(Event.type == "comment.seen", Event.subject_id.in_(comment_ids))
        .order_by(Event.id)
    )
    return {subject: (at, actor) for subject, at, actor in rows}


def run_steps_of(session: Session, comment_ids: Sequence[str]) -> dict[str, int]:
    """`{comment id: lines}` for the comments that carry a run log (service/work.py), without
    a workspace agent's for a guest."""
    if not comment_ids:
        return {}
    q = select(CommentRun.comment_id, CommentRun.steps).where(
        CommentRun.comment_id.in_(comment_ids)
    )
    if guests.viewer_is_guest(session):
        q = q.where(CommentRun.account_id.not_in(guests.workspace_agent_ids(session)))
    return dict(session.execute(q).all())


def asks_of(session: Session, items: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Each card's newest open structured ask, with its comment id, author and time.
    addressed to the person it waits on (a card waiting on nobody, or on someone it did not
    ask, has none)."""
    waits = {i.id: i.waiting_on_account_id for i in items if i.waiting_on_account_id}
    if not waits:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for c in session.scalars(
        select(Comment)
        .where(
            Comment.item_id.in_(waits),
            Comment.ask_json.is_not(None),
            Comment.resolved_at.is_(None),
        )
        .order_by(Comment.created_at, Comment.id)
    ):
        if c.assignee_id == waits[c.item_id] and c.ask_json is not None:
            out[c.item_id] = {
                "comment_id": c.id,
                **ask_view(c.ask_json),
                "by_id": c.author_id,
                "at": c.created_at,
            }
    return out


def _answered(session: Session, cx: Any, item: Any, person_id: str) -> None:
    """The person answered: the card stops waiting on them and the open asks they were sent
    on it are resolved (`reason: "answered"`). Inside the caller's change."""
    asks = session.scalars(
        select(Comment).where(
            Comment.item_id == item.id,
            Comment.assignee_id == person_id,
            Comment.ask_json.is_not(None),
            Comment.resolved_at.is_(None),
        )
    ).all()
    for ask in asks:
        _resolve(cx, ask, cx.actor.account_id, "answered")
    if item.waiting_on_account_id == person_id:
        from slate.service.items import update_item

        update_item(session, cx.actor, item.id, {"waiting_on_account_id": None})


def _withdrawn(session: Session, cx: Any, comment: Comment) -> None:
    """An agent's open ask is being closed without an answer (resolved or deleted): when it
    was the last one the card waited on that person for, the card stops waiting on them, so
    it goes back to the agent as ordinary work. Inside the caller's change."""
    if comment.ask_json is None or comment.resolved_at is not None:
        return
    item = get_item(session, comment.item_id)
    if item.waiting_on_account_id != comment.assignee_id:
        return
    other = session.scalar(
        select(Comment.id)
        .where(
            Comment.item_id == item.id,
            Comment.id != comment.id,
            Comment.assignee_id == comment.assignee_id,
            Comment.ask_json.is_not(None),
            Comment.resolved_at.is_(None),
        )
        .limit(1)
    )
    if other is None:
        from slate.service.items import update_item

        update_item(session, cx.actor, item.id, {"waiting_on_account_id": None})


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
    ask: dict[str, Any] | None = None,
    _id: str | None = None,
) -> Comment:
    """Post a comment. With `assignee` (a handle or id) it is an Ask for that account.
    Regardless of `keep_open`, leave every Ask open; callers explicitly resolve completed Asks.
    With `closing_line`, a last line `SLATE: …` also reports back on the task (module doc).
    `ask` (`{questions}`, checked by service/reports.py, with `assignee`) makes it an
    agent's ask."""
    item = get_item(session, item_id)
    body, pasted = split_pasted(_body(body_md))
    comment_id = _id or new_id()
    replying = reply_target(session, item, actor.account_id)
    if replying is not None and not guests.may_reach(session, actor.account_id, replying):
        replying = None
    participants = set(
        session.scalars(select(Comment.author_id).where(Comment.item_id == item.id))
    ) | set(
        session.scalars(
            select(Comment.assignee_id).where(
                Comment.item_id == item.id, Comment.assignee_id.is_not(None)
            )
        )
    )
    with apply_change(session, actor) as cx:
        # A project's guests never reach a workspace agent (service/guests.py).
        target = resolve_account(session, assignee) if assignee else None
        guests.refuse(session, actor.account_id, target)
        if closing_line:
            body, ending_ask = _apply_closing_line(session, actor, item, body, comment_id)
            if ending_ask is not None:
                ask = ending_ask
                target = session.get(Account, item.waiting_on_account_id)
        mentions = guests.reachable_handles(session, actor.account_id, mentions_in(session, body))
        if target is not None and (replying is None or replying.id != target.id):
            replying = None  # addressed to someone other than the agent waiting on the author
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
        comment = Comment(
            id=comment_id,
            item_id=item.id,
            author_id=actor.account_id,
            body_md=body,
            created_at=utcnow(),
            ask_json=ask,
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
            _answered(session, cx, item, actor.account_id)
        elif target is not None:
            _assign(cx, comment, target, ask=ask is not None)
    return comment


def answer(session: Session, actor: Actor, comment_id: str, *, option: str) -> Comment:
    """Answer an agent's ask with one of its options: a comment by the caller whose text is
    the option, addressed to the agent (its next turn, as a typed reply is), and the card
    stops waiting on the person asked. For that person or an admin. Returns the new comment."""
    ask = get_comment(session, comment_id)
    if ask.ask_json is None:
        raise Invalid("this comment is not an agent's question")
    if ask.resolved_at is not None:
        raise Conflict("this question was already answered")
    if actor.account_id != ask.assignee_id and not access.is_admin(session, actor.account_id):
        raise Forbidden("only the person asked or an admin can answer it")
    questions = ask.ask_json["questions"]
    if len(questions) != 1:
        raise Invalid("this ask has several questions: answer them in one comment")
    options = questions[0]["options"]
    if option not in options:
        raise Invalid("option must be one of: " + ", ".join(options))
    item = get_item(session, ask.item_id)
    with apply_change(session, actor) as cx:
        reply = add_comment(session, actor, item.id, body_md=option, assignee=ask.author_id)
        if ask.resolved_at is None:  # an admin answering for the person asked
            _answered(session, cx, item, ask.assignee_id)
    return reply


def _apply_closing_line(
    session: Session, actor: Actor, item: Item, body: str, comment_id: str
) -> tuple[str, dict[str, Any] | None]:
    from slate.models import ItemAssignee
    from slate.service import reports
    from slate.service.brief import closing_line
    from slate.service.items import update_item

    line = closing_line(body)
    if line is None:
        return body, None
    account = session.get(Account, actor.account_id)
    is_agent = account is not None and account.kind == AccountKind.agent
    # The line's text is the status; without it the comment's first line is (reports.report).
    said = {"reply": body, "status": reports.standing_line(line.status or "")}
    outcome = "done" if line.outcome == "review" and not said["status"] else line.outcome
    ask = reports.check_ask(questions=line.questions) if is_agent and outcome == "ask" else None
    if not is_agent:
        if outcome == "done":
            try:
                status = reports.done_status(session, item)
            except Invalid:
                return body, None
            if status is not None:
                update_item(session, actor, item.id, {"status": status.id})
        return body, None

    if outcome == "later":
        from slate.service.work import check_later

        try:
            check_later(item)
        except Invalid:
            return body, None
    if outcome == "answered" and item.agent_state_by not in (None, actor.account_id):
        return body, None

    complete = outcome == "done" and session.get(ItemAssignee, (item.id, account.id)) is not None
    try:
        reports.report(
            session, actor, item.id,
            state={"ask": "blocked", "later": "idle", "answered": "idle"}.get(outcome, outcome),
            comment=False, complete=complete, _closing_line=True,
            waiting_on=line.waiting_on if ask is not None else None, ask=ask,
            _ask_comment_id=comment_id if ask is not None else None,
            **(said if ask is None else {}),
        )  # fmt: skip
    except reports.ClosingReportRefused:
        return body, None
    return (line.body or ask_headline(ask), ask) if ask is not None else (body, None)


def _assign(
    cx: Any, comment: Comment, target: Account | None, *, reply: bool = False, ask: bool = False
) -> None:
    before = comment.assignee_id
    item = cx.session.get(Item, comment.item_id)  # read before the writes below
    comment.assignee_id = target.id if target else None
    if target is not None:
        comment.resolved_at = comment.resolved_by_id = None
    stall = target is not None and target.kind == AccountKind.agent and item.stalled_at
    if stall:
        item.stalled_at = None  # a new Ask for the agent ends a stall
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
            **({"ask": True} if ask else {}),
        },
        covers=[comment, item] if stall else [comment],
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


def assign_comment(
    session: Session, actor: Actor, comment_id: str, assignee: str | None
) -> Comment:
    """Address a comment to an account (a handle or id), or take it off with None. For the
    comment's author or an admin. Assigning reopens a resolved comment."""
    comment = get_comment(session, comment_id)
    if comment.author_id != actor.account_id and not access.is_admin(session, actor.account_id):
        raise Forbidden("only the comment's author or an admin can assign it")
    target = resolve_account(session, assignee) if assignee else None
    guests.refuse(session, actor.account_id, target)
    if (target.id if target else None) == comment.assignee_id and comment.resolved_at is None:
        return comment
    with apply_change(session, actor) as cx:
        _assign(cx, comment, target)
    return comment


def resolve_comment(
    session: Session, actor: Actor, comment_id: str, *, reason: str = "resolved"
) -> Comment:
    """Close an Ask: for its assignee, its author or an admin. Already resolved: no change.
    `reason` is `answered` when an agent's finish answered it. Closing an agent's question
    this way withdraws it: the card stops waiting on the person asked."""
    comment = get_comment(session, comment_id)
    if comment.assignee_id is None:
        raise Invalid("this comment is not addressed to anyone")
    if comment.resolved_at is not None:
        return comment
    mine = actor.account_id in (comment.assignee_id, comment.author_id)
    if not mine and not access.is_admin(session, actor.account_id):
        raise Forbidden("only the assignee, the author or an admin can resolve it")
    with apply_change(session, actor) as cx:
        _withdrawn(session, cx, comment)
        _resolve(cx, comment, actor.account_id, reason)
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
    Its reactions go with it, and an open Ask it carried closes with it (an agent's question:
    the card stops waiting on the person asked). One `comment.deleted`
    event keeps what it said (`before` = the comment and its reactions), so the history and the
    audit log still hold it; inboxes and check-in news drop every entry about it."""
    comment = get_comment(session, comment_id)
    if comment.author_id != actor.account_id and not access.is_admin(session, actor.account_id):
        raise Forbidden("only a comment's author or an admin can delete it")
    reactions = list(
        session.scalars(select(CommentReaction).where(CommentReaction.comment_id == comment.id))
    )
    with apply_change(session, actor) as cx:
        _withdrawn(session, cx, comment)
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


def clean_emoji(value: str) -> str:
    """A reaction emoji, trimmed: 1 to `MAX_EMOJI` characters, no spaces (comments and chat)."""
    emoji = (value or "").strip()
    if not emoji or len(emoji) > MAX_EMOJI or any(c.isspace() for c in emoji):
        raise Invalid(f"emoji must be 1-{MAX_EMOJI} characters with no spaces")
    return emoji


def react(session: Session, actor: Actor, comment_id: str, *, emoji: str) -> Comment:
    """Add the caller's `emoji` to a comment. Reacting twice with the same emoji changes nothing
    and records nothing."""
    comment = get_comment(session, comment_id)
    emoji = clean_emoji(emoji)
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
    reaction = session.get(CommentReaction, (comment.id, actor.account_id, clean_emoji(emoji)))
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
