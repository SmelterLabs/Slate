# Task window, drafts, productivity, search and revert

The item detail window and what sits around it on the board: comments, checklists, files,
watchers, activity; unsent drafts; the palette, natural quick add, My Work, the inbox and undo;
one search; revert from the log.

## Task window (comments, checklists, files, watchers, activity)

The board's item detail is a large centered window (`web/src/components/task/`). The API behind
it:

- **Comments**: `GET /items/{id}/comments`; `POST /comments/{id}/reactions {emoji}` and `DELETE
  /comments/{id}/reactions/{emoji}` (one emoji per account per comment, idempotent; table
  `comment_reactions`). `DELETE /comments/{id}` (`comments.delete_comment`, 204) hard-deletes a
  comment for its author or an owner/admin (403 otherwise) with its reactions, and records one
  `comment.deleted` whose `before` is the comment's snapshot plus `reactions`: the audit log keeps
  the text, the activity line (`history.verb`) shows only whose it was. An open Ask closes with
  the row. `comments.not_about_deleted_comment()` drops every event about a deleted comment from
  the inbox and check-in news. Guests: `access._hidden_comments` also lists deleted comments that
  were by or to a workspace agent (read from the event's `before`, the row being gone), and
  `EventGate._for_guest` drops their live `comment.deleted`. `CommentOut` carries `reactions [{emoji, account_ids}]` and `mentions`
  (handles). A mention is `@handle` of a real account (`comments.MENTION_RE`). `item.commented`'s
  `after` carries `mentions`; an edit that adds a mention records `item.mentioned {comment_id,
  mentions}` for the new ones only.
- **Webhook routing**: `assignee_me` also matches when the subscriber watches the item, or is in
  `mentions` of an `item.commented`/`item.mentioned` event (`deliveries.matches`). There is no
  separate filter key for either.
- **Checklists**: tables `checklists` and `checklist_items` (entries carry `item_id` too, so counts
  need no join). `POST /items/{id}/checklists {name, items[]}`, `PATCH|DELETE /checklists/{id}`,
  `POST /checklists/{id}/items`, `PATCH|DELETE /checklist-items/{id}` (`done`, `title`, `assignee`,
  `position`). Every write returns the whole checklist. Ticking records
  `checklist.entry_checked|unchecked` rather than a timestamp diff. Deleting a checklist removes its
  entries by `ON DELETE CASCADE`; the event's `before` keeps the names.
- **Attachments**: `POST /items/{id}/attachments?filename=` with the raw file as the body (no
  multipart, so no `python-multipart`), or `/attachments/base64` with JSON. Max 25 MB. `GET
  /attachments/{id}` returns it (bearer auth like every read; `?download=true` forces a download;
  `Content-Security-Policy: sandbox` so an uploaded HTML or SVG cannot run as Slate). **Bytes live
  in `attachment_blobs` inside the SQLite file**, not on disk: the nightly online backup then
  covers them with nothing extra in `deploy/server/`. `attachments.path` is `db:<id>`.
- **Subtasks nest four deep (migration `a4c8e2f6b9d3`):** `items.MAX_DEPTH = 4` (a top task is
  depth 0). The `items_depth_insert|update` triggers hold the rule with nested scalar subqueries
  (SQLite triggers take no CTE): insert refused when the parent is four down; update refused on a
  cycle, or when depth + the subtree's height would pass four. `models.TRIGGERS` and the newest
  trigger-writing migration must match verbatim (`tests/test_migrations.py` reads
  `*_nested_subtasks.py`, not the initial schema). Service: `depth_of`, `ancestors_of`,
  `descendants_of`, `height_of`; `create_item` and `split` check depth; a bulk move carries
  `descendants_of` (every level); `trash._children` cascades through levels. `item_detail.subtasks`
  stays one level (each with `subtask_count`): the board drills down by opening a subtask. The
  ClickUp importer keeps nesting to four and flattens only below. Board: `TaskModal` shows the crumb
  trail of every ancestor, the Subtask adder and block while `ancestors.length < 4`;
  `Subtasks.svelte` rows show a nested count. Test:
  `test_api_items.py::test_subtasks_nest_four_deep` (service refusal and the trigger through plain
  SQL), `test_move_subtasks.py`, `test_trash.py`.
- **MCP merged schemas:** `input_schema` merges every action's params into one `params` object by
  name. A name with several shapes folds into one enum when every shape is a string enum, else
  `anyOf` (`_merged_prop`). If the first action's shape won instead, `slate_item`'s `kind` would
  be the item kind (`task|doc`) and a client that validates against `tools/list` could never send
  `link`'s `kind: blocks`;
  `tests/test_mcp.py::test_a_param_shared_by_actions_with_different_values_accepts_all_of_them`.
- **Media viewer** (`web/src/components/task/MediaViewer.svelte`, state in `task.viewing`): clicking
  an image, video or PDF attachment opens it full-screen inside the app (PDFs in an `<iframe>` on
  the blob URL, so the browser's own PDF viewer draws it; this needs `frame-src blob:` in
  `BOARD_CSP`, as does the Docs & Files PDF view);
  `task.media()`/`task.step()` walk the task's media, Escape is handled first in `App.onKey` so it
  closes the viewer and not the task window, and the object URL is revoked on close (`task.reset()`
  closes it with the task). Why not a new tab: the blob has to be fetched with the token first,
  and a click on a `target=_blank` link after an `await` is not a user gesture, so browsers
  block the tab and nothing happens. Other files download, which is not blocked.
  **Documents:** Word (.docx), Excel (.xlsx), CSV and text attachments open in the same
  viewer, drawn by the board from the fetched blob: `lib/office.ts` (`officeKind`,
  `sanitizeDocHtml`, `renderDocx` through **mammoth**, `readWorkbook` and `formatCell` through
  **exceljs**, both `import()`ed on first use so the main bundle does not carry them; exceljs is a
  930 kB chunk) and `components/files/DocumentPreview.svelte` (a Word file as a US-Letter page,
  816px with an inch of padding; a workbook as a grid with column letters, row numbers, sticky
  headers, merges as spans, bold, right-aligned numbers, a tab per sheet, `SHEET_ROWS` 2000 then a
  note; CSV and text as in FileView). The HTML mammoth produces goes through an allowlist sanitizer
  (known tags, `http(s)`/`mailto` hrefs, `data:image/` sources only, no attributes otherwise) before
  `{@html}`; the workbook is rendered from data, never HTML. `task.viewing` keeps the blob beside
  the URL; `isMedia` includes `isDocumentKind(viewerKind(...))`. `viewerKind` knows `docx | xlsx`
  (by content type, then by extension, since some uploads arrive as octet-stream), and `FileView`
  shows those through the same component (`docs.fileBlob`). Rejected: a server-side converter
  (LibreOffice, rule 8) and SheetJS (its npm build is old with open advisories; its own CDN is a
  fragile build-time dependency). Limits: `OFFICE_LIMIT` 25 MB, above which a download card. Not
  read: .doc/.xls, PowerPoint. E2E `web/e2e/documents.spec.ts` with fixtures under
  `web/e2e/fixtures/` (`quote.xlsx` written by exceljs, `scope.docx` a minimal hand-built package).
  npm audit on exceljs flags transitive node-only packages (archiver's minimatch, uuid, deepmerge)
  that the shipped browser bundle does not execute.
- **Paste or drop on the comment box** (`Composer` `paste` / `drop`): files on the clipboard (a
  screenshot has no text, so text pastes as before) upload as the task's attachments (`task.upload`,
  which names a clipboard `image.png` with `pastedName`) and wait above the box as thumbnails
  (`pending`; the × removes the attachment again). Send appends one `📎 <filename>` line per file
  (`activity.fileLine`), so a comment can be a file alone and the stored text still says which
  file is meant. `ActivityPanel` shows those lines as the files themselves
  (`activity.commentFiles` matches each line to the newest attachment of that name; a line whose
  file is gone stays text): images inline, other files as chips, opening in `MediaViewer`. The
  brief (`brief.files_note`) lists the task's files with where to read them; the Hermes adapter
  adds the same list to a later turn (`card_files`), which carries only the reply. E2E:
  `chat-files.spec.ts` "paste a screenshot into a card comment".
- **Pasted media** (`attachments.split_pasted` / `attach_pasted`): a base64 `data:` URI in a comment
  (add or edit) or an item body (create, update, batch update) is cut out of the text, saved as an
  attachment in the same change, and replaced by `📎 <name> (attached)`. The name comes from the
  markdown alt text (`image-<n>` without one) plus the type's extension. Why: card markdown draws no
  images, so an agent pasting an image as `![](data:...)` would leave the reader a screen of
  base64; the Slate skill says to `attach`, and this catches the agents that don't. Text that does
  not decode is left as written. Docs pages are not touched (they have their own files and render
  images).
- **Watchers**: table `item_watchers`; `GET|PUT /items/{id}/watchers`, `POST
  /items/{id}/watch|unwatch` (the caller, or body `{"account"}` for anyone; MCP `watch`/`unwatch`
  take `account` too). Events `item.watched|unwatched` (`after`/`before` = account id).
- **Watchers, ClickUp-style**: auto-watch in `watchers.auto_watch(cx, item_id, ids,
  why)`, called inside the change by `items._insert` (creator), `items._set_assignees` (added
  assignees) and `comments.add_comment` (author). Account pref `auto_watch` `{created, assigned,
  commented}`; default on for people, off for agents (an agent's check-in would otherwise fill with
  work it filed); never the system account. An auto-watch is `item.watched` with `field` "auto" and
  is not news (the assignment already is); one added by hand is inbox reason `watching` for the
  added account. Unassigning does not unwatch. A watcher's inbox gets reason `watching` for activity
  beyond comment/status/update/archived/moved: any `item.updated` field except
  `position`/`completed_at`/`due_date_only`, `inbox.WATCH_TYPES` (assignees, checklists, files,
  links, subtasks via `item.created` with a watched `parent_id`, an agent's report when its state
  changes). Description saves collapse to the newest per author and item in the inbox list; push
  sends one per author and item until a 10-minute pause (`push.EDIT_QUIET`). When the same actor's
  change also made the item wait on the account (an agent's blocked reply: comment + report +
  `waiting_on`), the follow/watching entries within 2 s are dropped and only `waiting` is told
  (`inbox.waits`/`waiting_covers`, inbox and push). An agent's report that it started
  (`item.reported` with state `working`) is an inbox entry but is never pushed or emailed
  (`push.started_work`, checked in `push.wanted`): it follows the watcher's own message by a
  second, and the report that ends the run is the one that interrupts. Auto-watch events are not
  check-in news. Push
  reason `watching` (default on) pushes everything on a watched item, including the follow reasons,
  through `push.wants()`; push bodies come from `push.watch_verb()`. `inbox.Follows` carries
  `watched`. UI: `task/WatchersPicker.svelte` (the eye in the Activity header) and the "Watch tasks
  automatically" block in Notifications.
- **Activity**: `GET /items/{id}/activity?include=all|comments|events&limit=` merges comments (with
  reactions) and events in time order; `item.commented` and `comment.edited|reacted|unreacted` are
  folded into the comment entries (`service/activity.py`).
- `ItemOut` carries `checklist_done`, `checklist_total`, `attachment_count` (batch-counted in
  `items.views`); `ItemDetailOut` adds `checklists`, `attachments`, `watchers`.
- `items.item_detail` imports `attachments`, `checklists`, `comments`, `watchers` inside the
  function: those modules import `items`, so a top-level import would be a cycle.

Board side:

- `TaskModal.svelte` is the shell (header, left column, `ActivityPanel`). Field editors are the
  board's `DatePicker`, `AssigneePicker`, `TagEditor` and `RecurrenceEditor`, plus `StatusPicker`
  and `PriorityPicker`. Styles are component-scoped and use the `:root` tokens only, so a theme that
  redefines the tokens restyles the window. Under 760 px it fills the screen (safe-area padding) and
  the activity column stacks under the task.
- `AutoText.svelte` is the autosaving markdown box (description, "Done means"): local draft; save
  1.2 s after the last keystroke, on blur, and on destroy (closing or switching tasks never loses
  typing); remote changes replace the draft only while it is not being edited. Every save is one
  `PATCH`, so one body version; the activity column folds a run of same-field edits by one actor
  within 10 minutes into one line (`lib/activity.ts` `timeline`).
- `lib/task.svelte.ts` holds the window's own state and writes (activity feed, reactions,
  checklists, uploads, image thumbnails as object URLs fetched with the token, watchers, `link()`).
  Writes call `board.refreshDetail()`; the activity reloads whenever the detail's last event
  changes.
