"""Agent reports: an agent says what it is doing on an item, and people see it on the card.

    report(session, actor, item_id, state="working", note="Drafting the email", progress=40)

States: `working`, `blocked`, `review` set the item's agent flag (`items.agent_*`: who, when,
note, progress); `done` and `idle` clear it. Each report is one `item.reported` event whose
`after` is the report (`{state, note, progress, by}`) and `before` the old flag (or null), so
the history shows every report and the board updates live. Rule 4: this is a flag over the
history, never a board column.

Side effects, each its own ordinary event in the same transaction:

- `note` (with `comment`, the default) is also posted as a comment by the agent, so the report
  reads as conversation in the task window and `@mentions` in it reach the
  mentioned accounts' inboxes.
- `done` moves the item to its list's first done-kind status unless `complete=False` or it is
  already done or cancelled.
- `blocked` with `waiting_on` (a handle) sets the item's `waiting_on_account_id`. An ask for a
  person takes one to four `questions`, or `question` and optional `options` for one. Its
  comment (the `reply`, else the headline) carries the question list. The badge uses the
  headline and the card lands in Today "waiting on me". Without `waiting_on` an ask
  waits on the person whose Ask started this turn, else the workspace owner
  (`brief.default_ask`). `blocked` waiting on a person without a question is refused
  (`ASK_ADVICE`); waiting on an agent, or on nobody, needs none. A later report does not clear
  the wait; the person's answer (service/comments.py) or a PATCH does, and closing the item
  (done or cancelled, including a `done` report) clears it.
- **Standing**: an agent's every report but `working` says where the card stands, in one
  line: `status` (checked at the API, 1 to STATUS_MAX characters), else the first line of
  `reply`, else of `note` (`standing_line`), else the state's label. An ask uses its headline,
  cut to STATUS_MAX. Stored on `items.standing_*`, kept after closing, carried as `after.status`.
  `review` needs a `status` (what the person should check; REVIEW_ADVICE) and its `waiting_on`
  names the person it is for (`items.review_for`, default as for an ask); it does not set who
  the card waits on. The item view computes whose move it is (`items._move_id`).
- `failed` (the run broke) sets a badge nobody is told about; the work loop retries the card
  (service/work.py).
- `working` with `assign_me` adds the agent to the assignees if it is not already there.
- `working` with `claim` is an atomic take (it adds the agent to the assignees unless
  `no_assign`, used for an agent answering an Ask on a task it is not assigned to): refused
  (`Conflict`, 409) when another account's working/blocked/review flag on the item is younger
  than `CLAIM_FRESH`, checked under the write lock so two agents racing for one item cannot
  both win. An older flag counts as abandoned (the board dims it at the same age), and a
  parked blocked/review flag never stops an agent the card has an open Ask for. When the
  holder took the item through the work loop (service/work.py) its lease decides instead:
  refused while the lease runs, free once it has run out.
- `url` (http or https) is where the agent is working: its chat, session, PR or log. The badge
  shows it as "Open". A report without `url` keeps the last one; `done` and `idle` clear it.
- Any report other than `working` ends the reporter's lease on the item; `done` and `idle`
  end anyone's.
- `reply` (card conversation) is the agent's answer for this turn, posted verbatim as one
  comment by the agent instead of the labelled note (the badge note is `note`, else the
  reply's first line). `blocked` with `waiting_on` then reads on the board as "Waiting on
  you" for that person, who is told (inbox reason `waiting`).
- `thread` (`{ref, url}`) is the agent's own conversation for this item in its runtime (an
  opaque session id, and a link to open it): stored per item and agent (`agent_threads`),
  returned on the item and on `/work/next`, so the adapter continues the same session on the
  next turn. A new or changed thread is part of the `item.reported` event (`after.thread`); its
  `ref` is also the `session_ref` of the events the report writes, when the caller sent none.
  Its `url` is the badge's Open link when no `url` is given.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.ids import new_id
from slate.models import (
    Account,
    AccountKind,
    AgentState,
    AgentThread,
    Item,
    ItemAssignee,
    Status,
    StatusKind,
    WorkLease,
    utcnow,
)
from slate.service import comments
from slate.service.core import Actor, apply_change, in_change
from slate.service.errors import Conflict, Invalid, NotFound
from slate.service.items import get_item, resolve_account, update_item
from slate.settings import safe_link_schemes, settings

STATES = ("working", "blocked", "review", "failed", "done", "idle")
LABELS = {
    "working": "Working",
    "blocked": "Blocked",
    "review": "Ready for review",
    "failed": "Could not run",
    "done": "Done",
    "idle": "Stopped",
}
NOTE_MAX = 280
URL_MAX = 2000
REF_MAX = 200
REPLY_MAX = 20000
QUESTION_MAX = 200
QUESTIONS_MAX = 4
OPTION_MAX = 40
OPTIONS_MIN, OPTIONS_MAX = 2, 4
ASK_ADVICE = "to wait on a person, use ask with a one-line question"
STATUS_MAX = 140
REVIEW_ADVICE = (
    "review needs a status: say in one line what the person should check, or finish done "
    "when there is nothing to check"
)
# A badge nobody refreshed for this long is abandoned: a claim may take the item over.
CLAIM_FRESH = timedelta(hours=6)


FLAG_FIELDS = (
    "agent_state",
    "agent_state_by",
    "agent_state_at",
    "agent_note",
    "agent_progress",
    "agent_url",
)


def flag_of(item: Item) -> dict[str, Any] | None:
    """The item's current agent flag as a dict, or None."""
    if item.agent_state is None:
        return None
    return {
        "state": item.agent_state.value,
        "by": item.agent_state_by,
        "at": item.agent_state_at,
        "note": item.agent_note,
        "progress": item.agent_progress,
        "url": item.agent_url,
    }


