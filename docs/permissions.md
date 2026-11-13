# Permissions: roles, sharing, private lists, limited tokens, audit log

`service/access.py` and what it enforces, how every read and write is filtered, the live
gate, and the audit log. The guest-session contract is `docs/collaboration.md`.

## Permissions: roles, sharing, private lists, limited tokens, audit log

`service/access.py` (the rules and the two hooks), `service/permissions.py` (roles, shares, privacy
writes), `service/audit.py`, `api/permissions.py`, MCP `slate_share` and `slate_audit`, board
`components/members/`, `components/audit/`, `ShareDialog.svelte`, `lib/permissions.ts`.

- **Model.** `accounts.role` (owner, admin, member, guest), `sections.private`, `lists.private`,
  `shares(account_id, scope_kind section|list, scope_id, level view|comment|edit)`,
  `tokens.limit_json` (`{section_ids, list_ids, level}`). Owner/admin see everything; members
  everything not private; guests only shares. A section share covers its non-private lists; a list
  share reaches into a private list. Shares only add. The account `POST /bootstrap` creates is
  the owner.
- **Enforcement is not in the routes.** `api.auth` and the MCP `LocalBackend` call `access.bind()`,
  which puts an `Access` on the request's session (`session.info["slate_access"]`). Then two hooks
  on every `Session` (`slate.db`) do the work: `read_criteria` (do_orm_execute) adds
  `with_loader_criteria` for items, lists, sections, chat messages, events, and every table with an
  `item_id` or `list_id` column (found by walking the mappers), so any ORM read (search, check-in,
  export, audit, batch) only returns rows the caller can see; `core.Change` calls
  `access.check_object()` / `check_record()` on every create/set/delete/record, so a write below the
  needed level raises `Forbidden` (403). Unseen rows are absent (404), never "forbidden" (no
  existence leak). Needed level: `comment` for comments, reactions and chat; `view` for watching,
  reminders, read marks and favorites; `edit` otherwise; moving an item checks the destination list
  too.
- **Zero cost for the owner.** An owner or admin with an unlimited write token gets no `Access` on the
  session: both hooks return at once. A member with nothing private anywhere is also unrestricted
  (one `EXISTS` query per request).
- **Criteria are lambdas** (`lambda c: c.list_id.in_(lists)`): plain expressions do not follow
  aliases (`statuses AS item_status` breaks them), and lambdas bind the closure values as
  parameters so the compiled-statement cache stays shared (a test runs two guests back to back).
- **Token scopes never outrank the role.** `accounts._lookup` drops `admin` from a non-admin's token
  (it becomes write) and from any limited token; `create_token` does the same at mint time. `GET
  /me` returns `access` (all, level, manage, limited, general, sections, lists); the board reads it
  to hide what it cannot do, the server enforces regardless.
- **Who counts as an admin: one rule.** `access.grants_admin(role, scopes, limit)`: an owner or
  admin account, through a token with the `admin` scope and no list or section limit. Every check
  asks it: `access.is_admin(session)` (reads the bound caller, `session.info["slate_caller"]`,
  which `attach` keeps even for an unrestricted caller; with no bound caller it is the named
  account's role, else Slate's own session), `access.require_admin`, `Principal.is_admin` and
  `Access.is_admin`; `GET /me` `access.manage` is the same answer. So the same owner on a write
  token, or on a list-limited one, acts as a member. Never test "no `Access` on the session" for
  admin: an unrestricted member has none either.
- **Live paths.** The SSE stream builds an `access.EventGate` per connection: replays read through
  the caller's filter, live events pass `gate.allows()` (item -> list cache; message events by
  `after.channel`), and the gate reloads on `share.*`, `account.role_changed`, `list.*`,
  `section.*`. Webhook fan-out and Web Push use the same gate for the subscription's owner. The
  board reloads `/me` and its data when an event changes its own access
  (`lib/permissions.affectsMyAccess`). The replay and the gate agree: a restricted member with
  General gets `tag.*`, `account.*` and `capacity.*` from both.
- **Personal events** (`access.is_personal`: `account.prefs_changed`, `account.digest`,
  `channel.read`, `item.read`, `inbox.*`, `push.*`, `webhook.*`, `token.*`, `favorite.*`,
  `session.*`, `agent.connection_*`) are about one account's own settings and bookkeeping. Only that
  account and an admin read them, whatever else the caller reaches and whether or not anything is
  private: a criterion `read_criteria` adds for every bound non-admin caller (`_owned`: the
  subject is the caller, the payload's `account_id` is, or the subject is a token, webhook,
  favorite or push device it owns), and `EventGate._owns` for live events.