- The hash carries `?task=<id>` while a task is open (`history.replaceState`, so no `hashchange`);
  `App.readHash` opens it on load or when a link changes the hash. `parseRoute` ignores the query.
- The "via API" chip shows only for agents: the board itself writes through the API, so it would
  otherwise sit on every line of a person's own history.
- Reducer: `checklist.*` and `item.attachment_*` refetch the item (its counts);
  `item.mentioned|watched|unwatched` change nothing on cards. The event names are in
  `EVENT_TYPES`.
- **Agent cards put the thread first.** `TaskModal` splits its left
  column into `.tw-head` (banners, agent badge, status, thread links, title) and `.tw-rest` (fields,
  panels, description, subtasks...). `agentOn` (agent flag, agent threads, or an agent assignee)
  folds the field grid and the panels into a **Details** button whose summary is `refSummary` once
  any reference field is set (`refsOpen` is decided once per opened task, never saved: it is not a
  view setting). On the phone (`max-width: 760px`) an agent card's `.tw-body.agent-first` is a flex
  column, `.main` is `display: contents`, and `order` puts head, then `ActivityPanel`, then rest.
  Run now and Hand off are not under the status: `TaskModal` passes them to `ActivityPanel` as
  the `actions` snippet, rendered above the `Composer` (whose Ask picker is already there), so the
  three ways the next turn starts sit together. Cards with no agent keep the open grid.
