"""AGENTS.md rule 3, proven by walking the service package.

Every public function in `slate.service.*` must be classified below. A new function that is not
classified fails `test_every_service_function_is_classified`, so its author has to decide: is it
a read, or a mutation (then add a scenario that calls it)? Each mutation scenario must leave at
least one new event row whose actor is the caller. The flush guard (test_service_core) covers
writes that bypass these functions entirely.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Callable

import pytest
from sqlalchemy import func, select

import slate.service
from slate import webpush
from slate.models import Event, Source
from slate.service import (
    accounts,
    agent_rules,
    aiadd,
    attachments,
    automations,
    chat,
    chat_files,
    checklists,
    comments,
    doc_threads,
    docs,
    favorites,
    inbox,
    items,
    links,
    lists,
    permissions,
    push,
    recurrences,
    reminders,
    reports,
    sections,
    tags,
    trash,
    views,
    watchers,
    webhooks,
    work,
    workload,
)
from slate.service.core import Actor

READS = {
    "slate.service.item_reads": {"comment_dots"},
    # AI add: context, prompt and the model call read only; `fill` is in INFRASTRUCTURE.
    "slate.service.aiadd": {
        "ask",
        "call_model",
        "clean",
        "enabled",
        "first_sentence",
        "prompt",
        "queued",
    },
    "slate.service.connections": {"view", "list_connections"},
    "slate.service.collaboration": {"whiteboard_url", "project"},
    "slate.service.guests": {
        "workspace_agent",
        "is_guest",
        "viewer_is_guest",
        "workspace_agent_ids",
        "may_reach",
        "refuse",
        "reachable_handles",
    },
    "slate.service.permissions": {"list_shares", "effective", "who_can_see", "members"},
    "slate.service.audit": {"page", "facets", "export_csv"},
    "slate.service.accounts": {
        "list_accounts",
        "get_account",
        "account_count",
        "has_scope",
        "hash_token",
        "get_prefs",
        "list_tokens",
        "require_self_or_admin",
        "usage",
        # Writes only Token.last_used_at, the one listed bookkeeping exemption (core.BOOKKEEPING).
        "resolve_token",
        "touch_token",
        "needs_touch",
    },
    "slate.service.sections": {"get_section", "list_sections"},
    "slate.service.lists": {
        "get_list",
        "list_lists",
        "statuses_of",
        "resolve_status",
        "require_tasks",
    },
    "slate.service.tags": {"normalize", "list_tags", "get_tag", "usage_counts"},
    # The deny-list check runs inside every change (core.Change); it only reads.
    "slate.service.agent_rules": {"denied", "view", "covering", "check", "check_read"},
    "slate.service.links": {"links_of", "blocked_item_ids"},
    # Builds item views it is missing with items.views(); writes only its in-memory cache.
    "slate.service.item_cache": {"render", "warm", "trim"},
    "slate.service.items": {
        "depth_of",
        "descendants_of",
        "resolve_account",
        "get_item",
        "views",
        "item_events",
        "body_versions",
        "comments_of",
        "subtasks_of",
        "item_detail",
        "query_items",
        "priority_sort_key",
    },
    "slate.service.comments": {
        "get_comment",
        "clean_emoji",
        "mentions_in",
        "not_about_deleted_comment",
        "reactions_of",
        "comment_views",
        "open_asks",
        "authors_of",
        "reply_target",
    },
    "slate.service.chat": {
        "members_only_post",
        "place",
        "parse_channel",
        "get_message",
        "item_refs",
        "readable",
        "message_views",
        "messages",
        "thread",
        "unread",
        "unread_messages",
        "list_channels",
        "agent_turns",
        "channel_view",
        "default_title",
        "writable",
    },
    "slate.service.chat_files": {"view", "files_of", "unsent", "read_bytes"},
    "slate.service.checklists": {"get_checklist", "get_entry", "checklists_of"},
    "slate.service.attachments": {
        "decode_base64",
        "get_attachment",
        "attachments_of",
        "read_bytes",
        "split_pasted",
        "clean",
    },
    "slate.service.watchers": {"watchers_of", "watcher_ids", "auto_watches"},
    "slate.service.reports": {
        "flag_of",
        "check_url",
        "done_status",
        "check_thread",
        "thread_of",
        "first_line",
        "with_thread_ref",
    },
    "slate.service.work": {
        "lease_seconds",
        "check_later",
        "turn_asks",
        "pending_asks",
        "eligible",
        "is_takeable",
        "remaining",
        "lease_view",
        "context",
        "held_by",
        "as_event",
        "may_free_work",
        "agent_assignees",
        "nudge_fields",
    },
    "slate.service.agents": {"roster", "plan"},
    "slate.service.search": {"search", "like_pattern"},
    "slate.service.chatwake": {"lease_view", "turn_view", "remaining"},
    "slate.service.docwake": {"lease_view", "turn_view", "remaining", "section_around"},
    "slate.service.brief": {"brief", "closing_line", "default_ask", "files_note"},
    "slate.service.checkin": {"checkin", "queue", "why"},
    "slate.service.activity": {"activity"},
    "slate.service.favorites": {"list_favorites", "get_favorite", "favorite_out"},
    "slate.service.inbox": {
        "follows",
        "reason_for",
        "entries",
        "unread_count",
        "waits",
        "waiting_covers",
    },
    "slate.service.today": {"today"},
    "slate.service.trash": {"purge_due_at"},
    "slate.service.export": {"scope_of", "filename", "export_json", "export_csv", "backup_to"},
    "slate.service.views": {
        "validate_config",
        "view_out",
        "get_view",
        "list_views",
    },
    "slate.service.webhooks": {
        "view",
        "check_target",
        "private_targets",
        "list_webhooks",
        "get_webhook",
        "list_deliveries",
        "validate_url",
        "lead_hours",
        "wants_type",
    },
    "slate.service.deliveries": {
        "item_summary",
        "delivery_body",
        "matches",
        "latest_event_id",
        "retryable",
    },
    "slate.service.due": {"active_leads", "pending_stages"},
    "slate.service.automations": {
        "catalog",
        "clean_trigger",
        "clean_conditions",
        "clean_actions",
        "get_automation",
        "list_automations",
        "run_stats",
        "describe",
        "automation_view",
        "automation_views",
        "list_runs",
        "run_views",
        "trigger_fires",
        "conditions_hold",
        "signing_secret",
    },
    "slate.service.doc_compare": {
        "text_kind",
        "extract",
        "compare",
    },
    "slate.service.doc_hub": {"section_documents"},
    "slate.service.doc_threads": {
        "get_comment",
        "comments_of",
        "current_revision",
        "views",
        "view",
        "counts",
        "activity",
    },
    "slate.service.docs": {
        "parse_facts",
        "pinned_first",
        "date_in_name",
        "created_from_name",
        "get_doc",
        "fresh_doc",
        "unified",
        "body_ref",
        "root_of",
        "list_docs",
        "snippet",
        "doc_summary",
        "summaries",
        "embeds_in",
        "outline",
        "doc_out",
        "versions_of",
        "get_version",
        "revisions_of",
        "get_revision",
        "revision_out",
        "current_revisions",
        "tags_of",
        "drawer_listing",
        "version_out",
        "diff",
        "file_out",
        "get_file",
        "templates",
        "replace_section",
        "in_scope",
    },
    "slate.service.workload": {
        "capacity_view",
        "roster",
        "list_capacities",
        "get_capacity",
        "day_capacity",
        "boxes",
    },
    "slate.service.health": {
        "require_viewer",
        "report",
        "summary",
        "worst",
        "backup_status_path",
    },
    "slate.service.reminders": {
        "effective_at",
        "reminder_view",
        "reminders_of",
        "upcoming",
        "digest_settings",
        "digest_for",
    },
    "slate.service.push": {
        "subscription_view",
        "endpoint_allowed",
        "list_subscriptions",
        "chat_reason",
        "notification_for",
        "reasons_for",
        "renudge_minutes",
        "unread_by_channel",
        "renudge_notification",
        "wants",
        "wanted",
        "started_work",
        "watch_verb",
    },
    "slate.service.email": {"address", "compose", "reasons_for", "seen"},
    "slate.service.recurrences": {
        "get_recurrence",
        "list_recurrences",
        "occurrences",
        "next_occurrences",
        "recurrence_view",
        "render",
        "preview",
        "describe_rule",
        "next_after_done",
        "upcoming",
    },
}

# The change path itself and its helpers: not mutations of Slate data on their own.
INFRASTRUCTURE = {
    # Writes through items.update_item as Slate's own account (source system, session_ref
    # ai-add) after the request; tests/test_ai_add.py checks those events.
    "slate.service.aiadd": {"fill", "fill_later"},
    "slate.service.chatwake": {
        # Called inside chat.post / chat.edit's change (they take the `cx`).
        "on_posted",
        "on_mentioned",
        # Moves the lease only (`ChatWake.until` is bookkeeping): never an event.
        "heartbeat",
    },
    "slate.service.docwake": {
        # Called inside a page save's or a doc comment's change (it takes the `cx`).
        "on_mentioned",
        # Moves the lease only (`DocWake.until` is bookkeeping): never an event.
        "heartbeat",
    },
    # Called inside a comment or item change (it takes the `cx`); each file records its own
    # `item.attachment_added` (tests/test_inline_media.py).
    "slate.service.attachments": {"attach_pasted"},
    # Called inside an item, assignment or comment change (it takes the `cx`); each watcher it
    # adds records its own `item.watched` (field "auto", tests/test_watchers.py).
    "slate.service.watchers": {"auto_watch"},
    # Called inside a share change or an invitation cancel (it takes the `cx`); each link it
    # retires records its own `invitation.revoked` (tests/test_invitation_links.py).
    "slate.service.permissions": {"retire_links"},
    "slate.service.core": {
        "apply_change",
        "jsonable",
        "snapshot",
        "event_payload",
        "guard_flush",
        "guard_bulk",
        "write_lock_stats",
        "in_change",
    },
    "slate.service.bus": set(),
    "slate.service.errors": set(),
    # The permission hooks (read filter, write check) and their helpers.
    "slate.service.access": {
        "agent_projects",
        "hidden_agents",
        "reading_as",
        "scrub",
        "attach",
        "bind",
        "participant_ids",
        "check_object",
        "check_record",
        "compute",
        "doc_place",
        "doc_place_of",
        "digest_snapshot",
        "event_snapshots",
        "current",
        "level_rank",
        "read_criteria",
        "require_admin",
        "require_list_level",
        "check_scope",
        "unrestricted",
    },
    # Take an open Change (`cx`); they only run inside another mutation's apply_change().
    "slate.service.lists": {"move_item_to_status"},
    # Runs inside the change that marks an instance done (move_item_to_status).
    "slate.service.recurrences": {"on_item_done"},
    # Runs inside a person's status change (move_item_to_status), recording item.reported.
    "slate.service.reports": {"clear_flag"},
    # Runs at the end of every apply_change(), adding item.blocked / item.unblocked to it.
    "slate.service.links": {"settle"},
    # Engine cursors and the rows a walk queues are bookkeeping (no events).
    "slate.service.cursors": {"engine_cursor", "follow"},
    # Bookkeeping: write only `deliveries` rows (core.BOOKKEEPING_MODELS), the delivery log itself.
    "slate.service.deliveries": {
        "fan_out",
        "claim_due",
        "recover_claimed",
        "prepare_attempt",
        "record_attempt",
        "prune",
    },
}


def _setup(db):
    account, _, plaintext = accounts.bootstrap(db, handle="alex", display_name="Alex")
    actor = Actor(account_id=account.id, source=Source.api, session_ref="walk")
    return actor, accounts.resolve_token(db, plaintext)


def _create_account(db, actor, principal):
    accounts.create_account(db, actor, handle="agent", display_name="Agent", kind="agent")


def _create_token(db, actor, principal):
    accounts.create_token(db, actor, account_id=actor.account_id, scopes=["read"])


def _revoke_token(db, actor, principal):
    token, _ = accounts.create_token(db, actor, account_id=actor.account_id, scopes=["read"])
    accounts.revoke_token(db, actor, token_id=token.id, by=principal)


def _section(db, actor):
    return sections.create_section(db, actor, name="Personal", audience="personal")


def _list(db, actor):
    return lists.create_list(db, actor, section_id=_section(db, actor).id, name="Todo")


def _item(db, actor, **fields):
    return items.create_item(db, actor, list_id=_list(db, actor).id, title="Thing", **fields)


def _view(db, actor):
    return views.create_view(
        db, actor, name="Mine", scope_kind="list", scope_id=_list(db, actor).id, config={}
    )


def _comment(db, actor):
    return comments.add_comment(db, actor, _item(db, actor).id, body_md="hi @alex")


def _message(db, actor):
    return chat.post(db, actor, f"list:{_list(db, actor).id}", body_md="hi @alex")


def _checklist(db, actor):
    return checklists.create_checklist(db, actor, _item(db, actor).id, name="List", items=["a"])


def _entry(db, actor):
    return checklists.add_entry(db, actor, _checklist(db, actor).id, title="call")


def _chat_file(db, actor):
    return chat_files.upload(db, actor, "general", filename="a.png", data=b"png")


def _attachment(db, actor):
    return attachments.add_attachment(
        db, actor, _item(db, actor).id, filename="a.txt", data=b"hello"
    )


def _doc(db, actor, **kw):
    kw.setdefault("title", "Plan")
    return docs.create_doc(db, actor, **kw)


def _archived_doc(db, actor):
    return docs.archive_doc(db, actor, _doc(db, actor).id)


def _doc_file(db, actor):
    return docs.add_file(db, actor, _doc(db, actor).id, filename="a.png", data=b"png")


def _doc_comment(db, actor, **kw):
    return doc_threads.add_comment(db, actor, _doc(db, actor).id, body_md="hi", **kw)


def _suggestion(db, actor):
    doc = _doc(db, actor, body_md="Price 12")
    return doc_threads.add_comment(
        db, actor, doc.id, body_md="x", anchor={"quote": "12"}, suggestion_md="14"
    )


def _file_doc(db, actor):
    doc = _doc(db, actor, kind="file", title="Spec")
    docs.add_revision(db, actor, doc.id, filename="s.pdf", data=b"%PDF", note="first")
    return doc


def _webhook(db, actor):
    hook, _ = webhooks.create_webhook(db, actor, url="https://h.example.com", events=["*"])
    return hook


def _rule(db, actor):
    return recurrences.create_recurrence(
        db, actor, list_id=_list(db, actor).id, title_template="Trash {date}", rrule="FREQ=DAILY"
    )


def _adopt(db, actor):
    rule = _rule(db, actor)
    item = items.create_item(db, actor, list_id=rule.list_id, title="Trash", due_at="2030-01-09")
    return recurrences.adopt_instance(db, actor, rule.id, item.id)


def _automation(db, actor):
    return automations.create_automation(
        db,
        actor,
        name="Tag shipped",
        list_id=_list(db, actor).id,
        trigger={"type": "status_changed", "to_kind": "done"},
        actions=[{"type": "add_tag", "tag": "shipped"}],
    )


def _reminder(db, actor):
    return reminders.add_reminder(db, actor, _item(db, actor).id, at="2030-01-09T15:00:00Z")


def _trashed(db, actor):
    return trash.trash_item(db, actor, _item(db, actor).id)


def _subscribe(db, actor, endpoint="https://push.example.com/abc"):
    return push.subscribe(
        db,
        actor,
        endpoint=endpoint,
        p256dh=webpush.b64url(b"\x04" + b"\x01" * 64),
        auth=webpush.b64url(b"\x02" * 16),
        label="Phone",
    )


def _pin(db, actor):
    return favorites.add_favorite(db, actor, kind="list", target_id=_list(db, actor).id)


def _reorder(db, actor):
    lst = _list(db, actor)
    first = favorites.add_favorite(db, actor, kind="list", target_id=lst.id)
    second = favorites.add_favorite(db, actor, kind="section", target_id=lst.section_id)
    return favorites.reorder_favorites(db, actor, [second.id, first.id])


def _unlink(db, actor):
    a, b = _linked(db, actor)
    links.remove_link(db, actor, a.id, kind="blocks", other_id=b.id)


def _linked(db, actor):
    a = _item(db, actor)
    b = items.create_item(db, actor, list_id=a.list_id, title="Other")
    links.add_link(db, actor, a.id, kind="blocks", other_id=b.id)
    return a, b


def _guest(db, actor):
    return accounts.create_account(
        db, actor, handle="guest", display_name="Guest", kind="person", role="guest"
    )


def _share(db, actor, principal):
    return permissions.share(
        db,
        actor,
        by=principal,
        account_id=_guest(db, actor).id,
        scope_kind="list",
        scope_id=_list(db, actor).id,
        level="view",
    )


def _invite(db, actor, principal):
    from slate.service import collaboration

    section = sections.create_section(db, actor, name="Customer", audience="work")
    permissions.set_private(
        db, actor, by=principal, scope_kind="section", scope_id=section.id, private=True
    )
    return collaboration.issue(
        db, actor, by=principal, section_id=section.id, handle="invited-guest", display_name="Guest"
    )


def _revoke_invitation(db, actor, principal):
    from slate.service import collaboration

    row, _ = _invite(db, actor, principal)
    collaboration.revoke(db, actor, by=principal, invitation_id=row.id)


def _reissue_invitation(db, actor, principal):
    from slate.service import collaboration

    row, _ = _invite(db, actor, principal)
    return collaboration.reissue(db, actor, by=principal, invitation_id=row.id)


MUTATIONS: dict[str, dict[str, Callable]] = {
    "slate.service.aiadd": {
        "file": lambda db, a, p: aiadd.file(db, a, _list(db, a).id, "Water the plants.", {}),
    },
    "slate.service.connections": {
        # Issuer at exchange, exact agent at heartbeat/revoke, no supplied session_ref:
        # test_connection_lifecycle_events_have_exact_actor_and_no_session_input.
        "issue": None,
        "exchange": None,
        "heartbeat": None,
        "revoke": None,
    },
    "slate.service.collaboration": {
        "issue": _invite,
        "revoke": _revoke_invitation,
        "accept": None,  # guest/ui actor rather than issuer; test_collaboration checks attribution
        # Needs an accepted (guest-attributed) invitation first: tests/test_guest_sessions.py
        # checks the admin's `session.extended`.
        "extend": None,
        "reissue": _reissue_invitation,
    },
    "slate.service.sessions": {
        # Records as the `slate` system agent from the scheduler: tests/test_guest_sessions.py.
        "notify_expiring": None,
    },
    "slate.service.permissions": {
        "set_role": lambda db, a, p: permissions.set_role(
            db, a, by=p, account_id=_guest(db, a).id, role="member"
        ),
        "share": _share,
        "revoke_share": lambda db, a, p: permissions.revoke_share(
            db, a, by=p, share_id=_share(db, a, p).id
        ),
        "set_private": lambda db, a, p: permissions.set_private(
            db, a, by=p, scope_kind="list", scope_id=_list(db, a).id, private=True
        ),
    },
    "slate.service.links": {
        "add_link": lambda db, a, p: _linked(db, a),
        "remove_link": lambda db, a, p: _unlink(db, a),
    },
    "slate.service.inbox": {
        "mark": lambda db, a, p: inbox.mark(db, a, cleared=True, everything=True),
    },
    "slate.service.favorites": {
        "add_favorite": lambda db, a, p: _pin(db, a),
        "remove_favorite": lambda db, a, p: favorites.remove_favorite(db, a, _pin(db, a).id),
        "reorder_favorites": lambda db, a, p: _reorder(db, a),
    },
    "slate.service.webhooks": {
        "create_webhook": lambda db, a, p: _webhook(db, a),
        "update_webhook": lambda db, a, p: webhooks.update_webhook(
            db, a, _webhook(db, a).id, {"events": ["item.due"]}, by=p
        ),
        "create_test_delivery": lambda db, a, p: webhooks.create_test_delivery(
            db, a, _webhook(db, a).id, by=p
        ),
    },
    # Both act as the `slate` system account, not the caller: covered in tests/test_due_events.py.
    "slate.service.due": {"system_actor": None, "emit_due_events": None},
    "slate.service.automations": {
        "create_automation": lambda db, a, p: _automation(db, a),
        "update_automation": lambda db, a, p: automations.update_automation(
            db, a, _automation(db, a).id, {"enabled": False}
        ),
        "delete_automation": lambda db, a, p: automations.delete_automation(
            db, a, _automation(db, a).id
        ),
        "rotate_secret": lambda db, a, p: automations.rotate_secret(db, a, _automation(db, a).id),
        # Act as the `slate` system agent (session_ref automation:<id>); the run log rows are
        # bookkeeping. Covered in tests/test_automations.py.
        "system_actor_for": None,
        "run_rule": None,
        "run_now": None,
        "start": None,
        "process": None,
    },
    "slate.service.docs": {
        "create_doc": lambda db, a, p: _doc(db, a),
        "update_doc": lambda db, a, p: docs.update_doc(db, a, _doc(db, a).id, {"body_md": "x"}),
        "move_doc": lambda db, a, p: docs.move_doc(
            db, a, _doc(db, a).id, {"parent_id": _doc(db, a).id}
        ),
        "archive_doc": lambda db, a, p: _archived_doc(db, a),
        "unarchive_doc": lambda db, a, p: docs.unarchive_doc(db, a, _archived_doc(db, a).id),
        "delete_doc": lambda db, a, p: docs.delete_doc(db, a, _archived_doc(db, a).id),
        "restore_version": lambda db, a, p: docs.restore_version(db, a, _doc(db, a).id, 1),
        "duplicate_doc": lambda db, a, p: docs.duplicate_doc(db, a, _doc(db, a).id),
        "add_file": lambda db, a, p: _doc_file(db, a),
        "remove_file": lambda db, a, p: docs.remove_file(db, a, _doc_file(db, a).id),
        "create_task": lambda db, a, p: docs.create_task(
            db, a, _doc(db, a, list_id=_list(db, a).id).id, title="Call"
        ),
        "add_revision": lambda db, a, p: _file_doc(db, a),
        "restore_revision": lambda db, a, p: docs.restore_revision(db, a, _file_doc(db, a).id, 1),
        "promote_attachment": lambda db, a, p: docs.promote_attachment(
            db, a, _attachment(db, a).id
        ),
        "pin_revision": lambda db, a, p: docs.pin_revision(db, a, _file_doc(db, a).id, 1),
        "unpin_revision": lambda db, a, p: docs.unpin_revision(
            db, a, docs.pin_revision(db, a, _file_doc(db, a).id, 1).id
        ),
        "pin_to_top": lambda db, a, p: docs.pin_to_top(db, a, _file_doc(db, a).id),
        "unpin_from_top": lambda db, a, p: docs.unpin_from_top(
            db, a, docs.pin_to_top(db, a, _file_doc(db, a).id).id
        ),
        "to_drawer": lambda db, a, p: docs.to_drawer(
            db, a, _doc(db, a, section_id=_section(db, a).id).id
        ),
    },
    "slate.service.doc_threads": {
        "add_comment": lambda db, a, p: _doc_comment(db, a),
        "edit_comment": lambda db, a, p: doc_threads.edit_comment(
            db, a, _doc_comment(db, a).id, body_md="changed"
        ),
        "resolve_comment": lambda db, a, p: doc_threads.resolve_comment(
            db, a, _doc_comment(db, a, anchor={"quote": "Plan"}).id
        ),
        "reopen_comment": lambda db, a, p: doc_threads.reopen_comment(
            db,
            a,
            doc_threads.resolve_comment(db, a, _doc_comment(db, a, anchor={"quote": "Plan"}).id).id,
        ),
        "assign_comment": lambda db, a, p: doc_threads.assign_comment(
            db, a, _doc_comment(db, a).id, "alex"
        ),
        "accept_suggestion": lambda db, a, p: doc_threads.accept_suggestion(
            db, a, _suggestion(db, a).id
        ),
    },
    "slate.service.workload": {
        "set_capacity": lambda db, a, p: workload.set_capacity(
            db, a, a.account_id, {"hours_per_day": 6, "work_days": [0, 1, 2]}
        ),
        "reset_capacity": lambda db, a, p: workload.reset_capacity(
            db, a, workload.set_capacity(db, a, a.account_id, {"tasks_per_day": 3})["account_id"]
        ),
    },
    "slate.service.trash": {
        "trash_item": lambda db, a, p: trash.trash_item(db, a, _item(db, a).id),
        "restore_item": lambda db, a, p: trash.restore_item(db, a, _trashed(db, a).id),
        "trash_items": lambda db, a, p: trash.trash_items(db, a, [_item(db, a)]),
        "restore_items": lambda db, a, p: trash.restore_items(db, a, [_trashed(db, a)]),
        "purge_item": lambda db, a, p: trash.purge_item(db, a, _trashed(db, a).id),
        "empty_trash": lambda db, a, p: (_trashed(db, a), trash.empty_trash(db, a)),
        # Runs as the `slate` system agent: covered in tests/test_trash.py.
        "purge_expired": None,
    },
    "slate.service.health": {
        # Records as the `slate` system agent from the scheduler: covered in tests/test_health.py.
        "record_change": None,
    },
    "slate.service.reminders": {
        "add_reminder": lambda db, a, p: _reminder(db, a),
        "remove_reminder": lambda db, a, p: reminders.remove_reminder(db, a, _reminder(db, a).id),
        # Record as the `slate` system agent: covered in tests/test_reminders.py.
        "fire_reminders": None,
        "send_digests": None,
        # Writes inside a recurrence's change (`cx.create`): covered in tests/test_recurrences.py.
        "copy_due_alerts": None,
    },
    "slate.service.push": {
        "subscribe": lambda db, a, p: _subscribe(db, a),
        "unsubscribe": lambda db, a, p: push.unsubscribe(db, a, _subscribe(db, a).id),
        "unsubscribe_endpoint": lambda db, a, p: push.unsubscribe_endpoint(
            db, a, _subscribe(db, a).endpoint
        ),
        # The engine: bookkeeping on the subscription and on `push_deliveries` (the receipts
        # table is the delivery log, core.BOOKKEEPING_MODELS), and `push.expired` as its owner
        # with source `system`. Covered in tests/test_push.py and tests/test_push_retry.py.
        "process": None,
        "renudge": None,
        "send_test": None,
    },
    "slate.service.email": {
        # The engine: bookkeeping on `email_deliveries` only (core.BOOKKEEPING_MODELS) and the
        # `email` cursor; send_test writes nothing. Covered in tests/test_email_notify.py.
        "process": None,
        "send_test": None,
    },
    "slate.service.accounts": {
        "create_account": _create_account,
        "create_token": _create_token,
        "revoke_token": _revoke_token,
        "set_prefs": lambda db, a, p: accounts.set_prefs(db, a, {"theme": "dark"}),
        "update_account": lambda db, a, p: accounts.update_account(
            db,
            a,
            account_id=accounts.create_account(
                db, a, handle="agent2", display_name="Agent 2", kind="agent"
            ).id,
            by=p,
            display_name="Renamed",
            disabled=True,
        ),
        "bootstrap": None,  # covered separately: it creates its own actor
    },
    "slate.service.sections": {
        "create_section": lambda db, a, p: _section(db, a),
        "update_section": lambda db, a, p: sections.update_section(
            db, a, _section(db, a).id, name="Home"
        ),
    },
    "slate.service.lists": {
        "create_list": lambda db, a, p: _list(db, a),
        "update_list": lambda db, a, p: lists.update_list(db, a, _list(db, a).id, name="Later"),
        "set_statuses": lambda db, a, p: lists.set_statuses(
            db, a, _list(db, a).id, [{"name": "todo"}, {"name": "done", "kind": "done"}]
        ),
    },
    "slate.service.agent_rules": {
        "set_denied": lambda db, a, p: agent_rules.set_denied(db, a, ["export"]),
    },
    "slate.service.tags": {
        "create_tag": lambda db, a, p: tags.create_tag(db, a, name="h3"),
        "ensure_tags": lambda db, a, p: tags.ensure_tags(db, a, ["h3", "ai"]),
        "update_tag": lambda db, a, p: tags.update_tag(
            db, a, tags.create_tag(db, a, name="h3").id, name="h4"
        ),
        "merge_tag": lambda db, a, p: tags.merge_tag(
            db,
            a,
            items.update_item(db, a, _item(db, a).id, {"tags": ["h3", "ai"]}) and "h3",
            into="ai",
        ),
        "delete_tag": lambda db, a, p: tags.delete_tag(
            db, a, items.update_item(db, a, _item(db, a).id, {"tags": ["h3"]}) and "h3"
        ),
    },
    "slate.service.items": {
        "create_item": lambda db, a, p: _item(db, a, body_md="first"),
        "split": lambda db, a, p: items.split(db, a, _item(db, a).id, [{"title": "a"}]),
        "update_item": lambda db, a, p: items.update_item(
            db, a, _item(db, a).id, {"body_md": "x", "status": "done", "due_at": "2026-11-01"}
        ),
        "archive_item": lambda db, a, p: items.archive_item(db, a, _item(db, a).id),
        "unarchive_item": lambda db, a, p: items.unarchive_item(
            db, a, items.archive_item(db, a, _item(db, a).id).id
        ),
        "delete_item": lambda db, a, p: items.delete_item(db, a, _item(db, a).id),
        "bulk_update": lambda db, a, p: items.bulk_update(
            db, a, [_item(db, a).id], {"priority": "high"}
        ),
    },
    "slate.service.views": {
        "create_view": lambda db, a, p: _view(db, a),
        "update_view": lambda db, a, p: views.update_view(db, a, _view(db, a).id, {"name": "B"}),
        "set_archived": lambda db, a, p: views.set_archived(db, a, _view(db, a).id, archived=True),
        # Bootstrap seeds the landing views, so a second call has nothing to make:
        # test_bootstrap_writes_events_attributed_to_the_new_account checks those events.
        "seed_default_views": None,
    },
    "slate.service.comments": {
        "add_comment": lambda db, a, p: comments.add_comment(db, a, _item(db, a).id, body_md="hi"),
        "edit_comment": lambda db, a, p: comments.edit_comment(
            db, a, comments.add_comment(db, a, _item(db, a).id, body_md="hi").id, body_md="yo"
        ),
        "react": lambda db, a, p: comments.react(db, a, _comment(db, a).id, emoji="👍"),
        "unreact": lambda db, a, p: comments.unreact(
            db, a, comments.react(db, a, _comment(db, a).id, emoji="👍").id, emoji="👍"
        ),
        "assign_comment": lambda db, a, p: comments.assign_comment(
            db, a, _comment(db, a).id, "alex"
        ),
        "resolve_comment": lambda db, a, p: comments.resolve_comment(
            db,
            a,
            comments.add_comment(db, a, _item(db, a).id, body_md="?", assignee="alex").id,
        ),
        "delete_comment": lambda db, a, p: comments.delete_comment(
            db, a, comments.react(db, a, _comment(db, a).id, emoji="👍").id
        ),
    },
    "slate.service.chat": {
        "post": lambda db, a, p: _message(db, a),
        "edit": lambda db, a, p: chat.edit(db, a, _message(db, a).id, body_md="yo"),
        "delete": lambda db, a, p: chat.delete(db, a, _message(db, a).id),
        "react": lambda db, a, p: chat.react(db, a, _message(db, a).id, emoji="👍"),
        "unreact": lambda db, a, p: chat.unreact(
            db, a, chat.react(db, a, _message(db, a).id, emoji="👍").id, emoji="👍"
        ),
        "make_task": lambda db, a, p: chat.make_task(db, a, _message(db, a).id),
        "mark_read": lambda db, a, p: chat.mark_read(db, a, _message(db, a).channel),
        "mark_all_read": lambda db, a, p: _message(db, a) and chat.mark_all_read(db, a),
    },
    "slate.service.chat_files": {
        "upload": lambda db, a, p: _chat_file(db, a),
        "discard": lambda db, a, p: chat_files.discard(db, a, _chat_file(db, a).id),
        # Scheduler hook, as the `slate` system agent: covered in tests/test_chat_files.py.
        "sweep_unsent": None,
    },
    "slate.service.chatwake": {
        # Only an agent mentioned by a person holds a turn; the walk's actor is a person.
        # Each writes `message.agent_turn` by the agent (or `slate` for expire):
        # tests/test_chat_wake.py.
        "next_turn": None,
        "finish": None,
        "reconcile": None,  # audited in tests/test_run_safety.py
        "expire": None,
    },
    "slate.service.docwake": {
        # The same shape as chatwake: each writes `doc.agent_turn` by the agent (or `slate` for
        # expire): tests/test_doc_wake.py.
        "next_turn": None,
        "finish": None,
        "reconcile": None,
        "expire": None,
    },
    "slate.service.checklists": {
        "create_checklist": lambda db, a, p: _checklist(db, a),
        "update_checklist": lambda db, a, p: checklists.update_checklist(
            db, a, _checklist(db, a).id, {"name": "Steps"}
        ),
        "delete_checklist": lambda db, a, p: checklists.delete_checklist(
            db, a, _checklist(db, a).id
        ),
        "add_entry": lambda db, a, p: checklists.add_entry(
            db, a, _checklist(db, a).id, title="call"
        ),
        "update_entry": lambda db, a, p: checklists.update_entry(
            db, a, _entry(db, a).id, {"done": True, "title": "ring"}
        ),
        "delete_entry": lambda db, a, p: checklists.delete_entry(db, a, _entry(db, a).id),
    },
    "slate.service.attachments": {
        "add_attachment": lambda db, a, p: _attachment(db, a),
        "remove_attachment": lambda db, a, p: attachments.remove_attachment(
            db, a, _attachment(db, a).id
        ),
    },
    "slate.service.reports": {
        "report": lambda db, a, p: reports.report(
            db, a, _item(db, a).id, state="done", note="shipped", waiting_on=None
        ),
    },
    "slate.service.revert": {
        # The inverse change's event carries `session_ref` `revert:<event id>`, not the
        # caller's; tests/test_revert.py checks the actor, source and ref of that event.
        "revert_event": None,
    },
    "slate.service.work": {
        "take": lambda db, a, p: work.take(db, a, _item(db, a).id),
        "next_task": lambda db, a, p: (_item(db, a, assignees=["alex"]), work.next_task(db, a)),
        "heartbeat": lambda db, a, p: work.heartbeat(
            db,
            a,
            (lease := work.take(db, a, _item(db, a).id)).item_id,
            progress=30,
            run_id=lease.run_id,
        ),
        "finish": lambda db, a, p: work.finish(
            db,
            a,
            (lease := work.take(db, a, _item(db, a).id)).item_id,
            outcome="done",
            run_id=lease.run_id,
        ),
        "reconcile": lambda db, a, p: work.reconcile(
            db,
            a,
            (lease := work.take(db, a, _item(db, a).id)).item_id,
            run_id=lease.run_id,
            state="stopped",
            evidence="test control confirmed",
        ),
        "expire_leases": None,  # scheduler hook: writes as `slate` (tests/test_work.py)
        "request_run": lambda db, a, p: (
            _create_account(db, a, p),
            work.request_run(db, a, _item(db, a, assignees=["agent"]).id),
        ),
    },
    "slate.service.item_reads": {
        # Needs someone else's comment first (an event by them): tests/test_comment_dots.py
        # checks the one `item.read` event is the caller's.
        "mark_read": None,
    },
    "slate.service.watchers": {
        "watch": lambda db, a, p: watchers.watch(db, a, _item(db, a).id),
        "unwatch": lambda db, a, p: watchers.unwatch(
            db, a, (i := _item(db, a)).id, watchers.watch(db, a, i.id)[0].id
        ),
        "set_watchers": lambda db, a, p: watchers.set_watchers(db, a, _item(db, a).id, ["alex"]),
    },
    "slate.service.recurrences": {
        "create_recurrence": lambda db, a, p: _rule(db, a),
        "update_recurrence": lambda db, a, p: recurrences.update_recurrence(
            db, a, _rule(db, a).id, {"lead_days": 3}
        ),
        "pause_recurrence": lambda db, a, p: recurrences.pause_recurrence(db, a, _rule(db, a).id),
        "resume_recurrence": lambda db, a, p: recurrences.resume_recurrence(
            db, a, recurrences.pause_recurrence(db, a, _rule(db, a).id).id
        ),
        "end_recurrence": lambda db, a, p: recurrences.end_recurrence(db, a, _rule(db, a).id),
        "run_now": lambda db, a, p: recurrences.run_now(db, a, _rule(db, a).id),
        "adopt_instance": lambda db, a, p: _adopt(db, a),
        # Acts as each rule's creator with source `system`; covered separately below.
        "materialize": None,
    },
}


def _public_functions() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for info in pkgutil.iter_modules(slate.service.__path__, "slate.service."):
        module = importlib.import_module(info.name)
        found[info.name] = {
            name
            for name, obj in vars(module).items()
            if inspect.isfunction(obj) and obj.__module__ == info.name and not name.startswith("_")
        }
    return found


def test_every_service_function_is_classified():
    for module, names in _public_functions().items():
        known = (
            READS.get(module, set())
            | INFRASTRUCTURE.get(module, set())
            | set(MUTATIONS.get(module, {}))
        )
        assert names <= known, f"unclassified in {module}: {sorted(names - known)}"
        assert known <= names, f"stale classification in {module}: {sorted(known - names)}"


def test_no_read_opens_a_change():
    """A function listed under READS is never run by a scenario, so one that opens a change
    would escape the actor check below: it belongs under MUTATIONS."""
    opens = []
    for module, names in READS.items():
        mod = importlib.import_module(module)
        for name in names:
            source = inspect.getsource(getattr(mod, name))
            if "apply_change(" in source:
                opens.append(f"{module}.{name}")
    assert not opens, f"listed as reads but open a change: {sorted(opens)}"


SCENARIOS = [
    (module, name, fn) for module, fns in MUTATIONS.items() for name, fn in fns.items() if fn
]


@pytest.mark.parametrize(("module", "name", "scenario"), SCENARIOS, ids=[s[1] for s in SCENARIOS])
def test_every_mutation_writes_an_event_with_its_actor(db, module, name, scenario):
    actor, principal = _setup(db)
    before = db.scalar(select(func.count()).select_from(Event))
    scenario(db, actor, principal)
    new = list(db.scalars(select(Event).order_by(Event.id).offset(before)))
    assert new, f"{name} wrote no event"
    assert all(e.actor_id == actor.account_id for e in new)
    assert all(e.session_ref == "walk" and e.source == Source.api for e in new)


def test_bootstrap_writes_events_attributed_to_the_new_account(db):
    account, _, _ = accounts.bootstrap(db, handle="alex", display_name="Alex")
    events = list(db.scalars(select(Event)))
    assert len(events) == 4  # account, token, two seeded views
    assert {e.actor_id for e in events} == {account.id}


def test_materialize_writes_events_as_the_rule_creator_via_system(db):
    actor, _ = _setup(db)
    rule = _rule(db, actor)
    before = db.scalar(select(func.count()).select_from(Event))
    assert recurrences.materialize(db)
    new = list(db.scalars(select(Event).order_by(Event.id).offset(before)))
    assert new
    assert {(e.actor_id, e.source, e.session_ref) for e in new} == {
        (actor.account_id, Source.system, f"recurrence:{rule.id}")
    }