- **Chat.** `parse_channel` 404s a channel the caller cannot read; General is hidden from guests and
  list-limited tokens; a section seen only through one of its lists has no section channel.
- **A project agent's chat starts at its share.** `agent:<id>` is one
  global channel, so a guest or limited token that reaches an agent through `Access.agents` (an
  agent shared into a section or list it sees) reads it only from the share's `created_at` on:
  `Access.agent_since` (`_project_agents`, the earliest live share; revoke-and-regrant starts the
  clock over). The read filter's `_chat_visible` expresses that in SQL (an `EXISTS` on `shares`, so
  a share granted after the access was computed still counts), used for `messages` and for message
  events (so the stream's replay and the audit log agree); `EventGate._allows_outer` compares the
  live payload's `at` with `Access.agent_chat_since(channel)`. The agent itself, members and admins
  read the whole chat. `ChatWake` rows have no date and keep the plain channel rule.
- **Docs.** A page's level is its top page's place (`access.doc_place`, read past the filter with
  autoflush off): the list's level, else the section's (0 if the section is only seen through a
  list), else the workspace's (members yes; guests and list-limited tokens no). The read filter uses
  a recursive CTE (`_visible_docs`) to hand the top page's place down to subpages, and filters
  `doc_versions`/`doc_files` by it. Creating, editing, archiving and moving need edit; a move checks
  both the old and new place. The stream gate caches doc -> place and clears it on `doc.moved`.
- **Capacities** belong to no list, so they follow `Access.general` (members yes; guests and
  list-limited tokens no; `_WORKSPACE_TYPES`).
- **NULL-scoped rows are General's too.** A row with `list_id IS NULL` or `item_id IS
  NULL` (workspace-wide automations and their webhook URLs, automation runs with no item) is
  visible only with General access: `and_(is NULL, general)`. `Automation` has its own criteria:
  its list, else its `section_id` among the caller's sections at view or better, else General. `View` is
  filtered by its scope (`list`/`section` the caller sees; `everything` for General; the caller's
  own views; and `SEED_VIEWS`, Slate's landing views, for everyone, so `#/today` keeps working for a
  guest). A view with `shared` false is its owner's alone, admins included: a `View` criterion and
  a `view.*` event criterion for every bound caller, and `EventGate._sees_view`. Changing or
  archiving a view is for its owner or an admin, a landing view for an admin only
  (`views._require_may_change`). `DocTag` follows the doc (`_visible_docs`).
- **Tags.** The catalog is workspace-wide and `item_tags.tag_id` cascades, so
  renaming, recolouring, merging or deleting a tag touches items outside a restricted caller's reach
  with no event for those rows: `tags.update_tag`, `merge_tag` and `delete_tag` call
  `access.require_admin` (an admin by the rule above; members are refused whether or not
  anything is private).
  Creating a tag and applying it are not admin-only (`Tag` is in `_GUEST_GLOBAL`). `tags.list_tags`
  gives a restricted caller only the tags on an item or doc it can see (the `ItemTag`/`DocTag`
  criteria do the work) plus those it created (`tag.created` events by it, read unrestricted). The
  board's tag manager hides rename/merge/delete and the colour swatch below `canManage`.
- **Watchers and reminders.** `watchers._someone` and `reminders.add_reminder` let a
  caller below edit on the item's list act only on itself; naming someone else needs edit
  (`require_list_level`). Being removed as a watcher by someone else is inbox reason `watching`
  (`item.unwatched` whose `before` is you; `inbox.entries` adds the condition).
  `items.resolve_account` resolves a handle for a restricted caller only within
  `access.participant_ids()` and answers `no account '<ref>'` for both unknown and out-of-reach
  handles, so watch/assign/reminder references cannot enumerate accounts. `GET /stats` is gated like
  `/health` (`health.require_viewer`): it names the database file and counts accounts.
- **Audit log.** `GET /audit` (filters: actor, actor_kind, source, type with `prefix.`,
  item/list/section, since/until, q; cursor `before`), `/audit/facets`, `/audit.csv` (50,000 rows).
  It reads the events table through the same filter, so non-admins see their lists' history plus
  their own actions. `audit.VERBS` turns event types into the one-line summary. The `source`
  filter separates the board from agents: `api/auth.py` `request_source` records `ui` only when a
  REST call carries both `X-Slate-Client: browser` and `X-Slate-Source: ui` (the board's
  `authHeaders`, cookie guests included); every other REST call is `api`, and no header can claim
  `mcp` or `system`.