- **Size and place:** the window is resizable and dockable, and remembers
  both per device. Pure parts in `lib/taskwindow.ts` (`WindowState {dock, width, height,
  activity}`, clamps, `windowWidth`, `stacks`, `readState`/`writeState` on localStorage
  `slate.taskWindow`), live state in `lib/taskwindow.svelte.ts` (`taskWindow`, the chat panel's
  pattern). `TaskModal` sets `--tw-w`, `--tw-h`, `--tw-activity` on `.tw` and the CSS reads them.
  The **corner grip** (`.tw-corner-grip`, centered only) resizes the window about its center: the
  width grows by twice the pointer's distance from the center so the window never leaves the
  middle; clamped to 720×480 up to the screen less 48px; double-click or Home resets to
  1280×900. The **activity grip** (`.tw-activity-grip`, on the column's left edge) drags the
  column from 300px up to the window less the main column's 360px keep; a dock narrower than
  the saved width clamps it on the fly (`clampActivity(activity, shown)`), the saved number is
  untouched. The **dock button** in the top actions (a floated `Dropdown`, `menuitemradio`
  entries Centered / Right third / Right quarter, icons `dockCenter`/`dockRight`) sets
  `data-dock`; docked, `.tw.docked` sits at `right: 0`, full height, a third or a quarter of the
  viewport wide, **no scrim and `aria-modal=false`**, so the board beside it stays usable and
  clicking another card replaces the window's content. A window under 760px wide, which is any
  dock on a screen under 2280px (third) or 3040px (quarter), gets `.stacked`: the phone layout's
  body rules (activity under the task, agent-first order) live in `.tw.stacked …` selectors so
  both share them; the `@media (max-width: 760px)` block only makes the window the whole screen.
  The phone (`innerWidth <= 760`) never shows the grips or the dock button. E2E
  `web/e2e/task-window-size.spec.ts`; unit `lib/taskwindow.test.ts`.

## Drafts (unsent text)

`web/src/lib/drafts.ts` (pure storage, tested in `drafts.test.ts`) and `drafts.svelte.ts`
(reactive). Every box with a send step keeps its text in `localStorage` under `slate.draft.<key>` as
`{t, at}`; keys are built only by `draftKey.*` so the card marker (`itemHasDraft`, the **Draft** tag
on `Card.svelte`) can find a card's drafts. `persisted(() => key)` is a drop-in for `let text =
$state('')`: the setter writes through on every keystroke, '' removes the draft, and a changed key
(the task window switched cards) loads that key's draft. Edits to existing comments and chat
messages save only when the text differs from the stored body, and reopen in edit mode. Cancel
clears; leaving keeps. While the page is leaving (`beforeunload`/`pagehide`, undone by `pageshow` or
any input, keydown or pointerdown) `saveDraft` writes nothing: a send cut off by a reload would
otherwise run the composer's "failed, put the text back" path and save the message just sent as a
draft (`drafts.spec.ts` "still sending"). Drafts expire after 30 days (`DRAFT_TTL_MS`,
pruned at start) and are capped at 20k chars. Never use it for a token, password or secret field.
Boxes that already autosave to the server (description `AutoText`, doc `DocBody`) are not drafted;
the task title is saved on close instead (`TaskModal` `titleFor`). Not drafted on purpose: settings
and connection forms, search and filter boxes, small popover forms (reminder note, doc "task from
selection").

## Productivity: palette, natural quick add, My Work, inbox, undo

- **Quick add (`web/src/lib/quickadd.ts`)** reads natural language: bare dates (`tomorrow`,
  `friday`, `next fri`, `in 3 days`, `jan 5`, `11/2`, `eow`), times (`4pm`, `at 4` = 16:00, `noon`),
  `start:`, `~list`, `!1`..`!4`, and repeats (`daily`, `every monday and thursday`, `every other
  week`, `every 15th`). `due` is `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` (workspace wall time, which
  the date rule accepts). Three-letter weekdays only count after
  `due`/`by`/`on`/`next`/`this` ("Wash the car sat" stays a title). A leading `\` keeps a word
  literal. A line with a repeat makes `POST /recurrences` (dtstart = first occurrence) instead of an
  item. `matchList()` resolves `~hint` (exact name/slug, then prefix, then substring; archived lists
  skipped).
- **Inbox (`service/inbox.py`, `/inbox`, MCP `slate_inbox`)** is derived from `events`, not copied:
  `reason_for()` picks mention / assigned / waiting / due (assigned items only) / comment / status /
  update / archived / moved, for items the account is assigned to or watches, plus `watching` (see
  Watchers above); own changes never count. Creating an item follows it only through auto-watch.
  30-day window. Read state is `inbox_state` (read-through and cleared-through cursors, time-ordered
  event ids) plus `inbox_marks` (one event read or cleared). Marking writes `inbox.read` /
  `inbox.cleared` with `subject_id` = the account, so other devices refetch. `completed_at` updates
  are not news (they always ride with `item.status_changed`).
- **Board pages**: `#/home` (My Work, `components/work/HomePage.svelte`, grouping in
  `lib/mywork.ts`) and `#/inbox` (`InboxPage.svelte`, state in `lib/inbox.svelte.ts`).
  `route.pageOf()` names them; App renders them instead of `ScopePage`. The inbox listens through
  `board.onEvent()` (a listener hook in `#apply`) and refetches, debounced; opening a task marks its
  entries read.
