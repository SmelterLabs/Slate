"""Baseline: the whole schema in one revision

This revision creates every table, index and trigger exactly as SQLite stores them (the
statements are the `sqlite_master` text, in creation order), so a new database has exactly the
schema of one that grew through Slate 0.1.0's step-by-step history: the same tables, columns,
types, defaults, indexes, constraints and triggers. A database already at this
revision or later upgrades as before. A database at an older revision must first be upgraded
to this one with Slate 0.1.0 (`alembic/env.py` refuses it with that advice).

Triggers here repeat `slate.models.TRIGGERS` verbatim; `tests/test_migrations.py` checks it.

Revision ID: a7e2c9d4f1b3
Revises:
Create Date: 2026-11-05 12:00:00
"""

# ruff: noqa: E501  (the statements are SQLite's stored text, line for line)

from __future__ import annotations

from alembic import op

revision = "a7e2c9d4f1b3"
down_revision = None
branch_labels = None
depends_on = None

TABLE_NAMES = [
    "tags",
    "events",
    "tokens",
    "deliveries",
    "statuses",
    "attachments",
    "body_versions",
    "item_assignees",
    "item_tags",
    "comments",
    "webhooks",
    "views",
    "checklists",
    "item_watchers",
    "attachment_blobs",
    "checklist_items",
    "comment_reactions",
    "inbox_marks",
    "inbox_state",
    "engine_cursors",
    "automations",
    "automation_runs",
    "push_subscriptions",
    "reminders",
    "item_links",
    "channel_reads",
    "message_reactions",
    "doc_versions",
    "favorites",
    "recurrences",
    "shares",
    "accounts",
    "lists",
    "sections",
    "work_leases",
    "agent_threads",
    "invitations",
    "chat_wakes",
    "items",
    "capacities",
    "doc_files",
    "doc_comments",
    "doc_tags",
    "docs",
    "push_deliveries",
    "agent_connections",
    "workspace_settings",
    "doc_wakes",
    "messages",
    "item_reads",
    "email_deliveries",
]

