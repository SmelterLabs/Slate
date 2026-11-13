# Design system, polish, phone and installed app

The look (tokens, motion, icons, shared pieces, the build revision) and everything the phone
and the installed app (PWA) need: manifest, service worker, touch drag, sheets, share target.

## Design system and polish

One look across every screen, ClickUp-style, with a violet brand.

- **Tokens** live at the top of `web/src/design.css`: surfaces (`--bg` main, `--panel` sidebar/top
  bar/inputs, `--raised` menus and windows, `--card`, `--card-hover`), ink (`--text`, `--muted`,
  `--faint`), lines (`--line`, `--line-strong`), brand (`--accent`, `--accent-hover`, `--accent-ink`
  for text on soft backgrounds, `--accent-soft`, `--brand-gradient`), status colours (`--danger`,
  `--overdue`, `--success`, `--warning`), depth (`--shadow-sm|md|pop|modal`, `--scrim`,
  `--focus-ring`), shape (`--radius-sm|radius|radius-lg|radius-pill`), rhythm (`--sp-1..6`,
  `--fs-xs..xl`) and motion (`--ease`, `--ease-spring`, `--dur-1..3`). Dark is `:root`; light is the
  same list twice (the system-preference media block and `:root[data-theme='light']`), so change
  both. Use tokens, not raw colours, in new styles. Base type is 14 px.
- **`web/src/design.css`** is imported first by `main.ts` and holds the tokens and what spans
  components: focus and selection, scrollbars, hover and press feedback, menu and window
  entrances, skeletons, empty states and `.sr-only`. The sidebar, favorites bar, toast, reconnect
  banner, sign-in screen and view bar are styled once, in their own sheet. `app.css` is the shell
  and the features, `board.css` the board.
- **Motion rules**: animate only `opacity` and the individual `scale`/`translate` properties, never
  `transform` (the sheet, list settings and toast centre themselves with `transform`; the task
  window uses `translate`). The task window only fades, because the phone e2e measures it right
  after it opens. `prefers-reduced-motion` cuts every animation and transition to 1 ms.
- **Icons**: one set, `components/Icon.svelte` (`IconName` type; 24 px grid, `stroke` prop, `fill`
  for solid stars). `components/task/Icon.svelte` is a wrapper that defaults to 16 px. Add a path
  there rather than an emoji or text glyph (the `↻` on Make recurring is the one kept, since tests
  name that button by it).
- **Tags look the same everywhere**: `TagPill.svelte` (the tag's own colour, else a stable palette
  pick from `lib/cards.ts tagColor`) is used by the card, list row, table, task window tag editor,
  My Work and the tag manager. Don't hand-roll `.chip` tags.
- **Every layout has the same task actions**: board cards, list rows and table rows all open the
  task window on click and the same `CardMenu` on ⋯ / right-click. In the table, the name opens the
  task; the pencil renames in place.
- **Scoped class names must not reuse global ones**: Svelte scopes a component's `<style>`, but
  app.css rules on `.row`, `.card`, `.chip`, `.menu`, `.banner` still reach any element with that
  class. `.row` is the List layout's row grid, so an inner element given that class renders as a
  tall box; inner wrappers use prefixed names (`ab-row`, `ho-row`, `form-row`).
  `lib/classnames.test.ts` fails if a component's `<style>` defines `.row` or `.card` again.