- **One word per mechanism (`lib/words.ts`).** A person has to answer:
  `waitingOn(name)` = "Waiting on Alex" (first name; the agent badge, the card pill, Needs you, the
  inbox icon). Another task has to finish: "Depends on" (card pill with the first blocker's title,
  `blockedTitle`, the task banner, `DependenciesPanel` group and picker, My Work's card, history
  `made this depend on`, filter label). An agent stopped and named nobody: "Agent blocked"
  (`STATE_LABEL.blocked`). An agent says done: "Ready for review". `conversation.badgeLabel(flag,
  item, nameOf, labels)` names whoever the card waits on, the same words for every viewer. The
  person field keeps the label "Waiting on"; the card hides its Waiting on pill when the agent
  badge already says it. `LEAVES` is the one
  sentence each way off the board shows when pressed (toast after the label via `#remember(...,
  say)`, tooltips, the card menu's Put away confirm, the bulk bar's confirm). The task verb is **Put
  away** (`PUT_AWAY`), so it never shares a name with the Archive page; Unarchive is **Bring back**;
  the Archive page's tab is "Put away". API, MCP, events and automations keep `archive`. Bulk
  "Delete" is "Trash".
- **The number means Needs you.** `windowBadge(board.needsMe.length)` sets the title
  `(n) Slate` and `setAppBadge`, the same count as the pill and the count on **My Work** in the
  sidebar (`data-needs-nav`). The app icon does not count unread chat: the icon must match the
  pill, the count that means stop and act; Chat keeps its own badge and the flashing title.
  Everything's sidebar badge (`board.todayCount`) is overdue plus due today only (waiting on me
  is Needs you's). My Work has no "Asked of you" or "Waiting on me" side cards (they are Needs
  you groups) and has a Needs you tile; its dependency card is "Depends on". The Inbox's empty
  state does not claim nothing needs you while Needs you lists something.
