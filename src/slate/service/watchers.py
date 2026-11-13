"""Watchers: accounts following an item, like ClickUp's. A watcher hears about every change to
the item: the Inbox (service/inbox.py, reason `watching` for activity beyond comments, status and
the main fields), Web Push (push reason `watching`), an agent's check-in (`watching`), and a
webhook with `assignee_me` (`deliveries.matches`), so an agent can follow work it is not assigned
to. Anyone who can edit an item can add or remove any watcher; below edit (a view or
comment share) a caller watches and unwatches only itself. Being removed by someone else is
inbox news (reason `watching`, service/inbox.py), so a watch never vanishes silently.

Auto-watch (ClickUp's defaults): a person starts watching an item they create, are assigned to,
or comment on. Each is an account pref (`auto_watch` = {created, assigned, commented: bool});
agents default to off for all three, so an agent's check-in is not flooded by work it filed.
An auto-watch is an `item.watched` event with `field` "auto": it is not news to anyone (being
assigned already is), unlike someone adding you by hand."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from slate.models import Account, AccountKind, Watcher
from slate.service import access
from slate.service.accounts import AUTO_WATCH as AUTO_WATCH
from slate.service.core import Actor, Change, apply_change
from slate.service.due import SYSTEM_HANDLE  # Slate's own account never watches anything
from slate.service.items import get_item, resolve_account


def watchers_of(session: Session, item_id: str) -> list[Account]:
    return list(
        session.scalars(
            select(Account)
            .join(Watcher, Watcher.account_id == Account.id)
            .where(Watcher.item_id == item_id)
            .order_by(Account.handle)
        )
    )


def watcher_ids(session: Session, item_id: str) -> set[str]:
    return set(session.scalars(select(Watcher.account_id).where(Watcher.item_id == item_id)))


def auto_watches(account: Account, why: str) -> bool:
    """Whether `account` starts watching an item it `why` (created / assigned / commented)."""
    if account.handle == SYSTEM_HANDLE:
        return False
    chosen = (account.prefs_json or {}).get("auto_watch")
    if isinstance(chosen, dict) and isinstance(chosen.get(why), bool):
        return chosen[why]
    return account.kind == AccountKind.person


def auto_watch(cx: Change, item_id: str, account_ids: Sequence[str], why: str) -> None:
    """Inside an open change: add each account that auto-watches on `why` and is not watching
    yet. Recorded as `item.watched` with field "auto"."""
    session = cx.session
    pending = {(o.item_id, o.account_id) for o in session.new if isinstance(o, Watcher)}
    with session.no_autoflush:
        for acct_id in dict.fromkeys(account_ids):
            account = session.get(Account, acct_id)
            if account is None or not auto_watches(account, why):
                continue
            if (item_id, acct_id) in pending or session.get(Watcher, (item_id, acct_id)):
                continue
            pending.add((item_id, acct_id))
            w = Watcher(item_id=item_id, account_id=acct_id)
            session.add(w)
            cx.record("item.watched", item_id=item_id, field="auto", after=acct_id, covers=[w])


def _someone(session: Session, actor: Actor, item_list: str | None, account: str | None) -> str:
    """The account a watch call is about: the caller, or (with edit on the list) anyone."""
    acct_id = resolve_account(session, account).id if account else actor.account_id
    if acct_id != actor.account_id:
        access.require_list_level(
            session, item_list, access.EDIT, "add or remove someone else as a watcher"
        )
    return acct_id


def watch(
    session: Session, actor: Actor, item_id: str, account: str | None = None
) -> list[Account]:
    """Add `account` (handle or id; default the caller) as a watcher. Idempotent. Someone
    else needs edit on the item's list."""
    item = get_item(session, item_id)
    acct_id = _someone(session, actor, item.list_id, account)
    if session.get(Watcher, (item.id, acct_id)) is None:
        with apply_change(session, actor) as cx:
            w = Watcher(item_id=item.id, account_id=acct_id)
            cx.session.add(w)
            cx.record("item.watched", item_id=item.id, after=acct_id, covers=[w])
    return watchers_of(session, item.id)


def unwatch(
    session: Session, actor: Actor, item_id: str, account: str | None = None
) -> list[Account]:
    item = get_item(session, item_id)
    acct_id = _someone(session, actor, item.list_id, account)
    w = session.get(Watcher, (item.id, acct_id))
    if w is not None:
        with apply_change(session, actor) as cx:
            cx.record("item.unwatched", item_id=item.id, before=acct_id, covers=[w])
            session.delete(w)
    return watchers_of(session, item.id)


def set_watchers(
    session: Session, actor: Actor, item_id: str, refs: Sequence[str]
) -> list[Account]:
    """Replace the watcher set (handles or ids)."""
    item = get_item(session, item_id)
    wanted = {resolve_account(session, r).id for r in refs}
    have = watcher_ids(session, item.id)
    with apply_change(session, actor):
        for acct in sorted(wanted - have):
            watch(session, actor, item.id, acct)
        for acct in sorted(have - wanted):
            unwatch(session, actor, item.id, acct)
    return watchers_of(session, item.id)