- **One class name, one feature, across the global sheets**: `main.ts` imports the global CSS
  files in a fixed order, so a later file that styles a class on its own restyles every element
  with it. Doc-page names carry a prefix (`doc-sub-icon`, `doc-picker-list`, and `md-table-wrap`
  round a rendered Markdown table; `table-wrap` is the Table layout's scroller).
  `lib/classnames.test.ts` fails when two global sheets style the same class, unless the class
  is in its `LAYERED` list with the reason. A selector lives in one sheet.
- **Escape**: `Dropdown` and `Menu` listen in the capture phase and stop the event, so Escape closes
  the open panel only; App's window handler then closes the palette, sheet, tag manager, task window
  or selection, one per press.
- **Shared pieces**: `Skeleton.svelte` (first load), `EmptyState.svelte` (icon, title, hint,
  optional action buttons as children). ScopePage shows "No tasks match" when a search or filter
  hides everything, and "Nothing here yet" for empty list and table layouts (the board keeps its
  own empty columns). `PageLoad.svelte` stands in for an on-demand page while it loads (see
  `docs/board.md`).
- **Build revision** (`src/slate/build.py`, `lib/build.svelte.ts`): `/healthz` carries `rev` (the
  commit count of the checked-out branch, `git rev-list --count HEAD`, so it only grows) and
  `commit` (short sha). The Docker build writes `src/slate/build.json` in its own small `rev` stage
  (`scripts/build_metadata.py`), from the repo's `.git` (so `.git` must stay out of
  `.dockerignore`; a build from a shallow clone needs `fetch-depth: 0` or the build args, since a
  shallow clone's count is wrong) or from the `SLATE_REV`/`SLATE_COMMIT` build args when the build context has no usable
  `.git` (a worktree, an archive); a dev checkout asks `git` once at startup, and a shallow clone
  or no git says `dev`. `SLATE_REV`/`SLATE_COMMIT` in the environment override everything (not
  Settings fields: nobody sets them by hand, so they stay out of `env.example`). The board reads
  them from the `/healthz` body that `freshness.ts` fetches (under the dev server too, where there
  is no bundle to compare) and shows `r<rev>` by the Slate name in the sidebar (`.brand-rev`,
  commit in the tooltip). The rev, not `pyproject`'s `version`, is what identifies a build. Tests:
  `tests/test_build.py`, e2e `build.spec.ts`.
- **Shell**: the nav runs the full height on the left (`grid-row: 2 / 4`); the favorites bar sits
  over the main column (`grid-column: 2`, full width on phones). The nav has a brand header with the
  live/reconnecting dot, sections as spaces with a coloured initial by audience
  (`.space-mark.aud-*`), and a footer with the person's avatar and icon buttons named Tags, Archived
  and Sign out.

## Phone and installed app (PWA)

Slate installs to a phone's home screen as a standalone app. Everything is static and lives in
`web/`; FastAPI serves it from `SLATE_STATIC_DIR` like the rest of the board (Python's `mimetypes`
already maps `.webmanifest` to `application/manifest+json`).

- `web/public/manifest.webmanifest` (name, `display: standalone`, theme colours) and
  `web/public/icons/` (`icon.svg` is the source for the tab icon and `icon-192/512.png`: a chalk S
  on a rounded slate tile with transparent corners, so it reads as an app icon on a Windows taskbar;
  `maskable.svg` is the full-bleed copy, art scaled into the 80% safe zone, for `maskable-512.png`
  and `apple-touch-icon.png`, since iOS and Android crop those themselves. The PNGs were rendered
  from the SVGs with Chromium and are committed, so no build step needs an image tool; re-render
  them if you change either SVG).
- `web/public/sw.js`, registered from `main.ts` in production builds only. It caches the shell,
  never data: a navigation to the board (`/`) is network-first with the cached `/` as the offline
  fallback (so a deploy shows on the next open), and only a page that loads the board's entry bundle
  is stored as the shell; `/assets/*` are cache-first (hashed names) and pruned to what the latest
  `index.html` references, icons and the manifest are stale-while-revalidate. `/api/`, `/mcp`,
  `/healthz`, `/health`, `/docs`, `/redoc`, `/openapi.json` and any other page are never
  intercepted, so the live stream, every write and the server's own pages go straight to it. A
  notification tap opens only a URL on the board's own origin (anything else opens the inbox).
  Bump `CACHE` in `sw.js` only if the caching strategy itself changes.
- FastAPI serves the board through `BoardFiles` (`app.py`): every file except `/assets/*` is sent
  with `Cache-Control: no-cache`, so the browser HTTP cache (which the service worker's
  network-first fetch goes through) can never hand back an `index.html` older than the deploy;
  `/assets/*` are `immutable`. Result: every device runs the deployed build on its next load. A
  board left open across a deploy reloads itself (`lib/freshness.ts`): when the live stream comes
  back (a deploy restarts the server) or the tab is shown again, it compares the entry bundle it
  runs with `/healthz` `board` (the bundle the served `index.html` loads, `app.board_bundle()`,
  re-read when the file changes) and reloads if they differ, waiting while an input, textarea or
  editable element has focus. It asks `/healthz`, not `/`, because the service worker answers
  non-navigation requests for `/` from its cache and never intercepts `/healthz`. `index.html` is in
  `docgen._NOT_EVENTS` because it looks like an event type. View settings (group, sort, filters,
  card fields) live in saved views on the server. Toolbar edits are drafts until Save or Revert,
  kept on the account as prefs `view_drafts` (per view key, only the settings that differ from the
  saved view, newest 40; saved 400 ms after the last change, flushed with a keepalive fetch on
  `pagehide`; while one is unsent, incoming prefs keep this device's copy so an echo cannot undo a
  newer change). Prefs `last_view` (per scope key, newest 100) is the tab last opened there; with no
  `/v/` in the URL, `board.active` shows it. Code: `lib/lastview.ts`,
  `store.setConfig/#setDraft/rememberView`. E2E specs that touch the toolbar clear both keys in
  `beforeEach`. The rest of how the board looks (theme, folded columns per view) is
  `accounts.prefs_json`, read from `GET /me` (`prefs`) and merged by `PATCH /me/prefs` (null removes
  a key); the board applies `account.prefs_changed` live, so a second device follows at once.
  localStorage holds only the token, the remembered quick-add list, and a first-paint copy of the
  theme. Rule for new board settings: they go in a saved view (shared) or in prefs (per account),
  never in localStorage. A browser that still holds a theme or folds in localStorage hands them to
  the account on its next load (`#adoptLocalPrefs`).
- `index.html`: `viewport-fit=cover`, theme-color per colour scheme, Apple web-app metas and the
  apple-touch-icon. iOS status bar style is `default`, so content never sits under the clock.
- Layout: the `/* --- phone and installed app (PWA) --- */` block at the end of `app.css` holds the
  phone rules shared by every screen: `100dvh` instead of `100vh`, safe-area insets on the topbar,
  nav, main, toasts and list settings (the task window pads itself), 16 px text fields on touch
  screens (iOS zooms into anything smaller), horizontal scroll-snap on `.kanban`, 85vw columns
  (`.column.collapsed` excluded), dropdowns and ⋯ menus as bottom sheets, 40 px tap targets. Its
  selectors are one notch more specific than the base rules, because other stylesheets can load
  after `app.css`. Put new phone tweaks there rather than in component styles.
- **Touch drag** (`lib/touchdrag.ts`, installed once in `main.ts`): browsers start an HTML drag only
  from a mouse, so a long press (350 ms, 8 px slop) on any `draggable="true"` element dispatches the
  same `dragstart`/`dragenter`/`dragover`/`dragleave`/`drop`/`dragend` events (a `DragEvent` with a
  stand-in `dataTransfer`) to whatever is under the finger. Every mouse drop target (board columns,
  list and table groups, favorites bar, nav lists, docs tree) works by
  finger with no per-component code. A clone follows the finger (`.touch-ghost`), the original gets
  `.touch-lifted`, `html.touch-dragging` turns off the board's scroll-snap; `autoScroll` scrolls the
  nearest scroller near an edge (falling back to the boxes the drag started in when the finger is
  past them). Hold and let go without moving dispatches `contextmenu` (the card menu). While a
  finger is down on a draggable, trusted `contextmenu`, `dragstart` (iOS/Android native drag) and
  `selectstart` are cancelled, and the element's `draggable` is set to `false` (class
  `.touch-pressed` keeps selection off) until the finger lifts: iOS 15+ and Android start their own
  drag from a long press on a draggable, which claims the touch and sends `touchcancel`, so
  cancelling its `dragstart` alone comes too late. `haptic()` vibrates on Android and flips a hidden
  `<input switch>` for iOS 18's haptic. E2E: `e2e/touch.spec.ts` drives real touch through CDP
  `Input.dispatchTouchEvent` (note: opening a CDP session resets `context.setOffline`).
