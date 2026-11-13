# The board's views and layouts

The board as a whole, structure editing, the views engine, the three layouts (Board, List, Table),
the favorites bar, and the ClickUp
parity pieces (dependencies, estimates, tags). The board's module map and test recipe are in
`docs/board.md`; the task window is `docs/task-window.md`; look and phone rules are
`docs/design-system.md`.

## Board

Security headers: `app.BOARD_HEADERS` (a `Content-Security-Policy` with `script-src 'self'`, inline
styles allowed, images and media from anywhere, `frame-src blob:` so the attachment and file-doc PDF
viewers can frame a fetched blob, no framing of the board itself; `X-Frame-Options`, `nosniff`,
`Referrer-Policy`) go on everything `BoardFiles` serves except the hashed bundles. A new external
resource the board loads (a font host, say) must be added to `app.BOARD_CSP`; file downloads have
their own stricter policy.

Svelte 5 + Vite + TypeScript SPA in `web/`, served at `/` from `SLATE_STATIC_DIR`. One store
(`web/src/lib/store.svelte.ts`) holds every non-archived item; a pure reducer
(`web/src/lib/reducer.ts`) applies SSE events in place and asks for a single-item refetch only when
the payload is not enough. The stream opens *and is open* before the first REST load (`start()`
awaits `onopen`, buffering what arrives, so an event committed between the REST read and the
subscription cannot be lost) and reconnects with `since=<last event id>`. A start that fails (any
error but a 401, or the stream dropping before the board is ready) drops its stream and buffer and
retries with the reconnect backoff (1, 2, 4… 30 s; `#retryStart` → `#reconnect`, which checks `/me`
first so a revoked token signs out; cancelled by `signOut`); each `start()` takes a generation so an
overtaken one gives up at its next await. Full reloads asked for by events (`fx.reload`:
`list.statuses_changed`, `*.unarchived`) are debounced 100 ms and `#loadAll` carries a generation,
so an older answer never overwrites a newer one. The reducer is idempotent (the catch-up replay
repeats events): counts the payload does not carry (`comment_count`, a deleted subtask's parent) are
refetched, never incremented. `store.test.ts` covers the lifecycle with a fake `fetch` and
`EventSource`. The task window is a real modal: it takes focus on open, traps Tab, and hands focus
back to its opener on close (`TaskModal.svelte`). Full design, the catch-up sequence, the quick-add
grammar, the named-event gotcha (new event types must be added to `EVENT_TYPES`) and how to run the
unit and Playwright tests: **`docs/board.md`**. CI job `web`: `npm ci`, `npm run check`, `npm run
build`, `npm test` (Vitest, then Playwright against a real `slate serve`).

## Structure and editors on the board

All in `web/`; the only server pieces are the recurrence preview and adopt (`docs/recurrence.md`).
Details in `docs/board.md`.

- **Nav** (`Nav.svelte`): + Section, per-section + and ⋯, per-list ⋯ (`Menu.svelte`), inline
  rename/add inputs, inline archive confirm, an Archived panel with Restore. Section and list writes
  are `POST /sections|/lists` and `PATCH` (name, position, `archived`, and for lists `section_id`);
  reorder renumbers positions 0..n-1 with one PATCH per moved row (`lib/structure.ts
  reorder()`/`place()`). Lists drag in the nav (drop on a list = before it, on a section heading =
  last) and have "Move to <section>" in their ⋯ menu; right-click opens a row's ⋯ menu. New lists
  get the API default statuses, To do (open) / In progress (active) / Done (done).
- **List settings** (`ListSettings.svelte`, opened by `board.openListSettings(listId)`; mounted by
  Nav, outside the `.nav` element because the phone nav is CSS-transformed and would trap a fixed
  panel): one `PUT /lists/{id}/statuses` with `fallback` when removed statuses still hold items
  (counted client-side by `strandedCount()`), then `PATCH default_status` if it moved.
- **Field editors** (in the task window, `docs/task-window.md`): `DatePicker` (quick picks and
  optional time; `lib/when.ts`), `TagEditor` (autocomplete from `GET /tags`), `AssigneePicker`, move
  to list (`POST /items/bulk {list_id}`), one-click complete (`board.toggleComplete`), unarchive,
  and `RecurrenceEditor`.
- **Recurrence editor**: presets <-> RRULE in `lib/rrule.ts` (`toRrule`, `fromRrule`, `presetFor`;
  Vitest round trips). `fromRrule` returns null for anything a preset cannot say exactly (BYHOUR,
  mixed weekdays with ordinals, several months...), which the editor shows as Custom. The live
  preview calls `/recurrences/preview` 250 ms after the last change, keeping only the latest answer.
  "Make recurring" posts the rule with the item's title, body, priority, tags, assignees and
  `adopt_item_id`.
- **Reducer**: `section.unarchived`/`list.unarchived` reload everything (the default item load
  leaves out items of archived lists). `item.moved` is in `EVENT_TYPES`. Recurrence events refresh
  a rule the store already holds; the task window loads rules with `GET /recurrences/{id}`.
- **Layout**: the store methods are in the `// --- structure & editors` block of the `Board`
  class; the CSS is the "structure from the nav, list settings, drawer editors, recurrence editor"
  block in `app.css`. ScopePage's ⚙ on list pages calls `board.openListSettings`. One-click
  complete is the store's `toggleComplete` (with Undo), shared by cards and the task window.

## Views board

**Filter completeness:** `isActiveRule`/`isActiveFilter` in `views.ts` drive matching, badge counts
and chip summaries together. Empty selections, blank text/numbers and partial date ranges are
drafts; false/zero and valueless comparators such as `empty` remain real predicates. Recursive
matching uses an absent (`null`) result for inactive branches, not `true`: otherwise an empty branch
widens an OR. Only an inactive root matches everything. Keep drafts in config so new and previously
saved rules remain editable; explicitly clearing the last checkbox removes its rule. Toolbar chips
retain original config indices for removal. Account choices use `board.pickable`, with labeled
selected historical accounts appended; `me` remains viewer-relative, handles/IDs fixed. This is
client-side normalization: stored view configs and accounts are not rewritten. Regression coverage:
`views.test.ts`, `filterfields.test.ts`, `e2e/filter-cleanup.spec.ts`.

The board's grouping, filtering and sorting live in one pure module, `web/src/lib/views.ts`
(`runView(items, scope, config, ctx) -> Group[]`), evaluated client-side over the store for every
scope (`everything`, `section:<id>`, `list:<id>`), with the saved-view config
(`GET/POST/PATCH /views`). Due buckets are calendar days in the workspace timezone (`dayOf()`,
cached per instant; `workspaceInstant()` is DST-safe). Toolbar edits are drafts per view in the
store until Save. Each
group carries a `Drop` (what dropping a card there writes), so drag-and-drop is one
`board.applyDrop()`. Routing: `#/everything[/v/<id>]`, `#/section/<id>[/v/<id>]`,
`#/list/<id>[/v/<id>]`, `#/today` → the seeded Agenda view; `?kiosk=1` is monitor mode.

**Performance gotcha:** reading 3,000 items through Svelte `$state` proxies costs ~10× a plain
object (runView 60–130 ms vs 8 ms). The store therefore keeps a plain mirror of every item,
`board.plainItems` (reactive through `board.rev`), refreshed by `#sync(id)` at every write to
`board.items`. Any new code that mutates `board.items` must call `#sync`, or the views will not see
the change. The engine also gets snapshots of the config and lists, columns render 25 cards before
"Show more", and hover actions mount on first hover. Measured with 3,000 items in Chromium: regroup
60–100 ms including two paint frames, an API PATCH visible on the board in ~50 ms. Details, the
event and drop tables: `docs/board.md`.

**Bulk status kind:** `POST /items/bulk` takes `changes.status_kind`; the service resolves it per
item to the first status of that kind in the item's own list (after any `list_id` move), so a
cross-list status change is one transaction. A list with no status of that kind refuses the whole
batch (422 naming the lists). The bulk bar leaves those items out client-side first and flashes
which lists were skipped. `changes` is checked by `schemas.BulkChanges` (the PATCH fields and
types plus `add_tags`, `remove_tags`, `archive`, `trash`, `list_id`, `status_kind`; unknown fields
and wrong types are a 422 before anything runs); it stays a plain object in the schema so the MCP
tool's input schema has no `$ref`.

## View polish

Details across the layouts; all board-side, nothing on the server.

- **Board**: `.card.cu` has a 3px left edge coloured by priority (`.card.cu.prio-stripe-*` in
  `board.css`, after the shorthand `border`, which would otherwise reset the app.css stripe
  colours) and red when `cardDue` says overdue (`.card.cu.overdue`). The copy-ID square is
  `opacity: 0` until hover, focus-within or focus.
- **List**: the Due column is 150px (110 clips "Tomorrow 11:00 AM"). Grouped by status the Status
  column goes (`ListLayout` `noStatus` → `ItemRow` prop and `.row.no-status` grids, incl. `.wide`);
  group headers show the group's summed `estimate_minutes` (`groupMinutes`, `formatEstimate`).

## ClickUp-style board

The main screen for many people is the due board, so it copies ClickUp's board closely. All board
view code is in `web/`; the server's part is `comment_count` on `ItemOut` (batched in
`service/items.views()`, bumped live by the reducer on `item.commented`) and two view-config keys.