- **Needs you** (`lib/attention.ts`, client side only): the open cards that wait on the viewer, one
  reason each, most urgent first: `waiting` (`waiting_on_account_id` is me), `blocked` (agent flag
  blocked with nobody named, on my card), `asked` (an open Ask to me), `review` (agent flag review
  on my card). "My card" = I filed it, I'm assigned, or a non-person filed it; another person's card
  is theirs. Deliberately left out: working flags, dependency blocks, overdue. `board.needsMe`
  caches it per `rev`. Shown by `NeedsBadge.svelte` (desktop: a pill docked bottom-right on every
  page except My Work and Inbox, opening the list; phone: a pill in the top bar linking to My Work)
  and `work/NeedsPanel.svelte` (grouped by reason at the top of My Work and the Inbox, with Mark
  reviewed). Each sidebar list shows a `.needs-mark` (count, colored by its most urgent reason) from
  `board.needsIn(listId)` (`attention.byList`, cached with `needsMe`). Cards on their lists keep
  their own agent badge. E2E `e2e/needs-you.spec.ts`.
- **Needs you on the card itself** (`Card.svelte`): each card runs `needOf` (the same
  rule as the pill), so a card in the viewer's Needs you gets `.needs-me` + `data-need`, a ring in
  that reason's tone (red waiting/blocked, accent asked, orange review). It is a ring, not the
  border, because the left edge is the priority stripe. A wait on the viewer with no agent badge
  saying it shows a red "Waiting on Alex" chip under the title (`data-waits-on-me`) whatever the
  card's field settings, replacing the gray Waiting-on pill in the footer. Why: once an agent's
  flag is cleared, a small gray footer pill would be the card's only sign while the pill and the
  sidebar are red.