def clear_flag(cx: Any, item: Item) -> None:
    """Clear the item's agent flag inside an open change, recorded as an `idle` report by the
    actor: a person dismissing "Ready for review", or moving the card on."""
    before = flag_of(item)
    if before is None:
        return
    for key in FLAG_FIELDS:
        setattr(item, key, None)
    cx.record(
        "item.reported",
        item_id=item.id,
        field="agent_state",
        before=before,
        after={
            "state": "idle",
            "note": None,
            "progress": None,
            "by": cx.actor.account_id,
            "url": None,
        },
        covers=[item],
    )


def check_url(url: str | None) -> str | None:
    """A badge link: an absolute URL with a scheme in `settings.link_schemes` (http and https
    unless configured), or None. A scheme other than http(s) still needs a host part
    (`myapp://thing/...`), so a bare `myapp:` can never pass."""
    url = (url or "").strip() or None
    if url is None:
        return None
    if len(url) > URL_MAX:
        raise Invalid(f"url is at most {URL_MAX} characters")
    from urllib.parse import urlsplit

    schemes = safe_link_schemes(settings.link_schemes)
    parts = urlsplit(url)
    if parts.scheme.lower() not in schemes or not parts.netloc:
        raise Invalid("url must be " + " or ".join(f"{s}://" for s in schemes) + " link")
    return url


def check_thread(thread: Any) -> dict[str, Any] | None:
    """`{ref, url}` from a dict or model (or None): ref 1-200 characters, url http(s)."""
    if thread is None:
        return None
    raw = thread if isinstance(thread, dict) else thread.model_dump()
    ref = str(raw.get("ref") or "").strip()
    if not ref or len(ref) > REF_MAX:
        raise Invalid(f"thread.ref is 1 to {REF_MAX} characters")
    return {"ref": ref, "url": check_url(raw.get("url"))}


def with_thread_ref(session: Session, actor: Actor, thread: Any) -> Actor:
    """The actor with the thread's ref as its `session_ref` (when it has none), so History can
    trace the events back to the agent's session. Only at the outermost change: an inner block
    must keep its caller's actor."""
    if thread is None or actor.session_ref is not None or in_change(session):
        return actor
    ref = thread["ref"] if isinstance(thread, dict) else getattr(thread, "ref", None)
    return replace(actor, session_ref=str(ref)[:REF_MAX]) if ref else actor


def thread_of(session: Session, item_id: str, account_id: str) -> dict[str, Any] | None:
    row = session.get(AgentThread, (item_id, account_id))
    return {"ref": row.ref, "url": row.url} if row is not None else None


def first_line(text: str, limit: int = NOTE_MAX) -> str | None:
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#>*- ").strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return None