TABLES = [
    (
        "CREATE TABLE tags (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tname VARCHAR(60) NOT NULL, \n"
        "\tcolor VARCHAR(16), \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT ck_tags_name_lowercase CHECK (name = lower(name)), \n"
        "\tUNIQUE (name)\n"
        ")"
    ),
    (
        "CREATE TABLE events (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\titem_id VARCHAR(12), \n"
        "\tsubject_id VARCHAR(12), \n"
        "\tactor_id VARCHAR(12) NOT NULL, \n"
        "\tsource VARCHAR(16) NOT NULL, \n"
        "\ttype VARCHAR(40) NOT NULL, \n"
        "\tfield VARCHAR(60), \n"
        "\tbefore_json JSON, \n"
        "\tafter_json JSON, \n"
        "\tsession_ref VARCHAR(200), \n"
        "\tat VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(actor_id) REFERENCES accounts (id), \n"
        "\tCONSTRAINT event_source CHECK (source IN ('ui', 'api', 'mcp', 'system'))\n"
        ")"
    ),
    (
        "CREATE TABLE tokens (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\ttoken_hash VARCHAR(64) NOT NULL, \n"
        "\tscopes VARCHAR(64) NOT NULL, \n"
        "\tlabel VARCHAR(100), \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tlast_used_at VARCHAR(27), \n"
        "\trevoked_at VARCHAR(27), limit_json JSON, expires_at VARCHAR(27), browser_session BOOLEAN DEFAULT 0 NOT NULL, expiry_notified_at VARCHAR(27), \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id)\n"
        ")"
    ),
    (
        "CREATE TABLE deliveries (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\twebhook_id VARCHAR(12) NOT NULL, \n"
        "\tevent_id VARCHAR(12) NOT NULL, \n"
        "\tattempt INTEGER NOT NULL, \n"
        "\tstatus_code INTEGER, \n"
        "\tresponse_ms INTEGER, \n"
        "\terror TEXT, \n"
        "\tat VARCHAR(27) NOT NULL, state VARCHAR(10) DEFAULT 'ok' NOT NULL, next_at VARCHAR(27), body TEXT DEFAULT '' NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(event_id) REFERENCES events (id), \n"
        "\tFOREIGN KEY(webhook_id) REFERENCES webhooks (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE statuses (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tlist_id VARCHAR(12) NOT NULL, \n"
        "\tname VARCHAR(60) NOT NULL, \n"
        "\tkind VARCHAR(16) NOT NULL, \n"
        "\tcolor VARCHAR(16), \n"
        "\tposition INTEGER NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(list_id) REFERENCES lists (id) ON DELETE CASCADE, \n"
        "\tUNIQUE (list_id, name), \n"
        "\tCONSTRAINT status_kind CHECK (kind IN ('open', 'active', 'done', 'cancelled'))\n"
        ")"
    ),
    (
        "CREATE TABLE attachments (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\tfilename VARCHAR(255) NOT NULL, \n"
        "\tcontent_type VARCHAR(127) NOT NULL, \n"
        "\tsize INTEGER NOT NULL, \n"
        "\tpath VARCHAR(1000) NOT NULL, \n"
        "\tuploaded_by VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE, \n"
        "\tFOREIGN KEY(uploaded_by) REFERENCES accounts (id)\n"
        ")"
    ),
    (
        "CREATE TABLE body_versions (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\tversion_no INTEGER NOT NULL, \n"
        "\tbody_md TEXT NOT NULL, \n"
        "\tauthor_id VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tnote VARCHAR(500), \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(author_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE, \n"
        "\tUNIQUE (item_id, version_no)\n"
        ")"
    ),
    (
        "CREATE TABLE item_assignees (\n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tPRIMARY KEY (item_id, account_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE item_tags (\n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\ttag_id VARCHAR(12) NOT NULL, \n"
        "\tPRIMARY KEY (item_id, tag_id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE, \n"
        "\tFOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE comments (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\tauthor_id VARCHAR(12) NOT NULL, \n"
        "\tbody_md TEXT NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tedited_at VARCHAR(27), \n"
        "\tanchor_version_id VARCHAR(12), \n"
        "\tanchor_start INTEGER, \n"
        "\tanchor_end INTEGER, \n"
        "\tanchor_quote TEXT, assignee_id VARCHAR(12) REFERENCES accounts (id), resolved_at VARCHAR(27), resolved_by_id VARCHAR(12) REFERENCES accounts (id), \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(anchor_version_id) REFERENCES body_versions (id), \n"
        "\tFOREIGN KEY(author_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        'CREATE TABLE "webhooks" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\turl VARCHAR(2000) NOT NULL, \n"
        "\tevents_json JSON NOT NULL, \n"
        "\tfilter_json JSON, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tdisabled_at VARCHAR(27), \n"
        "\tsecret_enc TEXT DEFAULT '' NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id)\n"
        ")"
    ),
    (
        "CREATE TABLE views (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tname VARCHAR(100) NOT NULL, \n"
        "\tscope_kind VARCHAR(16) NOT NULL, \n"
        "\tscope_id VARCHAR(12), \n"
        "\tconfig_json JSON NOT NULL, \n"
        "\tposition INTEGER DEFAULT '0' NOT NULL, \n"
        "\towner_id VARCHAR(12) NOT NULL, \n"
        "\tshared BOOLEAN DEFAULT 1 NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tupdated_at VARCHAR(27) NOT NULL, \n"
        "\tarchived_at VARCHAR(27), \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(owner_id) REFERENCES accounts (id), \n"
        "\tCONSTRAINT view_scope_kind CHECK (scope_kind IN ('everything', 'section', 'list'))\n"
        ")"
    ),
    (
        "CREATE TABLE checklists (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\tname VARCHAR(200) NOT NULL, \n"
        "\tposition INTEGER NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE item_watchers (\n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tPRIMARY KEY (item_id, account_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE attachment_blobs (\n"
        "\tattachment_id VARCHAR(12) NOT NULL, \n"
        "\tdata BLOB NOT NULL, \n"
        "\tPRIMARY KEY (attachment_id), \n"
        "\tFOREIGN KEY(attachment_id) REFERENCES attachments (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE checklist_items (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tchecklist_id VARCHAR(12) NOT NULL, \n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\ttitle VARCHAR(500) NOT NULL, \n"
        "\tdone_at VARCHAR(27), \n"
        "\tassignee_id VARCHAR(12), \n"
        "\tposition INTEGER NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(assignee_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(checklist_id) REFERENCES checklists (id) ON DELETE CASCADE, \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE comment_reactions (\n"
        "\tcomment_id VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\temoji VARCHAR(32) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (comment_id, account_id, emoji), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(comment_id) REFERENCES comments (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE inbox_marks (\n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tevent_id VARCHAR(12) NOT NULL, \n"
        "\tcleared BOOLEAN NOT NULL, \n"
        "\tPRIMARY KEY (account_id, event_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id)\n"
        ")"
    ),
    (
        "CREATE TABLE inbox_state (\n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tread_through VARCHAR(12), \n"
        "\tcleared_through VARCHAR(12), \n"
        "\tPRIMARY KEY (account_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id)\n"
        ")"
    ),
    (
        "CREATE TABLE engine_cursors (\n"
        "\tname VARCHAR(40) NOT NULL, \n"
        "\tevent_id VARCHAR(12), \n"
        "\tupdated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (name)\n"
        ")"
    ),
    (
        "CREATE TABLE automations (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tname VARCHAR(120) NOT NULL, \n"
        "\tlist_id VARCHAR(12), \n"
        "\tsection_id VARCHAR(12), \n"
        "\ttrigger_json JSON NOT NULL, \n"
        "\tconditions_json JSON NOT NULL, \n"
        "\tactions_json JSON NOT NULL, \n"
        "\tenabled BOOLEAN NOT NULL, \n"
        "\tsecret_enc TEXT, \n"
        "\tcreated_by VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tupdated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(created_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(list_id) REFERENCES lists (id), \n"
        "\tFOREIGN KEY(section_id) REFERENCES sections (id)\n"
        ")"
    ),
    (
        "CREATE TABLE automation_runs (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tautomation_id VARCHAR(12) NOT NULL, \n"
        "\tevent_id VARCHAR(12), \n"
        "\titem_id VARCHAR(12), \n"
        "\ttriggered_by VARCHAR(12), \n"
        "\tstatus VARCHAR(10) NOT NULL, \n"
        "\tresults_json JSON NOT NULL, \n"
        "\tat VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(automation_id) REFERENCES automations (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE push_subscriptions (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tendpoint VARCHAR(2000) NOT NULL, \n"
        "\tp256dh VARCHAR(200) NOT NULL, \n"
        "\tauth VARCHAR(100) NOT NULL, \n"
        "\tlabel VARCHAR(100), \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tlast_ok_at VARCHAR(27), \n"
        "\tfailures INTEGER NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tUNIQUE (endpoint)\n"
        ")"
    ),
    (
        "CREATE TABLE reminders (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tremind_at VARCHAR(27), \n"
        "\tbefore_minutes INTEGER, \n"
        "\tnote VARCHAR(280), \n"
        "\tfired_for VARCHAR(27), \n"
        "\tcreated_by VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(created_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        'CREATE TABLE "item_links" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tfrom_item_id VARCHAR(12) NOT NULL, \n"
        "\tto_item_id VARCHAR(12) NOT NULL, \n"
        "\tkind VARCHAR(16) NOT NULL, \n"
        "\tcreated_by VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT ck_item_links_not_self CHECK (from_item_id != to_item_id), \n"
        "\tCONSTRAINT link_kind CHECK (kind IN ('blocks', 'relates', 'duplicates')), \n"
        "\tFOREIGN KEY(created_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(from_item_id) REFERENCES items (id) ON DELETE CASCADE, \n"
        "\tFOREIGN KEY(to_item_id) REFERENCES items (id) ON DELETE CASCADE, \n"
        "\tUNIQUE (from_item_id, to_item_id, kind)\n"
        ")"
    ),
    (
        "CREATE TABLE channel_reads (\n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tchannel VARCHAR(40) NOT NULL, \n"
        "\tread_through VARCHAR(12) NOT NULL, exposed_ids JSON DEFAULT '[]' NOT NULL, \n"
        "\tPRIMARY KEY (account_id, channel), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id)\n"
        ")"
    ),
    (
        "CREATE TABLE message_reactions (\n"
        "\tmessage_id VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\temoji VARCHAR(32) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (message_id, account_id, emoji), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE doc_versions (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tdoc_id VARCHAR(12) NOT NULL, \n"
        "\tversion_no INTEGER NOT NULL, \n"
        "\ttitle VARCHAR(200) NOT NULL, \n"
        "\tbody_md TEXT NOT NULL, \n"
        "\tauthor_id VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tupdated_at VARCHAR(27) NOT NULL, \n"
        "\tnote VARCHAR(200), \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(author_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(doc_id) REFERENCES docs (id) ON DELETE CASCADE, \n"
        "\tUNIQUE (doc_id, version_no)\n"
        ")"
    ),
    (
        'CREATE TABLE "favorites" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tkind VARCHAR(16) NOT NULL, \n"
        "\ttarget_id VARCHAR(12) NOT NULL, \n"
        "\tposition INTEGER NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT favorite_kind CHECK (kind IN ('list', 'section', 'view', 'item', 'doc')), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tUNIQUE (account_id, kind, target_id)\n"
        ")"
    ),
    (
        'CREATE TABLE "recurrences" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tlist_id VARCHAR(12) NOT NULL, \n"
        "\ttitle_template VARCHAR(500) NOT NULL, \n"
        "\tbody_template_md TEXT, \n"
        "\tpriority VARCHAR(16), \n"
        "\ttags_json JSON, \n"
        "\tassignee_ids_json JSON, \n"
        "\trrule TEXT NOT NULL, \n"
        "\tdtstart VARCHAR(27) NOT NULL, \n"
        "\ttimezone VARCHAR(64) NOT NULL, \n"
        "\tlead_days INTEGER NOT NULL, \n"
        "\tmissed_policy VARCHAR(16) NOT NULL, \n"
        "\tnext_run_at VARCHAR(27), \n"
        "\tcreated_by VARCHAR(12) NOT NULL, \n"
        "\tpaused_at VARCHAR(27), \n"
        "\tended_at VARCHAR(27), \n"
        "\t\"trigger\" VARCHAR(16) DEFAULT 'schedule' NOT NULL, \n"
        "\tskip_weekends BOOLEAN DEFAULT 0 NOT NULL, \n"
        "\treopen BOOLEAN DEFAULT 0 NOT NULL, \n"
        "\tcopy_checklists BOOLEAN DEFAULT 1 NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT missed_policy CHECK (missed_policy IN ('spawn_always', 'roll_forward', 'skip_if_open')), \n"
        "\tCONSTRAINT priority CHECK (priority IN ('urgent', 'high', 'normal', 'low')), \n"
        "\tCONSTRAINT recur_trigger CHECK (\"trigger\" IN ('schedule', 'on_done')), \n"
        "\tFOREIGN KEY(created_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(list_id) REFERENCES lists (id)\n"
        ")"
    ),
    (
        "CREATE TABLE shares (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tscope_kind VARCHAR(16) NOT NULL, \n"
        "\tscope_id VARCHAR(12) NOT NULL, \n"
        "\tlevel VARCHAR(16) NOT NULL, \n"
        "\tcreated_by VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE, \n"
        "\tFOREIGN KEY(created_by) REFERENCES accounts (id), \n"
        "\tUNIQUE (account_id, scope_kind, scope_id), \n"
        "\tCONSTRAINT share_level CHECK (level IN ('view', 'comment', 'edit'))\n"
        ")"
    ),
    (
        'CREATE TABLE "accounts" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\thandle VARCHAR(32) NOT NULL, \n"
        "\tdisplay_name VARCHAR(100) NOT NULL, \n"
        "\tkind VARCHAR(16) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tdisabled_at VARCHAR(27), \n"
        "\tprefs_json JSON DEFAULT '{}' NOT NULL, \n"
        "\trole VARCHAR(16) DEFAULT 'member' NOT NULL, wake VARCHAR(8) DEFAULT 'push' NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT account_kind CHECK (kind IN ('person', 'agent')), \n"
        "\tCONSTRAINT account_role CHECK (role IN ('owner', 'admin', 'member', 'guest')), \n"
        "\tUNIQUE (handle)\n"
        ")"
    ),
    (
        'CREATE TABLE "lists" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tsection_id VARCHAR(12) NOT NULL, \n"
        "\tname VARCHAR(100) NOT NULL, \n"
        "\tslug VARCHAR(100) NOT NULL, \n"
        "\tposition INTEGER NOT NULL, \n"
        "\tdefault_status_id VARCHAR(12), \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tarchived_at VARCHAR(27), \n"
        "\tprivate BOOLEAN DEFAULT 0 NOT NULL, kind VARCHAR(8) DEFAULT 'tasks' NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT fk_lists_default_status_id FOREIGN KEY(default_status_id) REFERENCES statuses (id), \n"
        "\tFOREIGN KEY(section_id) REFERENCES sections (id), \n"
        "\tUNIQUE (section_id, slug)\n"
        ")"
    ),
    (
        'CREATE TABLE "sections" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tname VARCHAR(100) NOT NULL, \n"
        "\tslug VARCHAR(100) NOT NULL, \n"
        "\tposition INTEGER NOT NULL, \n"
        "\taudience VARCHAR(16) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tarchived_at VARCHAR(27), \n"
        "\tprivate BOOLEAN DEFAULT 0 NOT NULL, whiteboard_url VARCHAR(2048), \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT audience CHECK (audience IN ('personal', 'ai', 'work', 'family')), \n"
        "\tUNIQUE (slug)\n"
        ")"
    ),
    (
        "CREATE TABLE work_leases (\n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tclaimed_at VARCHAR(27) NOT NULL, \n"
        "\tuntil VARCHAR(27) NOT NULL, \n"
        "\tfrom_pool BOOLEAN NOT NULL, run_id VARCHAR(64), asked_only BOOLEAN DEFAULT '0' NOT NULL, quarantined BOOLEAN DEFAULT '0' NOT NULL, \n"
        "\tPRIMARY KEY (item_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE agent_threads (\n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tref VARCHAR(200) NOT NULL, \n"
        "\turl VARCHAR(2000), \n"
        "\tupdated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (item_id, account_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE invitations (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tsection_id VARCHAR(12) NOT NULL, \n"
        "\tsecret_hash VARCHAR(64) NOT NULL, \n"
        "\tcreated_by VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\texpires_at VARCHAR(27) NOT NULL, \n"
        "\taccepted_at VARCHAR(27), \n"
        "\trevoked_at VARCHAR(27), \n"
        "\ttoken_id VARCHAR(12), \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(section_id) REFERENCES sections (id), \n"
        "\tFOREIGN KEY(created_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(token_id) REFERENCES tokens (id)\n"
        ")"
    ),
    (
        "CREATE TABLE chat_wakes (\n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tchannel VARCHAR(40) NOT NULL, \n"
        "\tthread_id VARCHAR(12) NOT NULL, \n"
        "\tmessage_id VARCHAR(12) NOT NULL, \n"
        "\tsince_id VARCHAR(12) NOT NULL, \n"
        "\ttaken_id VARCHAR(12), \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tclaimed_at VARCHAR(27), \n"
        "\tuntil VARCHAR(27), run_id VARCHAR(64), \n"
        "\tPRIMARY KEY (account_id, channel, thread_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(message_id) REFERENCES messages (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        'CREATE TABLE "items" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tlist_id VARCHAR(12) NOT NULL, \n"
        "\tparent_id VARCHAR(12), \n"
        "\tkind VARCHAR(16) NOT NULL, \n"
        "\ttitle VARCHAR(500) NOT NULL, \n"
        "\tbody_md TEXT NOT NULL, \n"
        "\tstatus_id VARCHAR(12), \n"
        "\tpriority VARCHAR(16), \n"
        "\tstart_at VARCHAR(27), \n"
        "\tdue_at VARCHAR(27), \n"
        "\tcompleted_at VARCHAR(27), \n"
        "\twaiting_on_account_id VARCHAR(12), \n"
        "\tacceptance_md TEXT, \n"
        "\trecurrence_id VARCHAR(12), \n"
        "\toccurrence_at VARCHAR(27), \n"
        "\texternal_ref VARCHAR(200), \n"
        "\tlast_due_event VARCHAR(16), \n"
        "\tposition INTEGER NOT NULL, \n"
        "\tcreated_by VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tupdated_at VARCHAR(27) NOT NULL, \n"
        "\tarchived_at VARCHAR(27), \n"
        "\testimate_minutes INTEGER, \n"
        "\tagent_state VARCHAR(16), \n"
        "\tagent_state_by VARCHAR(12), \n"
        "\tagent_state_at VARCHAR(27), \n"
        "\tagent_note VARCHAR(280), \n"
        "\tagent_progress INTEGER, \n"
        "\ttrashed_at VARCHAR(27), \n"
        "\tblocked_since VARCHAR(27), \n"
        "\tagent_url VARCHAR(2000), \n"
        "\trun_requested_at VARCHAR(27), \n"
        "\tdue_date_only BOOLEAN DEFAULT 0 NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT priority CHECK (priority IN ('urgent', 'high', 'normal', 'low')), \n"
        "\tCONSTRAINT ck_items_not_own_parent CHECK (parent_id IS NULL OR parent_id != id), \n"
        "\tCONSTRAINT agent_state CHECK (agent_state IN ('working', 'blocked', 'review')), \n"
        "\tCONSTRAINT item_kind CHECK (kind IN ('task', 'doc')), \n"
        "\tUNIQUE (recurrence_id, occurrence_at), \n"
        "\tFOREIGN KEY(agent_state_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(waiting_on_account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(status_id) REFERENCES statuses (id), \n"
        "\tFOREIGN KEY(list_id) REFERENCES lists (id), \n"
        "\tFOREIGN KEY(parent_id) REFERENCES items (id), \n"
        "\tFOREIGN KEY(recurrence_id) REFERENCES recurrences (id), \n"
        "\tFOREIGN KEY(created_by) REFERENCES accounts (id)\n"
        ")"
    ),
    (
        'CREATE TABLE "capacities" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\thours_per_day FLOAT, \n"
        "\ttasks_per_day INTEGER, \n"
        "\twork_days VARCHAR(13) NOT NULL, \n"
        "\tupdated_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tUNIQUE (account_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id)\n"
        ")"
    ),
    (
        'CREATE TABLE "doc_files" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tdoc_id VARCHAR(12) NOT NULL, \n"
        "\tfilename VARCHAR(255) NOT NULL, \n"
        "\tcontent_type VARCHAR(127) NOT NULL, \n"
        "\tsize INTEGER NOT NULL, \n"
        "\tdata BLOB NOT NULL, \n"
        "\tuploaded_by VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\trevision_no INTEGER, \n"
        "\tnote VARCHAR(200), \n"
        "\tsuperseded_at VARCHAR(27), \n"
        "\trestored_from INTEGER, text TEXT, tables_json JSON, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT uq_doc_files_doc_id_revision_no UNIQUE (doc_id, revision_no), \n"
        "\tFOREIGN KEY(uploaded_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(doc_id) REFERENCES docs (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE doc_comments (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tdoc_id VARCHAR(12) NOT NULL, \n"
        "\tparent_id VARCHAR(12), \n"
        "\tsurface VARCHAR(10) NOT NULL, \n"
        "\tauthor_id VARCHAR(12) NOT NULL, \n"
        "\tbody_md TEXT NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tedited_at VARCHAR(27), \n"
        "\tanchor_revision INTEGER NOT NULL, \n"
        "\tanchor_start INTEGER, \n"
        "\tanchor_end INTEGER, \n"
        "\tanchor_quote TEXT, \n"
        "\tanchor_page INTEGER, \n"
        "\tanchor_x FLOAT, \n"
        "\tanchor_y FLOAT, \n"
        "\tsuggestion_md TEXT, \n"
        "\taccepted_at VARCHAR(27), \n"
        "\tassignee_id VARCHAR(12), \n"
        "\tresolved_at VARCHAR(27), \n"
        "\tresolved_by_id VARCHAR(12), anchor_page_revision INTEGER, \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT ck_doc_comments_surface CHECK (surface IN ('review', 'discussion')), \n"
        "\tFOREIGN KEY(doc_id) REFERENCES docs (id) ON DELETE CASCADE, \n"
        "\tFOREIGN KEY(parent_id) REFERENCES doc_comments (id) ON DELETE CASCADE, \n"
        "\tFOREIGN KEY(author_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(assignee_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(resolved_by_id) REFERENCES accounts (id)\n"
        ")"
    ),
    (
        "CREATE TABLE doc_tags (\n"
        "\tdoc_id VARCHAR(12) NOT NULL, \n"
        "\ttag_id VARCHAR(12) NOT NULL, \n"
        "\tPRIMARY KEY (doc_id, tag_id), \n"
        "\tFOREIGN KEY(doc_id) REFERENCES docs (id) ON DELETE CASCADE, \n"
        "\tFOREIGN KEY(tag_id) REFERENCES tags (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        'CREATE TABLE "docs" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\ttitle VARCHAR(200) NOT NULL, \n"
        "\ticon VARCHAR(16), \n"
        "\tparent_id VARCHAR(12), \n"
        "\tsection_id VARCHAR(12), \n"
        "\tlist_id VARCHAR(12), \n"
        "\tbody_md TEXT NOT NULL, \n"
        "\tposition INTEGER NOT NULL, \n"
        "\trevision INTEGER NOT NULL, \n"
        "\tversion_no INTEGER NOT NULL, \n"
        "\tcreated_by VARCHAR(12) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tupdated_by VARCHAR(12) NOT NULL, \n"
        "\tupdated_at VARCHAR(27) NOT NULL, \n"
        "\tarchived_at VARCHAR(27), \n"
        "\tkind VARCHAR(8) DEFAULT 'page' NOT NULL, \n"
        "\tpinned_revision_no INTEGER, \n"
        "\tpinned_note VARCHAR(200), \n"
        "\ttop_pinned_at VARCHAR(27), \n"
        "\ttop_pinned_by VARCHAR(12), \n"
        "\tPRIMARY KEY (id), \n"
        "\tCONSTRAINT ck_docs_not_own_parent CHECK (parent_id IS NULL OR parent_id != id), \n"
        "\tCONSTRAINT ck_docs_kind CHECK (kind IN ('page', 'file', 'folder')), \n"
        "\tCONSTRAINT fk_docs_top_pinned_by_accounts FOREIGN KEY(top_pinned_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(section_id) REFERENCES sections (id), \n"
        "\tFOREIGN KEY(created_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(updated_by) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(parent_id) REFERENCES docs (id), \n"
        "\tFOREIGN KEY(list_id) REFERENCES lists (id)\n"
        ")"
    ),
    (
        "CREATE TABLE push_deliveries (\n"
        "\tsubscription_id VARCHAR(12) NOT NULL, \n"
        "\tevent_id VARCHAR(12) NOT NULL, \n"
        "\tkind VARCHAR(16) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tattempts INTEGER NOT NULL, \n"
        "\tstate VARCHAR(16) NOT NULL, \n"
        "\tnext_attempt_at VARCHAR(27), \n"
        "\texpires_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (subscription_id, event_id, kind), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(event_id) REFERENCES events (id)\n"
        ")"
    ),
    (
        "CREATE TABLE agent_connections (\n"
        "\tid VARCHAR(12) NOT NULL, \n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tissuer_id VARCHAR(12) NOT NULL, \n"
        "\tissuer_token_id VARCHAR(12) NOT NULL, \n"
        "\ttoken_id VARCHAR(12), \n"
        "\tlabel VARCHAR(80) NOT NULL, \n"
        "\tlist_ids JSON NOT NULL, \n"
        "\tsecret_hash VARCHAR(64) NOT NULL, \n"
        "\texpires_at VARCHAR(27) NOT NULL, \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\texchanged_at VARCHAR(27), \n"
        "\trevoked_at VARCHAR(27), \n"
        "\tlast_seen_at VARCHAR(27), \n"
        "\tverified_at VARCHAR(27), \n"
        '\t"check" VARCHAR(24) NOT NULL, \n'
        "\tcapabilities JSON NOT NULL, \n"
        "\tdevice_name VARCHAR(80), \n"
        "\tadapter VARCHAR(80), \n"
        "\tversion VARCHAR(40), \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(issuer_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(issuer_token_id) REFERENCES tokens (id), \n"
        "\tUNIQUE (token_id), \n"
        "\tFOREIGN KEY(token_id) REFERENCES tokens (id), \n"
        "\tUNIQUE (secret_hash)\n"
        ")"
    ),
    (
        "CREATE TABLE workspace_settings (\n"
        '\t"key" VARCHAR(64) NOT NULL, \n'
        "\tvalue_json JSON NOT NULL, \n"
        "\tupdated_at VARCHAR(27) NOT NULL, \n"
        '\tPRIMARY KEY ("key")\n'
        ")"
    ),
    (
        "CREATE TABLE doc_wakes (\n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tdoc_id VARCHAR(12) NOT NULL, \n"
        "\tthread_id VARCHAR(12) NOT NULL, \n"
        "\tcomment_id VARCHAR(12), \n"
        "\tby_id VARCHAR(12) NOT NULL, \n"
        "\texcerpt VARCHAR(200) NOT NULL, \n"
        "\tmention_no INTEGER NOT NULL, \n"
        "\ttaken_no INTEGER, \n"
        "\trun_id VARCHAR(64), \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tclaimed_at VARCHAR(27), \n"
        "\tuntil VARCHAR(27), \n"
        "\tPRIMARY KEY (account_id, doc_id, thread_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(doc_id) REFERENCES docs (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        'CREATE TABLE "messages" (\n'
        "\tid VARCHAR(12) NOT NULL, \n"
        "\tchannel VARCHAR(40) NOT NULL, \n"
        "\tparent_id VARCHAR(12), \n"
        "\tauthor_id VARCHAR(12) NOT NULL, \n"
        "\tbody_md TEXT NOT NULL, \n"
        "\tmentions_json JSON NOT NULL, \n"
        "\titem_id VARCHAR(12), \n"
        "\tcreated_at VARCHAR(27) NOT NULL, \n"
        "\tedited_at VARCHAR(27), \n"
        "\tdeleted_at VARCHAR(27), \n"
        "\tmembers_only BOOLEAN DEFAULT 0 NOT NULL, \n"
        "\tPRIMARY KEY (id), \n"
        "\tFOREIGN KEY(parent_id) REFERENCES messages (id) ON DELETE CASCADE, \n"
        "\tFOREIGN KEY(author_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE SET NULL\n"
        ")"
    ),
    (
        "CREATE TABLE item_reads (\n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\titem_id VARCHAR(12) NOT NULL, \n"
        "\tread_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (account_id, item_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE\n"
        ")"
    ),
    (
        "CREATE TABLE email_deliveries (\n"
        "\taccount_id VARCHAR(12) NOT NULL, \n"
        "\tevent_id VARCHAR(12) NOT NULL, \n"
        "\tattempts INTEGER NOT NULL, \n"
        "\tstate VARCHAR(16) NOT NULL, \n"
        "\tsend_after VARCHAR(27), \n"
        "\texpires_at VARCHAR(27) NOT NULL, \n"
        "\tPRIMARY KEY (account_id, event_id), \n"
        "\tFOREIGN KEY(account_id) REFERENCES accounts (id), \n"
        "\tFOREIGN KEY(event_id) REFERENCES events (id)\n"
        ")"
    ),
]