- **Files**: `BoardView.svelte` (columns, fold, column ⋯ menu), `Card.svelte` (title, a counts row,
  then a wrap of pills), `ColumnAdd.svelte` (+ Add Task), `CardMenu.svelte` (⋯ and right-click;
  **Move to…** swaps the menu for `MovePicker.svelte`, the searchable section-grouped list picker
  also used by the task window header and List field and the selection bar; filtering is
  `lib/move.ts`), `DuePicker.svelte` / `PriorityPicker.svelte` (pills you click), `Icon.svelte`
  (inline SVG paths, no icon font), `lib/cards.ts` (due label wording, tag colours, column-fold
  memory), `lib/theme.ts`. Styles: `web/src/board.css`, imported after `app.css` by `main.ts`, so it
  refines the base rules without editing them.
- **View config**: `card_fields` can be `description, subtasks, comments, checklist,
  attachments, due, start, priority, tags, status, assignees, list, waiting, recurrence, estimate,
  blocked` (`ALL_CARD_FIELDS` is the order a card shows them; server `CARD_FIELDS` must match).
  The keys `title_lines` (0 = whole title, 1-3) and `collapse_empty` (default true for Everything
  and sections, false for lists; `normalizeConfig` fills it per scope). Checklist and attachment
  pills read `checklist_done/checklist_total/attachment_count` on items (`docs/task-window.md`).