def standing_line(text: str) -> str | None:
    """The first non-empty line of `text` as plain words: Markdown marks and link targets
    taken out, cut to STATUS_MAX characters. None for empty text."""
    for raw in text.splitlines():
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw)
        line = re.sub(r"[*_`]", "", line).strip().lstrip("#>+- ").strip()
        if line:
            return line if len(line) <= STATUS_MAX else line[: STATUS_MAX - 1] + "…"
    return None


def check_ask(
    question: str | None = None,
    options: list[str] | None = None,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Validate either input form and return the stored question list, or None."""
    if questions is not None and (question is not None or options):
        raise Invalid("send question or questions, not both")
    if questions is None and question is None:
        if options:
            raise Invalid("options go with a question")
        return None
    if questions is None:
        questions = [{"question": question, "options": options or []}]
    if not isinstance(questions, list) or not 1 <= len(questions) <= QUESTIONS_MAX:
        raise Invalid(f"send 1 to {QUESTIONS_MAX} questions")
    checked = []
    for entry in questions:
        if not isinstance(entry, dict) or entry.keys() - {"question", "options"}:
            raise Invalid("each question must be an object with question and optional options")
        text = entry.get("question")
        text = text.strip() if isinstance(text, str) else ""
        if not text or len(text.splitlines()) != 1 or len(text) > QUESTION_MAX:
            raise Invalid(f"question is one line of 1 to {QUESTION_MAX} characters")
        raw_options = entry.get("options")
        if raw_options is None:
            raw_options = []
        message = (
            f"options are {OPTIONS_MIN} to {OPTIONS_MAX} answers of 1 to {OPTION_MAX} characters"
        )
        if not isinstance(raw_options, list) or any(not isinstance(o, str) for o in raw_options):
            raise Invalid(message)
        choices = [o.strip() for o in raw_options]
        if choices and (
            not OPTIONS_MIN <= len(choices) <= OPTIONS_MAX
            or any(len(o.splitlines()) != 1 or len(o) > OPTION_MAX for o in choices)
        ):
            raise Invalid(message)
        checked.append({"question": text, "options": choices})
    return {"questions": checked}


def refuse_person_wait(session: Session, actor: Actor, ref: str | None) -> None:
    """An agent never sets a card waiting on a person by hand: it asks (ASK_ADVICE)."""
    me = session.get(Account, actor.account_id)
    if (
        ref
        and me is not None
        and me.kind == AccountKind.agent
        and resolve_account(session, ref).kind == AccountKind.person
    ):
        raise Invalid(f"an agent cannot set a card waiting on a person; {ASK_ADVICE}")


def _asker(session: Session, actor: Actor, item_id: str) -> Account:
    """Who an ask names nobody waits on: the person whose Ask started this turn, else the
    workspace owner."""
    from slate.service import work
    from slate.service.brief import default_ask

    asks = work.turn_asks(session, actor.account_id, item_id)
    return resolve_account(session, default_ask(session, asks[0].author_id if asks else None))


def _comment_body(state: str, note: str, progress: int | None) -> str:
    pct = f" ({progress}%)" if progress is not None else ""
    return f"**{LABELS[state]}{pct}** · {note}"


class ClosingReportRefused(Exception):
    """A comment's ending does not apply, as determined before writing its report."""


@contextmanager
def _report_preflight(closing_line: bool) -> Iterator[None]:
    try:
        yield
    except (Invalid, Conflict, NotFound) as exc:
        if closing_line:
            raise ClosingReportRefused from exc
        raise


def report(
    session: Session,
    actor: Actor,
    item_id: str,
    *,
    state: str,
    note: str | None = None,
    progress: int | None = None,
    comment: bool = True,
    complete: bool = True,
    waiting_on: str | None = None,
    assign_me: bool = False,
    claim: bool = False,
    url: str | None = None,
    no_assign: bool = False,
    reply: str | None = None,
    thread: Any = None,
    run_id: str | None = None,
    status: str | None = None,
    ask: dict[str, Any] | None = None,
    _taking: bool = False,
    _finishing: bool = False,
    _ask_comment_id: str | None = None,
    _closing_line: bool = False,
) -> Item:
    with _report_preflight(_closing_line):
        if state not in STATES:
            raise Invalid(f"state must be one of {', '.join(STATES)}")
        if progress is not None and not 0 <= progress <= 100:
            raise Invalid("progress is a percentage, 0 to 100")
        note = (note or "").strip() or None
        reply = (reply or "").strip() or None
        if reply is not None and len(reply) > REPLY_MAX:
            raise Invalid(f"reply is at most {REPLY_MAX} characters")
        thread = check_thread(thread)
        url = check_url(url) or (thread or {}).get("url")
        actor = with_thread_ref(session, actor, thread)
        if waiting_on is not None and state not in ("blocked", "review"):
            raise Invalid("waiting_on goes with state 'blocked' or 'review'")
        if claim and state != "working":
            raise Invalid("claim goes with state 'working'")
        if ask is not None and state != "blocked":
            raise Invalid("question goes with an ask (state 'blocked')")
        if ask is not None and status is not None:
            raise Invalid("an ask's question is its status: send no status with it")
        if state == "review" and status is None:
            raise Invalid(REVIEW_ADVICE)
        assign_me = (assign_me or claim) and not no_assign
        item = get_item(session, item_id)
        waiting = resolve_account(session, waiting_on) if waiting_on else None
        reviewer = None
        if state == "review":
            reviewer, waiting = waiting or _asker(session, actor, item.id), None
            if reviewer.kind != AccountKind.person:
                raise Invalid("a review is for a person: waiting_on names who checks it")
        if ask is not None and waiting is None:
            waiting = _asker(session, actor, item.id)
        if waiting is not None and waiting.kind == AccountKind.person and ask is None:
            raise Invalid(f"blocked cannot wait on a person without a question: {ASK_ADVICE}")
        if ask is not None and waiting.kind != AccountKind.person:
            raise Invalid("an ask waits on a person; blocked waits on an agent or on nobody")
        finished = None
        if state == "done" and complete:
            finished = done_status(session, item)
    details = comments.ask_view(ask) if ask is not None else None
    ask_comment_id = _ask_comment_id or (new_id() if ask is not None and comment else None)
    headline = details["question"] if details else None
    badge_note = headline or note or (first_line(reply) if reply else None)
    thread_row = (
        session.get(AgentThread, (item.id, actor.account_id)) if thread is not None else None
    )
    new_thread = thread is not None and (
        thread_row is None or (thread_row.ref, thread_row.url) != (thread["ref"], thread["url"])
    )
    waiting_again = waiting is not None and item.waiting_on_account_id == waiting.id
    # Where the card stands: an agent's every report but `working` says it (module docstring).
    reporter = session.get(Account, actor.account_id)
    standing = None
    if state != "working" and reporter is not None and reporter.kind == AccountKind.agent:
        standing = (
            (headline if len(headline) <= STATUS_MAX else headline[: STATUS_MAX - 1] + "…")
            if headline
            else status or standing_line(reply or "") or standing_line(note or "") or LABELS[state]
        )

    with apply_change(session, actor) as cx:
        from slate.service import work

        session.refresh(item)
        held = session.get(WorkLease, item.id, populate_existing=True)
        if (
            held is not None
            and not _taking
            and not run_id
            and state in ("done", "idle")
            and work._person_clears_expired(session, cx, actor, item, held)
        ):
            held = None  # a person confirmed the expired run stopped (Clear on its badge)
        if not _taking and (held is not None or run_id):
            with _report_preflight(_closing_line):
                work._holding(session, actor, item, run_id)
            if not _finishing:
                finished = None  # only the fenced finish may settle this execution
        if state == "working":
            session.refresh(item)  # under the write lock: see a racing claim's commit
            _check_claim(session, item, actor.account_id)
        before = flag_of(item)
        if state in ("done", "idle"):
            values: dict[str, Any] = dict.fromkeys(
                (
                    "agent_state",
                    "agent_state_by",
                    "agent_state_at",
                    "agent_note",
                    "agent_progress",
                    "agent_url",
                )
            )
        else:
            same_agent = item.agent_state_by == actor.account_id
            values = {
                "agent_state": AgentState(state),
                "agent_state_by": actor.account_id,
                "agent_state_at": utcnow(),
                "agent_note": badge_note[:NOTE_MAX] if badge_note else None,
                "agent_progress": progress,
                # The link stays while the same agent keeps reporting without a new one.
                "agent_url": url or (item.agent_url if same_agent else None),
            }
        for key, value in values.items():
            setattr(item, key, value)
        after: dict[str, Any] = {
            "state": state,
            "note": badge_note,
            "progress": progress,
            "by": actor.account_id,
            "url": values["agent_url"],
        }
        covered: list[Any] = [item]
        if new_thread:
            assert thread is not None
            if thread_row is None:
                thread_row = AgentThread(item_id=item.id, account_id=actor.account_id)
                session.add(thread_row)
            thread_row.ref, thread_row.url, thread_row.updated_at = (
                thread["ref"],
                thread["url"],
                utcnow(),
            )
            after["thread"] = thread
            covered.append(thread_row)
        if waiting is not None and state == "blocked":
            # Who the agent now waits on. `again`: it already waited on them, so no
            # waiting_on change follows and this report is what tells them (inbox `waiting`).
            after["waiting_on"] = waiting.id
            after["again"] = waiting_again
        if details is not None:
            after.update(details)
            if ask_comment_id is not None:
                after["comment_id"] = ask_comment_id
        # Who a review is for goes with the review flag, whoever reports it.
        item.review_for = reviewer.id if reviewer is not None else None
        if standing is not None:
            item.standing_line, item.standing_by, item.standing_at = (
                standing,
                actor.account_id,
                utcnow(),
            )
            after["status"] = standing
        cx.record(
            "item.reported",
            item_id=item.id,
            field="agent_state",
            before=before,
            after=after,
            covers=covered,
        )
        if ask is not None and comment:
            assert waiting is not None
            body = reply or headline
            comments.add_comment(
                session,
                actor,
                item.id,
                body_md=body,
                assignee=waiting.id,
                ask=ask,
                _id=ask_comment_id,
            )
        elif reply and comment:
            comments.add_comment(session, actor, item.id, body_md=reply)
        elif note and comment:
            comments.add_comment(
                session, actor, item.id, body_md=_comment_body(state, note, progress)
            )
        changes: dict[str, Any] = {}
        if finished is not None:
            changes["status"] = finished.id
        if waiting is not None and item.waiting_on_account_id != waiting.id:
            changes["waiting_on_account_id"] = waiting.id
        if assign_me and state == "working":
            current = _assignee_ids(session, item)
            if actor.account_id not in current:
                changes["assignees"] = [*current, actor.account_id]
        if changes:
            update_item(session, actor, item.id, changes)
        if state != "working" and (held is None or held.run_id is None):
            lease = session.get(WorkLease, item.id)
            if lease is not None and (
                lease.account_id == actor.account_id or state in ("done", "idle")
            ):
                session.delete(lease)  # bookkeeping: rides on this change's commit
    return item


def _check_claim(session: Session, item: Item, account_id: str) -> None:
    holder = item.agent_state_by
    if item.agent_state in (None, AgentState.failed) or holder is None or holder == account_id:
        return  # a failed run holds nothing
    lease = session.get(WorkLease, item.id)
    if lease is not None and lease.account_id == holder:
        session.refresh(lease)
        from slate.service import work

        if work.lease_state(lease) == "expired":
            return
    else:
        at = item.agent_state_at
        if at is not None and utcnow() - at >= CLAIM_FRESH:
            return
        # A parked badge (blocked or review) with no lease is nobody working: an agent asked
        # something on the card answers it (the work loop's `held_by_other` is the same rule).
        if item.agent_state != AgentState.working:
            from slate.service import work

            if work._is_asked(session, item.id, account_id):
                return
    who = session.get(Account, holder)
    raise Conflict(
        f"item {item.id} is already taken: {who.handle if who else holder} reported "
        f"'{item.agent_state.value}' on it"
    )


def _assignee_ids(session: Session, item: Item) -> list[str]:
    return list(
        session.scalars(select(ItemAssignee.account_id).where(ItemAssignee.item_id == item.id))
    )


def done_status(session: Session, item: Item) -> Status | None:
    """The status a finished item moves to: its list's first done-kind status (None if it is
    done or cancelled already; `Invalid` if the list has none)."""
    current = session.get(Status, item.status_id) if item.status_id else None
    if current is not None and current.kind in (StatusKind.done, StatusKind.cancelled):
        return None
    status = session.scalar(
        select(Status)
        .where(Status.list_id == item.list_id, Status.kind == StatusKind.done)
        .order_by(Status.position)
        .limit(1)
    )
    if status is None:
        raise Invalid("this item's list has no done status; report with complete=false")
    return status