- **Waiting on you, in the window**: a card that waits on the viewer opens with a red
  banner at the top of the main column (`data-banner="waiting"`): who set the wait, the question in
  a sentence or two, **Answer** and **Not waiting on me**. `conversation.waitingQuestion(item,
  comments, events, me)` finds it: the latest `waiting_on_account_id` event naming me, its actor's
  newest comment up to a minute after it (a blocked report writes both together), else the newest
  comment by anyone else; `questionIn(md)` keeps its last one or two sentences ending in "?" (table
  rows skipped), else the first line. Answer calls `task.reveal(commentId)`: ActivityPanel opens that
  comment if collapsed, scrolls it into view, outlines it (`.comment.lit`) and focuses the reply
  box. Why: the question is often only the last sentence of a long agent comment, and once the
  agent flag is cleared ("stopped working") the badge is gone too, so without the banner
  "Answer →" in the pill would open a card that shows nothing to answer.
- **A reply after the flag is cleared** (`comments.reply_target`): a comment by the person the card
  waits on is the agent's next turn while the agent's blocked flag names them, and also when there
  is no agent flag at all and the last `waiting_on_account_id` change to that person was made by an
  enabled agent. Otherwise clearing the flag would leave the wait in place and the answer would
  reach nobody.
- **Command palette** (`components/work/CommandPalette.svelte`, engine `lib/palette.ts`): fuzzy rank
  over commands, sections, lists, views, chat channels, tags and every loaded task (every board page
  has a "Go to" command; a new page adds one); `>` commands only, `#` tags only; last row "Create"
  parses the text as quick add. Recents are account prefs (`recent_tasks`), so every device offers
  the same ones; the theme command uses `board.setTheme`.