- **Done column**: every grouping except status and status kind ends with a `done` group; closed
  items go there whatever their date (`dueBucket` returns `done`).
  Its `Drop` is `{kind: 'done'}`, so dropping a card on it completes it. Items closed before today
  still need `show_closed`. The default Everything/section view does not filter to open items and
  sorts by due date, then priority (the seeded "This week" view matches).
- **Folding**: a column is a strip when the viewer folded it, else when it is empty and
  `collapse_empty` is on (`isCollapsed`). The viewer's folds are per view in localStorage
  (`slate.collapsed.<viewKey>`), not in the saved view, so folding never makes a view dirty.
- **+ Add Task** files through `board.quickAdd(listId, parsed, board.columnDefaults(drop, listId))`:
  the column's drop says what the new task gets (due day at 5 pm, status, status of that kind in the
  target list, priority, assignee, list); quick-add syntax in the text wins over it.
- **Menus over columns**: columns scroll on their own (`.cards` has `overflow-y: auto`), which clips
  absolutely placed popovers. `Dropdown` has a `float` prop that pins the panel to the window
  (fixed, flipped up near the bottom); card menus and pickers use it.
- **Cheap cards**: the pickers and the ⋯ menu mount on first hover (`armed`); until then the pills
  are plain spans.
- **Theme**: `:root[data-theme=light|dark]` overrides the system preference; Customize → Theme sets
  it per browser.

## Layouts

A view's `layout` is `board`, `list` or `table` (`service/views.LAYOUTS`, `lib/views.LAYOUTS`).
The Timeline, Calendar and Box layouts are no longer part of Slate. A saved view (or an unsaved
draft in prefs `view_drafts`) that still names one reads as `list`, without the `calendar_span` and
`timeline_zoom` settings only they used: the server does it in `views.view_out`, the board in
`lib/views.withKnownLayout` (through `normalizeConfig` and `lastview.applyDiff`). Writing one of
those layouts is refused (422); the two old settings are accepted on write and dropped, so a board
page opened before an upgrade can still save. No migration rewrites stored configs. Capacity
(`GET /capacities`, `GET /workload/boxes`) stays for agents and the Agents page's capacity editor.

## Favorites bar

Per-account pins, ClickUp-style: a `favorites` row (account_id, kind `list|section|view|item`,
target_id, position; unique per account+target; no FK to the target since it can be any of four
tables). Service `slate/service/favorites.py` always acts for the caller (the actor's account):
`add_favorite` is idempotent and can insert at a `position` (renumbering the rest,
`favorite.moved`), `remove_favorite` hard-deletes (`favorite.removed` keeps the history),
`reorder_favorites` takes every id once. `favorite_out()` adds the target's `name`, the scope a chip
opens (`scope_kind`/`scope_id`: the list/section itself, a view's scope, an item's list) and
`available` (false while the target is archived or gone; the favorite survives so a restore brings
the chip back). REST `GET/POST /favorites`, `PUT /favorites/order`, `DELETE /favorites/{id}`; MCP
`slate_favorite` (list, add, remove, reorder), cap 50 per account.

Board: `FavoritesBar.svelte` sits in App's shell in grid row 2 over the main column
(`docs/design-system.md`; rows are topbar [phone only] / favorites bar / nav+main; not shown in
kiosk mode). Chip
resolution and drag order are pure in `web/src/lib/favorites.ts` (`chipFor`, `moveBefore`; Vitest).
The store keeps `board.favorites` (loaded in `#loadAll`), reloads it only for `favorite.*` events
whose actor is this board's account (favorites are per account but the stream carries everyone's),
and exposes `favoriteOf`, `addFavorite`, `removeFavorite`, `toggleFavorite`, `moveFavorite`
(optimistic; `PUT /favorites/order`, reverts on error). Pin points: ☆ in the nav row actions and ⋯
menus (lists, sections), the view tab ⋯ menu (★ marks pinned views), dragging a nav list onto the
bar, and `s` for the open task or the card under the pointer. The task window can call
`board.toggleFavorite('item', id)` for a star of its own. CSS: the `/* --- favorites bar ... */`
block after the shell in `app.css`; phone rows are 40 px tall and the bar hides when empty.

