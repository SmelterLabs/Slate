# Trash, the Archive page, export, backup and duplicates

Where work goes when it leaves the board, and how data leaves Slate.

## Trash, Archive page, export and backup

- **Trash** (`service/trash.py`, migration `0b5732291aa2`, column `items.trashed_at`). `trash_item`
  stamps the item and its subtasks (`trashed_at`, and `archived_at` when not archived already), one
  `item.trashed` each. `restore_item` brings back the item and the subtasks trashed in the same
  instant; a subtask with a trashed parent is refused (restore the parent). `purge_item` (trashed
  only) deletes the item and its subtasks with `item.purged` (comments, checklists, files and links
  cascade; events stay), deepest level first with a flush per level (`items.parent_id` has no
  ORM relationship, so one flush would delete in id order and break the foreign key).
  `empty_trash` (admins) purges everything in batches. The scheduler hook `purge_expired` purges
  items trashed more than `settings.trash_days` (`SLATE_TRASH_DAYS`, default 30; 0 keeps them
  until emptied) ago, as the `slate` system agent, one change per top item so one failing tree
  cannot hold back the rest. Archive and unarchive (one item or bulk `archive`) reach every
  subtask at any depth (`items._set_archived`). `unarchive_item` and bulk `archive: false`
  refuse trashed items. Bulk `trash: true|false` (sent alone in `POST /items/bulk`)
  calls `trash_items` / `restore_items`: one change for the whole set, all at one instant, so the
  board's multi-select Delete is one call and its Undo is one restore call (`board.trashMany`). Item
  views carry `trashed_at` and `purge_at`.
- **Query filters** on `GET /items` (and `slate_item list`): `archived=true|false` (true = archived,
  not trashed), `trashed=true` (only the trash; trashed items are excluded everywhere else,
  `include_archived` included), `closed=true|false` (status kind done/cancelled vs open/active),
  `order=position|completed|archived|trashed|updated|created` (anything but position is newest
  first, nulls last). Reading one item by id still works when it is trashed (the task window shows a
  Restore banner).
- **REST**: `POST /items/{id}/trash|restore` (item), `POST /items/{id}/purge` (204), `POST
  /trash/empty` (admin, `{purged}`), `GET
  /export?format=json|csv&list_id=|section_id=&include_archived=` (attachment; CSV has a BOM for
  Excel), `GET /admin/backup` (admin; SQLite online backup into a temp file, removed after the
  response). **MCP**: `slate_item` `trash|restore|purge`; `slate_export` `json` (JSON only, because
  the stdio binding parses every REST answer as JSON) and `empty_trash` (admin).
- **Export** (`service/export.py`): JSON is `{format: "slate-export", version: 1, exported_at,
  scope, accounts, sections, lists (with statuses), tags, items (the API item shape), comments,
  checklists, checklist_items, links, docs, messages, message_reactions}`; chat follows the scope (a
  list's channel; a section's channel and its lists' channels; every channel, General too, for
  everything), replies are messages with `parent_id`, read cursors are left out; never token hashes
  or secrets. CSV is one row per task (`CSV_COLUMNS`), names instead of ids. The backup holds
  everything, which is why it is admin-only.
- **Board**: `ArchivePage.svelte` at `#/archive` (`?tab=archived|lists|trash`): Done
  (`closed=true&archived=false&order=completed`), Archived tasks, Lists and sections (the
  archived ones), Trash (Restore, Delete forever with a confirm, admin Empty trash,
  days left). Header: Export JSON/CSV and admin Backup, downloaded through a blob because they need
  the token (`lib/archive.ts`). It reloads on item events (250 ms debounce). "Move to trash" is in
  the task window ⋯, the card menu and the palette, with Undo (restore) in the toast; list and
  section ⋯ menus export that scope. The reducer treats `item.trashed`/`item.restored` like archive
  (sets `trashed_at` and `archived_at`; restore of an unknown item refetches) and `item.purged` like
  delete. The inbox counts `item.trashed` as "archived".

## Duplicates

- **Link kind** `duplicates` (`LinkKind`, a non-native enum: migration `9d41c7e2a6b8` rebuilds
  `item_links` with the wider CHECK; downgrade deletes duplicate links first). From an item's side
  the kinds are `duplicate_of` and `duplicated_by`; item views and `GET /items/{id}/links` carry
  both lists (`links.LINK_KEYS` is the one list of link fields). Duplicate links never block.
- **Board**: cards and table rows of an open duplicate show a *Duplicate* badge (always on, not a
  card field). The Dependencies panel lists *Duplicate of* and *Duplicates* and offers *Duplicate
  of* as a link kind. History phrases for the duplicate link kinds are in `lib/history.ts`.