- **Undo** lives in the store: `update`, `bulk`, `archive`, `moveItem` and `quickAdd` push a revert
  (`lib/undo.ts` builds the PATCH from the pre-image). Optimistic paths stash the pre-image
  (`#stash`) before editing locally, so the revert sees old values. Reverts go through `/items/bulk`
  and are not themselves recorded. Ctrl/Cmd+Z pops; complete, put away, trash, moves and multi-item
  bulk also show a toast with Undo, and the ways off the board add their `LEAVES` sentence to it.
  Quick add's undo is the 10-minute delete.
- **Pickers** (assignees, mentions, hand-off, bulk assign, waiting on) take `board.pickable`:
  enabled accounts minus `slate`, the account the scheduler acts as. `slate` is Slate's own account,
  not an agent: `update_account` refuses to rename, disable or re-wake it (422), and
  `due.system_actor` puts back its name `Slate` and re-enables it should either have been changed
  (every scheduled event carries that name). The Agents page leaves `slate` out of its cards too
  (its events still show in the feed).
- **Keys** (App.svelte): Ctrl/Cmd+K palette, Ctrl/Cmd+Z undo, `j`/`k` move a cursor over `main
  [data-item]` in DOM order (`.kb-focus`), Enter opens, `e` completes, `g h`/`g i`/`g e` jump. The
  styles are in `web/src/work.css` (tokens only).

## One search