## ClickUp parity: dependencies, estimates, tags, Table

**Schema**: `items.estimate_minutes`; `item_links` (from blocks to, or relates; unique, not self).
Custom fields and status templates are not part of Slate.

**Estimates.** `PATCH /items/{id}` and `POST /items` take `estimate_minutes` (minutes, null clears).
Item output carries `estimate_minutes`, `blocked_by`, `blocking`, `related` and `blocked` (waits on
an open item).

**Links.** `POST /items/{id}/links {kind: blocks|blocked_by|relates|duplicate_of|duplicated_by,
other}` (`duplicates` is stored from the copy to the original); DELETE
takes `?kind=&other=`. Both items get `item.linked`/`item.unlinked`. A blocks-cycle is refused
(422). `GET /items?blocked=true` and the view filter `blocked` use `blocked_item_ids()`.

**Blocked / unblocked** (`items.blocked_since`). Blocked is derived (an
open blocker: not done/cancelled, not archived; trash archives) and never set by hand.
`core.apply_change` calls `links.settle(change)` after the block's writes and before commit, under
`access.unrestricted` (a list-limited agent finishing a blocker must still clear a task in another
list it cannot see). `settle` looks only at the change's `SETTLE_TYPES` events (status,
archive/unarchive, trash/restore, link/unlink, delete/purge) and `list.statuses_changed` (a status
kind moving into done), recomputes those items and the items they block, and records `item.blocked`
(`after.blocked_by`) or `item.unblocked` (`before.blocked_since`, `after = {cleared_by, open}`) with
`Change.derive` (sets the column and records the event, no access check). Same actor and session_ref
as the change, so an automation's change that unblocks does not trigger automations (the usual
no-chains rule). `blocked_since` exists only to tell a transition from no change; read `blocked`
from the view. Costs nothing for changes without those event types and one indexed query when they
block nothing. Inbox reason `unblocked` (assignees only, only when `after.open`; push default on),
automation triggers `task_unblocked` (skips closed tasks) and `task_blocked`. The next-task call
(agent work loop) skips with `Item.id.not_in(links.blocked_item_ids())`. Board: the reducer flips
`item.blocked` from these events (a status change does not refetch what it blocks),
`lib/format.ts` `openBlockers`/`blockedTitle` name the open blockers, card field `blocked` is
default-on (server and `views.ts`), the task window shows `[data-banner=blocked]`, My Work has a
Depends on card (`mywork.ts` `blocked`), and `lib/activity.ts` hides the `item.blocked` that just
repeats an `item.linked`. E2E `e2e/blocked.spec.ts` (desktop and phone).

**Tags.** `POST /tags/{tag}/merge {into}` retags every item and doc (an `item.updated` or `doc.updated`, field `tags`, for each) and deletes the
source; `DELETE /tags/{tag}` untags items and docs the same way, then deletes (`tag.deleted`).

**Views.** `layout` includes `table`. Sort key `estimate`; filters `blocked`, `estimate`.

**Board.** `TableView`/`TableRow` (editors mount on hover, totals per group), `EstimatePanel`
+ `DependenciesPanel` (they take only `item`; the task window mounts them), `TagManager` (nav foot
"Tags"). `formatEstimate`/`parseEstimate` live in `lib/format.ts`. Store methods are in the
"ClickUp parity" block; CSS is the "ClickUp parity" block in `app.css`. The reducer sets
`fx.tags` for `tag.*` events and refetches on link changes; an item a status change unblocks
hears its own `item.unblocked`.

## How the pieces fit together

- The task window mounts `EstimatePanel` and `DependenciesPanel` at its `<!-- panels -->` marker,
  above the description, and has a ☆ in its header (`board.toggleFavorite('item', id)`).
- Board cards show `estimate` and `blocked` pills. `ALL_CARD_FIELDS` lists the card fields; server
  `CARD_FIELDS` must match.
- The activity column names estimates and dependencies through `HistoryContext.title`
  (`lib/history.ts`).
- Gotcha: `app.css` scopes the shell's grid rules to `.shell > .main` because the task window has
  its own `.main` column; a bare `.main` rule pushes the window's content into an implicit grid row.