- **A button that must not start a drag** inside a draggable card (the copy-ID chip, `.card-copy`):
  make it `draggable="true"` itself with a `dragstart` that calls `preventDefault()` and
  `stopPropagation()` (a mouse drag starts from the nearest draggable, so this cancels it before the
  card sees it), and give it `data-no-drag` so `touchdrag.ts` skips it for a finger. Its click calls
  `stopPropagation()` so the card does not open. `lib/clipboard.ts` `copyText()` falls back to
  `execCommand` where `navigator.clipboard` is missing (plain-http LAN pages).
- **Manual order** (`lib/slots.ts`): when a view's first sort key is manual ascending
  (`views.isManual`), a card or row dropped between two others lands there: one PATCH of `position`
  (midpoint of the gap, or a STEP=1024 past an end), or, when there is no integer gap, one renumber
  of the group through `/batch` (`board.reposition`). BoardView and ListLayout track the dragged id
  from the bubbling `dragstart` (dragover can't read drag data) and show `.slot-before` /
  `.slot-end`.
- **Drops on the board** (`BoardView.svelte`): Board columns are only as tall as their cards
  (`align-self: flex-start`), so `.kanban` itself handles `dragover`/`drop` outside any column and
  hands them to the column above the pointer (`columnBelow`); otherwise a card let go below a
  short column would be silently ignored. A group with `drop: null` still accepts the drag (grey
  `.nodrop.over` outline) and the drop flashes `views.refusal(g)` (the group's `refuse` text) unless
  it is the column the card came from. Tag groups drop `{kind:'tag'}` (swap the source column's tag,
  known from `dragFrom` on the board and the `application/x-slate-group` drag data,
  `views.GROUP_TYPE`/`markSourceGroup`, in List and Table) and section groups `{kind:'section'}`
  (move into its only list, else a note). `e2e/drag.spec.ts` drags with real mouse moves and by
  finger. Checklist entries reorder the same way (PATCH per write).
- **Subtask lines** (`SubtaskLinks.svelte`, `web/src/subtasks.css`): with `show_subtasks` on,
  BoardView adds `.linked` (position: relative) and one 1×1 overflow-visible SVG drawn over the
  cards (pointer-events none). It measures `.cards > .card[data-item]` on the next animation frame
  after a scroll (capture listener, columns scroll on their own), a resize (ResizeObserver on the
  board and each linked card) or a DOM change (MutationObserver, ignoring changes inside the SVG so
  redraws do not loop). Same column: an elbow down the 14px subtask indent; other column: a
  horizontal S-curve. Ends scrolled out of their column are pinned to its edge and dashed. Hidden
  while dragging. The "Subtask of …" label is in `Card.svelte` (`.card-parent`), so it shows
  wherever a subtask card renders. Gotcha: board columns are flex items, whose default `min-width:
  auto` is their widest unbreakable content, so a nowrap line (the label) would stretch its
  column. `.cu-board .column` and the subtask card set `min-width: 0` so the label ellipsizes
  instead; `subtask-links.spec.ts` checks every column keeps the same width.
- **Swipe rows** (`lib/swipe.ts`, `use:swipe`): My Work rows and List-layout rows; right =
  `toggleComplete`, left = `board.snooze` (tomorrow, with Undo). Board cards don't swipe: the phone
  board scrolls sideways. The strip under the row is a `.swipe-under` element inserted before the
  row while swiping.
- **Keyboard** (`lib/viewport.ts`): `--kb` on `<html>` is the on-screen keyboard height from
  `visualViewport` (0 under 80 px), `html.keyboard-open` while it is up; `.shell` and the task
  window size to `100dvh - var(--kb)`, so composers stay above the keys.
- **Pull to refresh** (`PullToRefresh.svelte`): a vertical pull at scrollTop 0 inside `.main` (not
  in the task window, sheets or fields) calls `board.refresh()` (`start()` again behind the stream)
  and fires `slate:refresh` on window.
- **Offline**: `api()` turns a failed fetch into `ApiError(0, …)` with a plain message (offline vs
  weak connection, read vs write); `board.online` follows the browser's events and App shows "You're
  offline" instead of "Reconnecting". Writes are not queued while offline (they fail with that
  message).
- **Sheets**: under 760 px dropdowns and ⋯ menus are bottom sheets that dim the page (a 100vmax
  box-shadow) and slide up.
- Service workers (and so the installed app) require a real HTTPS certificate: serve Slate at an
  address such as `https://slate.example.com` through a TLS proxy (Tailscale Serve, Caddy, nginx).
  A private-network-only address means the phone must be on that network. On iOS the
  installed app has its own storage, separate from Safari, so it asks for the token once more.
- **Share target:** `manifest.webmanifest` declares `share_target`
  (`action: "/"`, GET, params `title`, `text`, `url`), so the installed app appears in the phone's
  share sheet and is launched at `/?title=…&text=…&url=…`; an in-app link can use `#/share?…` the
  same way. `lib/share.ts` is pure: `shareParamsFrom(search, hash)` finds the params, `shareDraft`
  turns them into `{line, body}` (title from `title`, else the first line of `text`; the URL from
  `url`, else the first http(s) link in the text, goes first in the body with the remaining text
  under it; a link shared on its own becomes `Read host/path`, or `Watch …` for video hosts;
  `#`/`@`/`~`/`!` words in a shared title are backslash-escaped so quick add keeps them literal) and
  `takeShare()` reads the address, strips the params with `history.replaceState` (as the invite
  route does with its secret) and lands on the current list or section, else `#/everything`, whose
  picker defaults to the last-used list. `App.readHash` puts the draft in `lib/share.svelte.ts`;
  `QuickAddBar` takes `line` into the box once (focused) and sends `body` as `body_md` in the one
  `POST /items` (a chip shows it; "drop the note" discards it). The share sheet
  reads the manifest at install time, so an already-installed app must be reinstalled (or updated,
  which Android does on its own after a while) before Slate shows up in the sheet; iOS Safari does
  not support `share_target` at all, so there the route is reachable only as a link. Tests:
  `lib/share.test.ts`, the share flow in `e2e/mobile.spec.ts`.
- Tests: `web/e2e/mobile.spec.ts` (manifest and icons served, worker registers and serves the shell
  offline, worker never answers `/api/`, no sideways scroll at 390 px, 16 px inputs, sheets on
  screen, task window without sideways overflow).