`GET /search?q=&limit=` (read scope; `api/search.py`, `service/search.py`; MCP `slate_search`, one
`search` action, read-only) answers with typed hits: `kind` task | doc | message, `id`, `title`
(task title, doc title, channel name), `snippet` (plain text around the first match,
`docs.snippet`), `where` (the task's list; the doc's list, drawer, section or `Workspace`; the
message's channel as `chat.place` names it, `#Todo`), `at`, plus `list_id` on task and doc hits and
`channel`/`thread_id` on message hits so the board can open the channel at that thread.

- **Three queries, no search index.** Tasks reuse `items.query_items(q=)` (title and body), docs
  reuse `docs.list_docs(q=)` + `summaries` (title, page text, the text inside files), chat is the
  one query of its own: a `LIKE` over `messages.body_md` (deleted messages skipped). All three go
  through the session's read criteria (`access.read_criteria`), so a guest only finds what its
  shares reach;
  `tests/test_search.py` checks it.
- **Every group matches the same way** (`db.contains`): `ulower(column) LIKE pattern` with a backslash escape.
  `ulower` is Python's `str.lower` registered on each connection (`db._configure_sqlite`), because
  SQLite's `lower()` changes only A-Z; `%` and `_` typed by the caller are literal
  (`db.like_pattern`).
- **Order and cap.** Tasks first, then docs, then messages (newest first). `limit` (default 30, max
  200) is shared: each group gets `limit // 3`, and what a short group leaves goes to the next
  groups in order (`search._share`), so the answer never exceeds `limit` and a flood of chat hits
  cannot hide the tasks.
- **Board.** `lib/search.ts` (`shouldSearch`: two or more characters, not a `>`/`#`/`@` search;
  `searchAll`; `groupHits`, which drops tasks and docs the palette's local index already lists;
  `hitHref`). The command palette asks the server 150 ms after typing stops, drops a stale answer
  (the query moved on), and renders the groups Tasks / Docs / Chat under the local matches, on the
  same keyboard cursor; a task opens in the window, a doc at `#/docs/<id>`, a message at
  `#/chat/<channel>?thread=<id>`. Scrolling the chat to the exact message is not done.

## Revert from the log

`POST /events/{id}/revert` (write; `api/events.py`, `service/revert.py`; MCP `slate_item revert
{event_id}`) undoes one recorded item change by issuing its inverse as the caller. Nothing in the
log is edited (rule 3 stays append-only): the inverse goes through `items.update_item`, or
`items.bulk_update` for a move, as the caller with the usual access checks, and writes its own
event whose `session_ref` is `revert:<event id>`. That event is the record; there is no
`item.reverted` type (it would be a second copy of the same fact). It returns the item view, like
a PATCH.

- **What reverts.** `item.updated` on a plain field (`revert.FIELDS`: title, priority, start/due
  dates, `due_date_only`, waiting on, acceptance, estimate, kind, external ref, position, the
  description, the tags), `item.status_changed` (`before` is a status id; "no status before" is
  refused), `item.assigned` (`before` is handles) and `item.moved` (`before` is the list id; a
  subtask that cannot move alone fails as the move would). Anything else is 422 "cannot be
  reverted".
- **Changed since.** The field's current value must still equal the event's `after` (compared
  through `core.jsonable`, so dates and enums match the stored JSON); otherwise 409 with "<field>
  has changed since: it is now X, not Y" and nothing is written. Reverting a revert is the original
  change again, and reverting the same row twice is a changed-since.
  The check and the inverse run in one `apply_change()`, so under the write lock: a change that
  lands meanwhile waits and is never overwritten. That is also why a revert is refused (422)
  inside an atomic batch, whose change belongs to another actor; `atomic: false` runs it.
- **Date-only due dates.** A `due_at` change records its `due_date_only` sibling right after it in
  the same change; `_sibling_flag` finds that event (same item and actor, next id, within 5 s) and
  passes its `before` so a date-only date comes back date-only. A lone `due_date_only` revert
  re-sends the current `due_at` with the old flag, since `update_item` only reads the flag with a
  date.
- **Access.** The event is read through the caller's filter (unseen → 404), then `update_item`
  enforces edit on the list (a view-only guest gets 403). `tests/test_revert.py`; the walk test
  lists `revert_event` as a mutation whose scenario is `None` because its event carries
  `revert:<id>` instead of the walk's `session_ref`.
- **Board.** `lib/revert.ts` (`canRevert`: supported type or field, not the viewer's own change, not
  itself a revert; `revertQuestion`; `revertEvent`). `ActivityPanel` (History tab) and `AuditPage`
  show **Revert** on such rows, ask with `window.confirm`, call the route, toast the outcome, and
  after a 409 leave the row disabled as "Changed since" with the server's sentence as its title. The
  detail and the audit page refresh from the stream as for any event.
