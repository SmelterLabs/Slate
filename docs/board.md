# The board

The Svelte 5 single-page app under `web/`, served by FastAPI at `/` from `SLATE_STATIC_DIR` (the Dockerfile's `web` stage builds `web/dist` and copies it to `src/slate/static`). Vite + Svelte 5 (runes) + TypeScript; no SvelteKit, no UI framework, no router, no runtime dependencies at all (everything in `package.json` is a dev dependency). Plain CSS with custom properties in `src/app.css`: dark unless the system says `prefers-color-scheme: light`.

## Layout

| Path | What it is |
|---|---|
| `src/main.ts`, `src/App.svelte` | `mount()`; the shell: token screen or nav + main + task window; hash routing via `lib/route.ts`; keyboard shortcuts; monitor (kiosk) mode; toast with an optional action (Undo); tab title "(n) Slate" from the Needs you count. |
| `src/lib/lazy.svelte.ts`, `src/components/PageLoad.svelte` | On-demand pages. Agents, Docs, Archive, Members, Audit, Health and Chat, and the tag manager, notification settings and shortcut sheet, are dynamic imports (`pages.*`, a `LazyPage` each), so the first load carries the board and the task window only. App renders `pages.x.component` once loaded and `PageLoad` meanwhile: blank for 300 ms, then "Loading…"; a failed fetch (offline, or a deploy replaced the chunk) shows Try again and Reload. Once the board is ready (not in kiosk mode) `prefetchPages()` fetches every chunk when the browser is idle, so a later open is instant and the installed app has them offline. A page's own stylesheet is imported by the page (`health.css` in `HealthPage`, `capacity.css` in `CapacityEditor`), so Vite ships it with that chunk. A new large page belongs in `pages`, not in App's static imports. |
| `src/lib/types.ts` | The REST shapes from `api/schemas.py`, including `SavedView`. |
| `src/lib/api.ts` | `fetch` wrapper: bearer header, JSON, `ApiError(status, detail)`; any 401 calls `onUnauthorized` (the store signs out). |
| `src/lib/store.svelte.ts` | `board`: the one store (a class with `$state` fields). Loading, the live stream, writes, saved views and drafts, drops, bulk, the open task's detail. |
| `src/lib/reducer.ts` | `applyEvent(data, event) -> Effects`: pure, unit-tested. |
| `src/lib/views.ts` | The views engine: filter, sort, group, due buckets, drop targets, workspace-timezone date helpers. Pure, unit-tested. |
| `src/lib/filterfields.ts` | What the filter builder offers per field (mirrors the server's `FILTER_FIELDS`) and the plain-English chip for a rule. |
| `src/lib/route.ts` | `parseRoute(hash)`, `isKiosk()`. |
| `src/lib/quickadd.ts` | `parseQuickAdd(text, now)`: the quick-add tokens. `workspaceDay()`. |
| `src/lib/format.ts` | Workspace-time labels (`dueLabel`, `ago`, `shortDate`), `isOverdue`, `hiddenAsFinished`, `describeRrule`. |
| `src/lib/history.ts` | One event -> "My Agent moved this to Ready · 2 min ago via MCP". |
| `src/lib/markdown.ts` | A small, escape-first markdown renderer (headings, lists, code, bold/italic, http(s), mailto and `obsidian:` links). Output is safe for `{@html}`. |
| `src/lib/rrule.ts` | Recurrence presets <-> one RRULE line (`toRrule`, `fromRrule`, `presetFor`, `nthOf`). |
| `src/lib/when.ts` | Date pickers: `splitWhen`/`joinWhen` (workspace date + optional time, 17:00 = no time) and `quickPicks`. |
| `src/lib/structure.ts` | `reorder()` (move up/down) and `place()` (drag or move a list into a section before another list) for the nav, the statuses editor's `strandedCount`/`draftProblem`/`draftWarning`. |
| `src/components/` | `TokenScreen`, `Nav`, `ScopePage` (one scope through its view), `ViewTabs`, `Toolbar`, `FilterBuilder` (recursive), `MultiPick`, `Dropdown`, `BoardView` (columns), `ListLayout` (grouped table), `Card`, `ItemRow`, `QuickActions` (hover reschedule/priority), `QuickAddBar` (with a list picker in wide scopes), `BulkBar`, `ShortcutSheet`, `Avatars`; the task window in `components/task/` (`TaskModal`, `ActivityPanel`, `Composer`, `AutoText`, `StatusPicker`, `PriorityPicker`, `Subtasks`, `Checklists`, `Attachments`, `Icon`; see `docs/task-window.md`); structure and editors: `Menu` (⋯, also opened by right-click), `ListSettings`, `DatePicker`, `TagEditor`, `AssigneePicker`, `RecurrenceEditor`, and structure management in `Nav`. |

## Views

`ScopePage` computes `runView(board.plainItems, scope, config, board.ctx)` once in a `$derived`; columns and rows only render the groups it returns. Pipeline: drop archived → drop subtasks unless `show_subtasks` → scope → hide items closed before today unless `show_closed` (items closed today stay, in the Done column at the end of every grouping except status ones) → toolbar search → filter tree → one stable multi-key sort (nulls last both ways; tie-break position, created, id) → group. Assignee and tag groups put an item in each of its groups. The housekeeping group "No status" appears only when non-empty; `hide_empty_groups` hides the rest.

Due columns: Overdue (open, due before today) · Today · `due_window_days` named days · Future · No due date · Done (items closed before today only with `show_closed`). Rules with no value picked (`is []`, `before ''`) are ignored, so a half-built filter never blanks the board.

| Grouped by | Dropping a card | Write |
|---|---|---|
| status (list scope) | that status | `PATCH status` |
| status kind | the item's list's first status of that kind; refused with a note if it has none | `PATCH status` |
| due | that day, keeping the workspace time of day (17:00 if none); No due date clears; Future = day after the window; Overdue refuses drops | `PATCH due_at` |
| priority | that priority, or none | `PATCH priority` |
| assignee | replaces the assignees with that one (Unassigned clears) | `PATCH assignees` |
| list | moves it (the server maps the status by kind) | `POST /items/bulk {list_id}` |
| tag, section, none | not a drop target | |

All drops are optimistic through `#optimistic()` and roll back on refusal. Bulk edits use `POST /items/bulk` in chunks of 500; status kind across lists is one bulk call per list. Saved views: `GET /views` at load; `view.*` events reload them (debounced 50 ms). Unsaved changes live in `board.drafts[viewKey]`; a scope with no saved view shows `default:<scope>` (`defaultConfig()`), and saving it creates a view.

**Plain mirror.** `board.items` is the `$state` map components read; `board.plainItems` is a plain copy (`#sync(id)` after every write; `#syncAll()` after a load keeps the loaded objects themselves as the mirror and hands `board.items` a `structuredClone`, since snapshotting 20,000 proxies takes 1.9 s) that the engine, nav counts and the overdue count read. Reading a proxied property costs ~1.6 µs, which at 3,000 items × a dozen fields is 60–130 ms per regroup. **Every new write to `board.items` must call `#sync(id)`.**

## Store and reducer

The store holds **every non-archived item** (`GET /items` with no filter) keyed by id, plus sections, lists (with statuses) and accounts. Boards, the list view, nav counts, the overdue count and the sidebar's Today count (`#tally()`: open per list, overdue, due by end of today, computed once per change) are all derived from that one map, so a single event updates every view. Done items stay in the map; `hiddenAsFinished()` hides done/cancelled items finished before today's midnight in the workspace timezone, client-side only. At 20,000 items the first load is about 1.5 s (`docs/PERFORMANCE.md`); the server answers it from its item cache.

Accounts: `GET /accounts` needs `admin`; with a lower-scoped token the store knows only `/me` plus every assignee it has seen, and unknown actors show as "Someone".

`applyEvent()` mutates the store in place (it works the same on a plain object in tests and on the `$state` proxy) and returns `Effects`:

| Event | From the payload | Otherwise |
|---|---|---|
| `item.updated` with a column `field` | set that field (`after`) | unknown item -> refetch it |
| `item.updated` field `tags` | replace tags | |
| `item.status_changed` | status from the list's statuses by id | unknown status -> refetch |
| `item.created` | build the item from the column snapshot (tags/assignees empty; their own events follow); bump the parent's `subtask_count` | unknown list or status -> refetch |
| `item.archived/unarchived` | `archived_at` | |
| `item.deleted` | remove; un-bump the parent | |
| `item.assigned`, `item.split` | | refetch (handles only / subtask count) |
| `item.reported` | set `agent` from `after` (`done`/`idle` clear it) | |
| `item.commented` | bump `comment_count` (only this type; mentions and watches do not) | |
| `section.*`, `list.*` | | reload nav; `list.statuses_changed` reloads everything |

A refetch is `board.refetchItems(ids)`: `GET /items?ids=…&include_archived=true` in chunks of 200, batched 50 ms; an id missing from the answer is removed locally. Any item event also refreshes the open task window if it is about that item or one of its subtasks.

**Batched drain.** Stream events go into a queue. A lone event is applied at once; while they keep coming, the board applies the queue in one batch (`flushSync` so the cost is measured), then waits as long as that batch took, so at most half the page's time goes to catching up (each re-run of the views costs ~100 ms at 20,000 items).

## Agents page

`#/agents` renders `AgentsPage.svelte` instead of a scope page. Its data is `lib/agents.svelte.ts`: the roster (`GET /agents?kind=all`) and a feed seeded by `GET /events?tail=true&limit=100&actor_kind=agent`, loaded the first time the page opens. After that the board store passes every stream event to `agents.onEvent()`, which prepends agent events to the feed (max 200; due-time events and the comment a report posts are left out) and re-reads the roster, debounced 300 ms, after a report or an account change. Nothing polls. The header's "API median" is a weighted p50 from `GET /stats`, read once on open.

Above the feed, "Next up" is `GET /agents/plan` (`lib/plan.ts` for the types and the time words): per agent, the tasks the work loop will hand it in the next 24 hours with when and why (`due`, `Run now`, `asked`) and the tasks it holds now with their age. The page re-reads it 400 ms after any `item.*`, `comment.*` or `account.*` stream event (`board.onEvent`, like the audit log), so a Run now pressed anywhere moves the block at once.

## Live stream and catch-up

One `EventSource` on `/api/v1/events/stream?token=…[&since=<cursor>]` (EventSource cannot send headers; the server redacts `token=` from the access log).

1. **Start:** open the stream *first*, with no `since` (the server starts at its newest event), and wait for it to be open (`onopen`: the response has started, so the server's bus subscription exists; the `: ready` comment is invisible to EventSource). Buffer everything it sends. Then load sections, lists and items over REST, then apply the buffer. An event committed before the REST reads is in the data; one after is in the buffer; one in both is applied twice, which is harmless (every reducer case is idempotent: set-to-value, `created` skips a held item, and what the payload cannot say idempotently (`item.commented`'s count, a deleted subtask's parent count when the item is already gone) is refetched instead of counted).
   **A failed start retries.** If `/me` or the load throws anything but a 401, or the stream drops before the board is ready, the stream and buffer are dropped and, after the reconnect backoff (1 s, 2 s, 4 s… capped at 30 s), the retry goes through the reconnect path: `/me` first (a 401 signs out; a stream the server refuses never opens, so nothing else would see it), then `start()` again. `signOut` cancels it. Each `start()` takes a generation (`#startGen`, bumped by `#stop()` and a retry), so a start overtaken by a refresh, a retry or a sign-out gives up at its next await instead of applying stale data.
2. **Cursor:** `lastEventId` is the id of the last event received; ids are time-ordered strings, so `<=` drops a duplicate.
3. **Drop:** on `onerror` (or the browser's `offline` event) the store closes the EventSource itself (no native auto-retry), shows "Reconnecting…", and retries with backoff 1 s, 2 s, 4 s… capped at 30 s. Each retry first calls `/me`: a 401 signs out instead of retrying forever. `online` retries at once.
4. **Reconnect** opens a new EventSource with `since=lastEventId`; the server replays everything after it, then goes live. If no event was ever received (no cursor), it reloads from scratch instead (step 1).
5. **Full reloads** asked for by events (`Effects.reload`: `list.statuses_changed` and every `*.unarchived`) are debounced 100 ms (`#scheduleReload`), and `#loadAll` carries a generation so a load that answers after a newer one started writes nothing (an older answer never overwrites a newer one; `refresh()` and access reloads race the same way).

Unit tests: `src/lib/store.test.ts` (happy-dom; `fetch` and `EventSource` faked, api layer and reducer real) covers the open-before-load order, the failed start and its backoff, the buffer applied once, the reload debounce and the generation guard.

**Named events gotcha:** the server sends `event: <type>`, and EventSource only delivers a named event to a listener registered for that exact name (`onmessage` sees only unnamed ones). `EVENT_TYPES` in `store.svelte.ts` lists every type Slate emits. **A new event type must be added there**, or the board will ignore it.

**Field ≠ column gotcha:** the reducer writes `after` into the item only for `item.updated`, `item.archived`, `item.unarchived` and `item.moved` (`COLUMN_CHANGES`); any other `item.*` type that falls through refetches. Notices such as `item.due` / `item.overdue` name `field: due_at` but carry `{due_at}` as `after`; writing that object into `due_at` makes the views engine throw, and a throw there freezes the whole board until a reload (the store keeps working, so toasts still show). A new notice-style event gets its own `case` in `reducer.ts`. E2E: `e2e/complete-leaves.spec.ts`, which runs the real scheduler due step on the e2e database (`e2e/due_tick.py`) and replays it by reconnecting.

## Writes

- Drag to a column: `moveToStatus()` sets the status locally, `PATCH /items/{id} {status: <id>}`, and on error restores the old status and shows the server's `detail` in a toast. The stream's `item.status_changed` then confirms it.
- Quick add: one `POST /items` (`title`, `tags`, `assignees`, `priority`, `due_at` as `YYYY-MM-DD`; Slate applies 17:00 workspace time). The store does not insert the response: the card arrives via `item.created`.
- AI add (`AiToggle.svelte`, `board.aiAdd`, server `service/aiadd.py`): when `GET /me` says `ai_add` and this device has not switched it off (`localStorage slate.aiAdd = 0`), quick add and `ColumnAdd` send one `POST /items/ai {list_id, text, defaults}` (defaults = the column's meaning). The server files one item with the words as typed and fills it in after the response with its own model, as the `slate` account; every field arrives over the stream as ordinary `item.*` events (`session_ref` `ai-add`). Rule 1 holds: one item, no children.
- Task window edits `PATCH` and store the response; subtasks are `POST /items {parent_id}`; ticking one sets the list's first `done`-kind status (unticking: the list's default status).
- Every board write is recorded with `source=ui`: `lib/api.ts` sends `X-Slate-Client: browser` and `X-Slate-Source: ui` on every request, and `api/auth.request_source` accepts `ui` only with both; every other REST caller is `api`.
- Recurrence line: the task window loads the rule with `GET /recurrences/{id}` and shows its server-made `description`.
- Structure: sections and lists are created, renamed, reordered and archived/restored from the nav with the ordinary REST calls; a list moves to another section by drag (drop on a list = before it, on a section heading = last) or its ⋯ / right-click menu, as `PATCH /lists/{id} {section_id, position}` plus position PATCHes for its new neighbours; each write reloads the nav at once and the stream tells the other boards. Restoring reloads everything, because items of archived lists are not in the default load.
- List settings: one `PUT /lists/{id}/statuses` (with `fallback` when removed statuses hold items), then `PATCH /lists/{id} {default_status}` if needed.
- Field editors: each change is one `PATCH /items/{id}`; move to list is `POST /items/bulk {ids: [id], changes: {list_id}}`; Complete moves to the list's first `done` status (and back to the default status). The recurrence editor previews with `GET /recurrences/preview` (debounced 250 ms, latest answer wins) and saves with `POST /recurrences {..., adopt_item_id}` or `PATCH /recurrences/{id}`; Pause/Resume/End are the rule endpoints.

## Comment dots

`Card.svelte` shows one dot in the bottom-right corner (`.card-dot`, `board.css`) when a comment is
new to the viewer since they last opened the card: `agent` (small gray), `human` (blue; guests are
people) or `mention` (blue, pulsing `card-dot-pulse`; still glow under reduced motion), strongest
wins. Rules and SQL: `service/item_reads.py`. Per-account, so they are not in the item view (the
item cache serves one view to everyone): `board.commentDots` is loaded from `GET /me/comment-dots`
with the board and reloaded (150 ms debounce) when `lib/commentdots.ts dotsChangedBy` says an event
may change it: someone else's `item.commented`, `comment.edited`, `item.mentioned`,
`comment.assigned`, or this account's own `item.read` (another device opened the card).
`board.open(id)` clears the dot locally and sends `POST /items/{id}/read` (writes nothing when
nothing is new); a reload while the card is open reads its dot at once. The upper right corner is
the complete circle and ⋯, so the dot shares the corner with the copy-ID button
and fades out on hover. E2E: `e2e/comment-dots.spec.ts`.

## Quick-add grammar

Whitespace-separated words; tokens can sit anywhere. `#tag` (lowercased, deduped), `@handle` (lowercased), `~list`, `!urgent|!high|!normal|!low` or `!1`..`!4` (other bang words stay in the title), `due:today|tomorrow|<weekday or 3+ letter prefix>|YYYY-MM-DD` and `start:` likewise (a weekday is the next one, today if it is that day; resolved on the workspace calendar). An unrecognised `due:` stays in the title. Bare dates, times and repeats are read too (`docs/task-window.md`, "Quick add"). Everything else, single-spaced, is the title; an empty title does not submit.

## Run and test

All from `web/`, with system Node (v22 or later; CI uses 22).

```
npm ci
npm run dev          # Vite on :5173, proxies /api and /mcp to 127.0.0.1:8000 (run uv run slate serve too)
npm run check        # svelte-check, warnings fail
npm run build        # -> web/dist
npm test             # unit (Vitest) then e2e (Playwright); e2e needs a fresh `npm run build`
npm run test:unit    # Vitest only: src/**/*.test.ts
npm run e2e          # Playwright only
```

**E2E** (`e2e/`, `playwright.config.ts`): Playwright's `webServer` runs `uv run slate migrate && uv run slate serve --port 8765` from the repo root on a fresh SQLite file in `web/.e2e/` (wiped by the runner at start), with `SLATE_STATIC_DIR=web/dist` and the scheduler off. So the tests exercise the production path: FastAPI serving the built board. `e2e/global-setup.ts` bootstraps `alex` (admin), creates an agent account with a write token, a section, a list (Open/Ready/Done) and items, and writes tokens and ids to `web/.e2e/seed.json`. Tests skip the token screen with `addInitScript` setting `localStorage['slate.token']`. Chromium only, one worker; tests share the seeded database and each opens its own page. `SLATE_SCREENSHOTS=1 npm run e2e` writes dark-mode screenshots to `docs/img/` (not checked in). `SLATE_E2E_PORT` changes the port; `SLATE_E2E_CHROMIUM=/path/to/chrome` uses a preinstalled Chromium of another revision (cloud sandboxes). `e2e/views.spec.ts` adds a section with a list whose items spread over the due columns, and runs at 1920×1080: HTML5 drag in Playwright does not autoscroll the board, so the target column must be on screen. On Windows, run the CLI with `node node_modules/@playwright/test/cli.js test -g …`: `npx.cmd` mangles quoted arguments when its path has a space.

The dropped-stream test uses `context.setOffline(true)`. That does not cut an open loopback socket, but it fires `offline`, which the store treats as a drop.