INDEXES = [
    ("CREATE INDEX ix_events_item_id_at ON events (item_id, at)"),
    ("CREATE INDEX ix_events_subject_id ON events (subject_id)"),
    ("CREATE INDEX ix_events_type ON events (type)"),
    ("CREATE INDEX ix_tokens_account_id ON tokens (account_id)"),
    ("CREATE INDEX ix_deliveries_event_id ON deliveries (event_id)"),
    ("CREATE INDEX ix_deliveries_webhook_id ON deliveries (webhook_id)"),
    ("CREATE INDEX ix_statuses_list_id ON statuses (list_id)"),
    ("CREATE INDEX ix_attachments_item_id ON attachments (item_id)"),
    ("CREATE INDEX ix_body_versions_item_id ON body_versions (item_id)"),
    ("CREATE INDEX ix_item_assignees_account_id ON item_assignees (account_id)"),
    ("CREATE INDEX ix_item_tags_tag_id ON item_tags (tag_id)"),
    ("CREATE INDEX ix_comments_item_id ON comments (item_id)"),
    ("CREATE INDEX ix_deliveries_next_at ON deliveries (next_at)"),
    ("CREATE INDEX ix_webhooks_account_id ON webhooks (account_id)"),
    ("CREATE INDEX ix_checklists_item_id ON checklists (item_id)"),
    ("CREATE INDEX ix_item_watchers_account_id ON item_watchers (account_id)"),
    ("CREATE INDEX ix_checklist_items_checklist_id ON checklist_items (checklist_id)"),
    ("CREATE INDEX ix_checklist_items_item_id ON checklist_items (item_id)"),
    ("CREATE INDEX ix_events_actor_id_id ON events (actor_id, id)"),
    ("CREATE INDEX ix_automations_list_id ON automations (list_id)"),
    ("CREATE INDEX ix_automations_section_id ON automations (section_id)"),
    ("CREATE INDEX ix_automation_runs_automation_id_at ON automation_runs (automation_id, at)"),
    ("CREATE INDEX ix_automation_runs_item_id ON automation_runs (item_id)"),
    ("CREATE INDEX ix_push_subscriptions_account_id ON push_subscriptions (account_id)"),
    ("CREATE INDEX ix_reminders_account_id ON reminders (account_id)"),
    ("CREATE INDEX ix_reminders_item_id ON reminders (item_id)"),
    ("CREATE INDEX ix_item_links_from_item_id ON item_links (from_item_id)"),
    ("CREATE INDEX ix_item_links_to_item_id ON item_links (to_item_id)"),
    ("CREATE INDEX ix_doc_versions_doc_id ON doc_versions (doc_id)"),
    ("CREATE INDEX ix_favorites_account_id ON favorites (account_id)"),
    ("CREATE INDEX ix_recurrences_list_id ON recurrences (list_id)"),
    ("CREATE INDEX ix_recurrences_next_run_at ON recurrences (next_run_at)"),
    ("CREATE INDEX ix_shares_account_id ON shares (account_id)"),
    ("CREATE INDEX ix_shares_scope_id ON shares (scope_id)"),
    ("CREATE INDEX ix_lists_section_id ON lists (section_id)"),
    ("CREATE INDEX ix_work_leases_account_id ON work_leases (account_id)"),
    ("CREATE INDEX ix_work_leases_until ON work_leases (until)"),
    ("CREATE INDEX ix_comments_assignee_id ON comments (assignee_id)"),
    ("CREATE INDEX ix_chat_wakes_until ON chat_wakes (until)"),
    (
        "CREATE UNIQUE INDEX ux_items_external_ref ON items (external_ref) WHERE external_ref IS NOT NULL"
    ),
    ("CREATE INDEX ix_items_due_at ON items (due_at)"),
    ("CREATE INDEX ix_items_agent_state_by ON items (agent_state_by)"),
    ("CREATE INDEX ix_items_status_id ON items (status_id)"),
    ("CREATE INDEX ix_items_parent_id ON items (parent_id)"),
    ("CREATE INDEX ix_items_list_id ON items (list_id)"),
    ("CREATE INDEX ix_items_waiting_on_account_id ON items (waiting_on_account_id)"),
    ("CREATE INDEX ix_items_trashed_at ON items (trashed_at)"),
    ("CREATE INDEX ix_doc_files_doc_id ON doc_files (doc_id)"),
    ("CREATE INDEX ix_doc_comments_doc_id ON doc_comments (doc_id)"),
    ("CREATE INDEX ix_doc_comments_parent_id ON doc_comments (parent_id)"),
    ("CREATE INDEX ix_doc_comments_assignee_id ON doc_comments (assignee_id)"),
    ("CREATE INDEX ix_doc_tags_tag_id ON doc_tags (tag_id)"),
    ("CREATE INDEX ix_docs_section_id ON docs (section_id)"),
    ("CREATE INDEX ix_docs_parent_id ON docs (parent_id)"),
    ("CREATE INDEX ix_docs_list_id ON docs (list_id)"),
    ("CREATE INDEX ix_push_deliveries_expires_at ON push_deliveries (expires_at)"),
    ("CREATE INDEX ix_push_deliveries_next_attempt_at ON push_deliveries (next_attempt_at)"),
    ("CREATE INDEX ix_agent_connections_account_id ON agent_connections (account_id)"),
    ("CREATE INDEX ix_doc_wakes_until ON doc_wakes (until)"),
    ("CREATE INDEX ix_messages_parent_id ON messages (parent_id, id)"),
    ("CREATE INDEX ix_messages_channel_id ON messages (channel, id)"),
    ("CREATE INDEX ix_item_reads_item_id ON item_reads (item_id)"),
    ("CREATE INDEX ix_email_deliveries_send_after ON email_deliveries (send_after)"),
    ("CREATE INDEX ix_email_deliveries_expires_at ON email_deliveries (expires_at)"),
]