- **Bypass on purpose:** `access.unrestricted(session)` for reads that must see the whole table
  (computing someone else's access, the gate's item lookup). Scheduler, CLI and services called
  directly in tests have no `Access` at all.
- **Gotcha:** a new table with an `item_id` or `list_id` column is filtered automatically; a new
  table scoped some other way (like chat's `channel`) needs its own criteria in `read_criteria` and
  a branch in `check_object`.
- **Rows that act on their scope.** An automation runs as
  the system agent over everything in its scope, so `check_object` makes a restricted caller scope
  it to a list or section it can edit (`access.check_scope`); a workspace-wide rule needs
  unrestricted access, and `run_now` needs edit on the item's list. The generic `list_id` branch
  also checks the destination of a `list_id` set (so a recurrence rule cannot be moved into a
  view-only list), and a `move_list` action needs edit on its target list when the rule is
  created or changed (`automations.clean_actions`). A list-limited token cannot subscribe a webhook (the fan-out gate knows the
  account, not the token). `EventGate.load` treats a disabled account or a revoked token as no
  access. Restricted callers bypass the item cache (`item_cache._restricted`): cached views are shared and theirs
  differ (filtered links).

## Agent action deny-list ("Agents may not…")

`service/agent_rules.py`, `api/settings.py` (`GET/PUT /settings/agents`), MCP `slate_account
agent_rules` / `set_agent_rules`, board `components/members/AgentRules.svelte` (top of
Members), `lib/permissions.ts` (`toggleDenied`, `changesAgentRules`).

Everything above limits what a token *reaches*. This limits what an **agent** may *do* at all:
one workspace rule, so an agent on an unlimited admin token, or a project agent a guest is
driving, cannot take an action the owner has ruled out. People are never limited by it, owners and
admins included.

- **Vocabulary** (`agent_rules.ACTIONS`, fixed; each key names the event types it covers,
  `type:field` where one type means several things):

  | Key | Covers |
  |---|---|
  | `delete` | `item.purged` (purge, empty the trash), `item.attachment_removed`, `doc.deleted`, `doc.file_removed` |
  | `trash` | `item.trashed` |
  | `archive_structure` | `section.archived`, `list.archived` |
  | `share` | `share.*`, `invitation.created`/`revoked`, `session.extended`, `account.role_changed`, `section.updated:private`, `list.updated:private` |
  | `tokens` | `token.created`/`revoked`, `agent.connection_created`/`revoked`, `account.created`/`disabled`/`enabled` |
  | `automations` | `automation.created`/`updated`/`deleted`/`secret_rotated`, `webhook.created`/`updated`/`disabled`/`enabled` |
  | `export` | `GET /export` (JSON, CSV) and `/admin/backup`: a read, checked in `export.py` |

- **Default** with no row: `delete`, `archive_structure`, `share`, `tokens` denied. Stored as
  one `workspace_settings` row (key `agents.denied`, a JSON list). `GET` says `default: true`
  until someone saves.
- **Enforcement** is in the single change path: `core.Change.create/set/delete/record` call
  `agent_rules.check(session, actor, type, field=, obj=)` before `check_object()` /
  `check_record()`, so REST, MCP `LocalBackend` and batch are all covered and no route checks
  anything. An agent actor (account kind `agent`) doing a covered write under a denied key gets
  `Forbidden("agents may not <verb> here (workspace rule)")` (403). The lookup only happens for
  covered event types, so other writes pay nothing.
- **Not checked:** Slate's own work (an actor with source `system`, or the `slate` account:
  the trash purge after its days, automations, due events); consequences written with
  `Change.derive`; an agent revoking its *own* token or connection, or removing an attachment it
  uploaded itself (`_OWN_OK`); `item.deleted`, which is only the
  creator's 10-minute undo of a task it just filed (`items.delete_item`), not a delete of
  anything someone else owns.
- **Changing it:** `PUT /settings/agents {denied: [...]}` replaces the list (owner or admin
  *person*; an agent is refused even with the admin role). It is a `settings.changed` event
  (`field` `agents.denied`, `before`/`after` the lists) with its actor; saving the same list
  records nothing. The stream gate hides it from restricted callers; the Members page reloads
  on it.
- **Gotcha:** a new destructive event type is not covered until its type is added to a key in
  `ACTIONS`; `export` is the one read, so a new export path calls `agent_rules.check_read`.