TRIGGERS = [
    (
        "CREATE TRIGGER events_no_update BEFORE UPDATE ON events\n"
        "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
    ),
    (
        "CREATE TRIGGER events_no_delete BEFORE DELETE ON events\n"
        "BEGIN SELECT RAISE(ABORT, 'events are append-only'); END"
    ),
    (
        "CREATE TRIGGER items_depth_insert BEFORE INSERT ON items\n"
        "WHEN NEW.parent_id IS NOT NULL AND (\n"
        "  (SELECT p4.parent_id FROM items p1\n"
        "     JOIN items p2 ON p2.id = p1.parent_id\n"
        "     JOIN items p3 ON p3.id = p2.parent_id\n"
        "     JOIN items p4 ON p4.id = p3.parent_id\n"
        "    WHERE p1.id = NEW.parent_id) IS NOT NULL)\n"
        "BEGIN SELECT RAISE(ABORT, 'item depth exceeds 4: the parent is already four levels down'); END"
    ),
    (
        "CREATE TRIGGER items_depth_update BEFORE UPDATE OF parent_id ON items\n"
        "WHEN NEW.parent_id IS NOT NULL AND (\n"
        "  NEW.id IN (\n"
        "    SELECT id FROM items WHERE id = NEW.parent_id\n"
        "    UNION SELECT a2.id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id\n"
        "      WHERE a1.id = NEW.parent_id\n"
        "    UNION SELECT a3.id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id\n"
        "      JOIN items a3 ON a3.id = a2.parent_id WHERE a1.id = NEW.parent_id\n"
        "    UNION SELECT a4.id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id\n"
        "      JOIN items a3 ON a3.id = a2.parent_id JOIN items a4 ON a4.id = a3.parent_id\n"
        "      WHERE a1.id = NEW.parent_id)\n"
        "  OR (SELECT a4.parent_id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id\n"
        "        JOIN items a3 ON a3.id = a2.parent_id JOIN items a4 ON a4.id = a3.parent_id\n"
        "       WHERE a1.id = NEW.parent_id) IS NOT NULL\n"
        "  OR ((SELECT a3.parent_id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id\n"
        "         JOIN items a3 ON a3.id = a2.parent_id WHERE a1.id = NEW.parent_id) IS NOT NULL\n"
        "      AND EXISTS (SELECT 1 FROM items WHERE parent_id = NEW.id))\n"
        "  OR ((SELECT a2.parent_id FROM items a1 JOIN items a2 ON a2.id = a1.parent_id\n"
        "        WHERE a1.id = NEW.parent_id) IS NOT NULL\n"
        "      AND EXISTS (SELECT 1 FROM items c1 JOIN items c2 ON c2.parent_id = c1.id\n"
        "                   WHERE c1.parent_id = NEW.id))\n"
        "  OR ((SELECT parent_id FROM items WHERE id = NEW.parent_id) IS NOT NULL\n"
        "      AND EXISTS (SELECT 1 FROM items c1 JOIN items c2 ON c2.parent_id = c1.id\n"
        "                   JOIN items c3 ON c3.parent_id = c2.id WHERE c1.parent_id = NEW.id))\n"
        "  OR EXISTS (SELECT 1 FROM items c1 JOIN items c2 ON c2.parent_id = c1.id\n"
        "              JOIN items c3 ON c3.parent_id = c2.id JOIN items c4 ON c4.parent_id = c3.id\n"
        "             WHERE c1.parent_id = NEW.id))\n"
        "BEGIN SELECT RAISE(ABORT, 'item depth exceeds 4: a cycle, or a tree nesting deeper than four');\n"
        "END"
    ),
]


def upgrade() -> None:
    for statement in TABLES + INDEXES + TRIGGERS:
        op.execute(statement)


def downgrade() -> None:
    for name in reversed(TABLE_NAMES):
        op.drop_table(name)
