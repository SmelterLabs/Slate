# Docs & Files: pages, file docs, drawers, threads, compare, facts

Pages edited in place, file docs with revisions, drawers, discussion and review threads,
compare by type, tags and the hub, facts blocks, and the collaboration-correctness and
resource-safety rules around them.

## Docs & Files

ClickUp-style Docs: pages next to the work, nested up to 8 deep, in the Workspace, a section or a
list. The same tree also holds **file docs**.

- **File docs** (migration `f4b8d2a6c1e9`): `docs.kind` is `page` (default) or `file`. A file doc's
  uploads are `doc_files` rows with a `revision_no` (1 upward, unique per doc), a `note` (what
  changed; **required for everyone**), `superseded_at` (stamped on the previous
  revision when the next arrives) and `restored_from`. Pages' inline images are `doc_files` rows
  with `revision_no` NULL. `body_md` on a file doc is the description (Notes), versioned
  like a page. Service: `add_revision` (25 MB, `MAX_REVISION_BYTES`; event `doc.revision_added`,
  metadata only), `restore_revision` (a new revision with the old bytes, never a rewrite),
  `revisions_of`, `get_revision`, `current_revisions` (one query for the index),
  `promote_attachment` (a task attachment becomes a file doc's revision 1, the attachment is
  removed, the task gets a comment `[[doc:ID]]`; three changes in sequence, each its own event). A
  revision is never removed (`remove_file` refuses one); archive the doc. `add_revision` reads
  the file before the write lock and picks the number under it, so two uploads at once get
  consecutive numbers. `duplicate_doc` reads only the docs it copies and copies each current
  revision (bytes, text and tables, never re-extracted) as the copy's revision 1. `GET /docs?q=` also matches a current revision's
  filename. Summaries carry `kind` and `file` (the current revision); `GET /docs/{id}` adds
  `revisions`, newest first. REST: `POST /docs {kind: "file"}`, `GET|POST /docs/{id}/revisions` (raw
  body with `?filename=&note=`), `POST /docs/{id}/revisions/base64`, `POST
  /docs/{id}/revisions/{n}/restore`, `POST /attachments/{id}/promote`. MCP `slate_doc`
  `upload|revisions|restore_revision`, `slate_item` `promote_attachment`. Export rows carry `file`;
  the health report's `db.blob_bytes` sums attachment and doc-file sizes (the Health page's "Files
  stored" tile).
- **Discussion and review threads** (migration `a7c3e9f1b5d2`, table `doc_comments`,
  `service/doc_threads.py`, `api/doc_comments.py`): one table for two surfaces. A **review** root is
  anchored (`anchor_start`/`anchor_end`/`anchor_quote` on a page; `anchor_page`,
  `anchor_x`/`anchor_y` in 0–1 on a file), can be resolved and reopened, and on a page may carry
  `suggestion_md` (Accept = `update_doc` with `base_revision`, checking the immutable page-save
  identity and replacing only the exact span or unique quote, then `doc.comment_resolved
  reason=accepted`). A comment without an anchor is **discussion**. Replies carry `parent_id` (one
  level) and inherit the root's surface and anchor. Every comment is stamped with `anchor_revision`
  (the page's `version_no` or the file's current revision) and views carry `outdated`. Why a
  separate table rather than `comments.item_id` nullable: Asks on tasks drive the agent work loop
  (`WAKE_TYPES`, `/work/next`, Today), the check-in and the inbox all assume an item; a doc comment
  would have reached into every one of them. Instead doc mentions and Asks ride on
  **`doc.mentioned`** (the page-mention event, with `comment_id` and `asked`), so inbox,
  check-in, push (`"X asked you in <doc>"`, url `#/docs/<id>?comment=<id>`) and webhooks route them
  with no rules of their own. A push agent mentioned or asked on a page also gets a doc turn
  (`service/docwake.py`, `docs/agents-work-loop.md` "Doc mentions wake an agent"): its runner
  answers with a comment in the thread; no task or item run starts. Events:
  `doc.commented` (after = the view), `doc.comment_edited`, `doc.comment_resolved` (`reason`
  resolved | replied | accepted | reopened), `doc.comment_assigned`, `doc.suggestion_accepted`; all
  are COMMENT-level writes for access (`_COMMENT_MODELS`, `_COMMENT_TYPES`), and `DocComment` rows
  follow the doc's visibility (`with_loader_criteria`). `GET /docs/{id}/activity` returns the
  comments plus the doc's `ACTIVITY_TYPES` events (created, updated, moved, revision_added,
  version_restored, comment_resolved, suggestion_accepted) for the panel to fold in. Summaries carry
  `comment_count` and `open_threads`. REST: `GET|POST /docs/{id}/comments`, `PATCH
  /doc-comments/{id}`, `POST /doc-comments/{id}/resolve|reopen|assign|accept`, `GET
  /docs/{id}/activity`; MCP `slate_doc`
  `comments|comment|edit_comment|resolve_comment|reopen_comment|assign_comment|accept_suggestion|activity`.
  Tests: `tests/test_doc_threads.py`, `web/e2e/doc-threads.spec.ts`.
- **Drawers** (migration `d2f6a8c4e1b7`): `lists.kind` is `tasks` or `drawer`; a drawer is a list
  whose entries are documents (`docs.list_id`), so one place answers "where does this go" and
  nothing is aggregated. `lists.require_tasks` refuses items on create and move;
  `update_list(kind=…)` converts, to a drawer only while the list holds no tasks. `docs.kind` also
  has `folder` (a container: no template, no inline files, no revisions; nests through `parent_id`
  like a subpage). `docs.pinned_revision_no` / `pinned_note` with `pin_revision`
  / `unpin_revision` (`doc.revision_pinned|unpinned`); summaries carry `pinned` and
  `current_revisions` tags each current row with the pinned revision's file id.
  `docs.drawer_listing(list_id, folder)` is `GET /lists/{id}/drawer` (entries in tree order with
  `children_count`, the folder path, counts); `docs.to_drawer(page)` is `POST /docs/{id}/to-drawer`
  (a new drawer list named after a top-level page in a section; the page's subpages then the page
  itself move to its top, each an ordinary `doc.moved`). The hub (`service/doc_hub.py`) is
  `drawers` (each with counts and how many `needs_you` entries are in it), `pages` (top-level,
  outside any drawer), `needs_you`, `counts`; it has no recent list or activity feed. MCP:
  `slate_doc drawer | to_drawer | pin_revision | unpin_revision`, `kind` on `slate_list create |
  update`. Board: `components/docs/DrawerPage.svelte` renders at `#/list/<id>` when the
  list's kind is `drawer` (`ScopePage` switches; `?folder=` and `?doc=` carry the folder and the
  reading state); `lib/docs.ts` `filterEntries`; `DocPage` takes `openDiscussion` and offers **Turn
  into a drawer** on a top-level section page; `FileRevisions` pins; the sidebar's Add has a List /
  Drawer toggle (`Nav` `addKind`, `board.createList(section, name, kind)`), drawers show a folder
  glyph and a document count, Docs & Files sits under More; `ListSettings` has a Kind switch
  (`board.setListKind`); `DocsHub` is drawers + pages + Needs you. Tests: `tests/test_drawers.py`,
  `web/e2e/drawer.spec.ts` (and `hub.spec.ts`, `collaboration.spec.ts`).
- **Hub, filters and tags** (migration `c9e5f3b8d4a2`, table `doc_tags`): docs carry the
  same `tags` tasks do (`docs._set_tags`, one `doc.updated` field `tags` with sorted names
  before/after; `tags` on create and PATCH; `GET /docs?tag=&kind=`; summaries carry `tags`;
  `tags.usage_counts` adds docs, so the Tags page counts them).
  `service/doc_hub.section_documents(session, section_id, me)` serves `GET /sections/{id}/documents`
  and MCP `slate_doc hub` (the shape under Drawers above): `needs_you` is the open roots (Asks
  addressed to `me`; review threads by someone else on a doc `me` created, edited or commented
  on), each with the board URL `/#/docs/<id>?comment=<cid>`. Guests see it only where they see the
  section. `slate backfill-doc-text` (`doc_compare.backfill_text`, as the `slate` system agent,
  one `doc.text_extracted` event per revision) indexes revisions that have no stored text or
  tables, for example after the extraction cap is raised. Board:
  `components/docs/DocsHub.svelte` is the documents block on `ProjectHome` (reloads on `doc.*`
  events; `?upload=1&in=s:<id>` on `#/docs` opens the upload sheet for the section); `lib/docs.ts`
  `fileGroup`, `filterDocs` (keeps ancestors so the tree still reads), `sortTree`, `tagCounts`;
  `DocsPage` has kind and file-group chips, a sort select and tag chips (state in `localStorage`
  `slate.docs.filter`); `DocPage` shows a `TagEditor` under the title (`docs.setTags`). Tests:
  `tests/test_doc_hub.py`, `web/e2e/hub.spec.ts`, Vitest for the helpers.
- **Compare** (migration `b8d4f2a7c3e1`, `service/doc_compare.py`): `doc_files.text` holds
  text extracted once on upload (`extract_text`: text files decoded utf-8-sig/utf-8/cp1252; CSV/TSV
  as they are; XLSX through `openpyxl` read-only, one `## Sheet` block per sheet, cells tab-joined;
  PDF through `pypdf` per page; `MAX_TEXT` 300k chars; never raises, a file that cannot be read
  uploads without text). `GET /docs?q=` matches it and the snippet comes from the current revision's
  text when the body has no hit. `compare(doc, from, to=current)`: CSV/TSV/XLSX → `kind: table`, one
  `SheetDiff` per sheet (rows aligned by index: `changed` cells, `added_rows`, `removed_rows`, both
  tables; a renamed single CSV is still one table); other text kinds → `kind: text`, a unified diff
  of the stored text, `approximate: true` when either side is a PDF; images and the rest → `kind:
  none` (the board shows them side by side with a swipe). `GET
  /docs/{id}/revisions/compare?from=&to=`, MCP `slate_doc compare_revisions`. Revisions carry
  `has_text`. Dependencies: `openpyxl`, `pypdf` (pure Python, in `pyproject.toml`). Board:
  `lib/docs.ts` `sideBySide` (a unified diff as aligned columns; removed/added pairs read as one
  change) and `cellChanges`; `components/docs/RevisionCompare.svelte` (From/To pickers, text
  side-by-side or inline with an approximate note for PDFs, sheet tabs and a painted table, image
  swipe with `clip-path`, download cards) replaces the viewer while comparing (`DocPage` `compare`
  state; FileRevisions' **Compare with current**, the older-revision banner's too); `DocHistory`
  has a **Side by side** tab using the same helper. Tests: `tests/test_doc_compare.py` (builds a
  real XLSX and a one-line PDF), `web/e2e/compare.spec.ts`.
- **Board, threads**: `lib/docs.ts` `threadsOf` (roots + replies by surface), `anchorLabel`,
  `docEventLine` (the activity phrasing), `feedRows` (threads and events in time order, consecutive
  events folded into one row); the store keeps `comments`, `activity`, `pendingAnchor` (a selection
  or a pin starting a thread) and `focusComment` (a pin, a `?comment=` link).
  `components/docs/DocDiscussion.svelte` is the panel: Discussion tab (feed with `<details>`
  activity groups, composer with `@` picker and an Ask select, the current revision shown), Review
  tab (new-thread form from `pendingAnchor` with optional suggestion and page number, thread cards
  with Reply/Resolve/Reopen/Accept, outdated threads folded, Show resolved). `DocBody`'s selection
  bubble has **Comment** (quote + offset in `body_md`); `FileView` draws numbered pins over
  images (`.file-pin`, amber when outdated) and a click on the image starts a thread at that spot.
  `DocPage` toggles the panel (badge = open threads, else comments; it shares the right column with
  history) and reads `?comment=`. Styles under `/* --- discussion and review threads` in `docs.css`.
- **Pinned to top (migration `f3a9c7e2b5d1`):** `docs.top_pinned_at` / `top_pinned_by`;
  `docs.pin_to_top` / `unpin_from_top` (`doc.pinned_to_top|unpinned_from_top`, field
  `top_pinned_at`), `POST|DELETE /docs/{id}/top`, MCP `slate_doc pin_to_top|unpin_from_top`;
  summaries carry `pinned_to_top: {at, by} | null`. `drawer_listing` orders each listing with
  `pinned_first` (oldest pin first, then tree order), so a pin only reorders the listing the entry
  is in (the drawer's top or its folder). Shared, not per account: a drawer is the project's shelf,
  and a per-account pin would be a favorite. Any kind can be pinned, folders included. Named
  "top" everywhere because "pin" is the revision pin. Board: `docs.pinToTop`, the pin
  button in `DrawerPage`'s tools column (lit when pinned), 📌 before the name in the table and the
  rail, `tr.pinned` wash. Test:
  `tests/test_drawers.py::test_pin_entries_to_the_top_of_a_drawer_or_folder`, e2e in
  `drawer.spec.ts`. Gotcha: the board's `EventSource` listens to an explicit list of event
  type names (`store.svelte.ts`, after the `// Every event type Slate emits` comment), so a new
  event type is silently never heard until it is added there; the symptom is "the write succeeds,
  nothing live-updates". Add the type to that list in the same change as `docgen.EVENT_DOCS`.
- **Dated from the name:** `docs.date_in_name` (ISO, US `m-d-y[y]`, compact `yyyymmdd`,
  month names; year 2000-2099; the first real date in the name) and `created_from_name` (noon
  `settings.timezone`, in UTC). `create_doc` sets a file doc's `created_at` from its title.
  Only `created_at` moves; revisions keep their upload times. The drawer table's **Created**
  column (`shortDate`) shows it. Test:
  `tests/test_doc_files.py::test_the_date_in_a_filename`,
  `::test_file_docs_are_dated_from_their_names`.
- **Board**: `lib/docs.ts` `viewerKind` (pdf | image | docx | xlsx | text | table | other, by
  content type then filename), `docGlyph` (the tree icon by type), `parseTable` (CSV/TSV);
  `lib/docs.svelte.ts` `createFile`, `uploadRevision`, `revisions`, `restoreRevision`, `fileText`,
  `download`. `components/docs/FileView.svelte` renders a revision (PDF in an `<iframe>` on an
  object URL fetched with the token, since frames cannot send the header; images; text; CSV as a
  table; a download card for the rest), `FileRevisions.svelte` is the history panel for files (note
  folded in a `<details>`, Download, Make current), `UploadRevision.svelte` the sheet (file +
  required note; `defaultNote` "First upload" for a new file). `DocPage` switches on `doc.kind`: the
  viewer and a *Viewing revision N of M* banner replace the editor, the body sits under **Notes
  about this file**, drag-and-drop onto the page opens the sheet. `DocsPage` has **Upload** beside
  New page and an *Upload a file* card; `TreeRow`, `Nav`, `ProjectHome` and the hits list use
  `docGlyph` and show `rN`. The task window's attachments have **Move to Docs & Files**
  (`task.promoteAttachment`, into the task's section). Tests: `tests/test_doc_files.py`,
  `web/e2e/files.spec.ts`, Vitest in `docs.test.ts`.

- **Tables** (migration `38b0917911cc` after `72dfb391b796`): `docs` (title, icon, `parent_id` |
  `section_id` | `list_id` for a top page, `body_md`, `position`, `revision` bumped on every save,
  `version_no`, archived_at), `doc_versions` (one row per sitting: title + body as they stood,
  author, note), `doc_files` (bytes in SQLite, deferred column). The migration also widens the
  favorites `favorite_kind` CHECK to `doc` (batch recreate).
- **Service** `service/docs.py` is the only writer. Events:
  `doc.created|updated|moved|archived|unarchived|deleted|version_restored|file_added|file_removed`
  (file events carry metadata, never bytes). `update_doc` takes `title`, `icon`, `body_md` or
  `append_md` or `replace_section {heading, body_md}` (agents edit one section without resending the
  page), `base_revision` (409 `conflict` when someone saved in between), `new_version`, `note`.
  **Versions**: `_checkpoint` folds a save into the latest version when it is the same author,
  unnamed and under 10 minutes old; otherwise it seals that version and starts a new one. A note
  names a version and seals it. Restore writes a new version, never rewrites history.
  `update_doc` and `create_task` re-read the doc with `fresh_doc` (`populate_existing`) as the
  first thing inside `apply_change` and make the `base_revision` check and the `append_md` /
  `replace_section` / `replace` edit on that row, so a save that committed while the request
  waited for the lock is a 409, not a silent overwrite, and two appends both land. `move_doc`
  reads the tree and checks it (not under itself, at most `MAX_DEPTH` deep) inside its
  `apply_change`, with `_all(fresh=True)`, so two moves at once cannot build a parent cycle; the
  tree walkers (`_ancestors`, `_descendants`, `_height`, `_tree_order`) still stop on one. There is
  no database trigger for doc cycles (items have one). **Gotcha**:
  any query inside `apply_change` autoflushes, and an uncovered dirty row raises
  `WriteWithoutEvent`; read the latest version *before* the change block and give new rows explicit
  ids and timestamps.
- **Embeds**: a line that is only `[[task:ID]]` renders as a live task card; `[[task:ID]]` inline is
  a chip, `[[doc:ID]]` a page link (also in task descriptions). `POST /docs/{id}/tasks` creates
  exactly one item (rule 1) in the page's list (or `list_id`), its body `From doc [[doc:ID]]`, and
  replaces the selected text (`replace`, `occurrence`) with the embed or appends it. `GET
  /docs?embeds=<item id>` lists pages that mention a task (the task window's "In docs"); `GET
  /docs/{id}` returns the embedded tasks as item views plus backlinks, outline, path, children and
  files.
- **Archive and delete**: archiving a page hides its subtree; pages in archived lists or sections
  are hidden too. Delete only works on an archived page and takes its subpages. Export
  (`service/export.py`) includes docs in scope.
- **REST/MCP**: routes as in the modules table; images upload raw (`POST /docs/{id}/files?filename=`
  with the content type) or as base64 for agents. `GET /docs` and `GET /docs/{id}[/versions]` are in
  fastlane `CACHEABLE`. MCP tool `slate_doc` (list, get, templates, create, update, move, archive,
  unarchive, delete, duplicate, versions, version, diff, restore, create_task, attach, remove_file).
- **Board**: `#/docs[/<id>]`, `components/docs/` (`DocsPage` = sidebar tree by place with search,
  drag to nest or reorder, archived pages, home with recent pages and templates; `DocPage` =
  breadcrumbs, icon, title, conflict banner, actions, subpages, backlinks, outline; `DocBody` =
  toolbar and the in-place editor (contenteditable over `lib/richdoc.ts`; slash menu, `# `/`- `/`1.
  `/`[] `/`> ` line shortcuts, link box, task/page pickers, image paste/drop, selection bubble with
  Bold/Italic/Link/Create task/Comment; it redraws from the text on blur, on paste and when the page
  changes elsewhere, keeping the caret by character offset); `DocHistory`; `TaskEmbed`; `TreeRow`;
  `Picker`). `lib/docmd.ts` renders the page into html and task blocks (escape-first, like
  `markdown.ts`), `lib/docs.ts` holds the pure editing helpers (slash commands, list continuation,
  tree), `lib/docs.svelte.ts` the store (900 ms autosave debounce, serialized PATCHes, conflict
  state). Tree expansion follows the account (`prefs.docs_expanded`). The palette searches page
  titles locally and page text on the server; `g p` opens Docs. Styles in `docs.css`; checklists use
  `.md-check` because the global `.check` is the board's round check button (in the editor an entry
  is `li.md-li-check > input + text`, normalized after every keystroke). Editor gotchas: Chrome can
  nest a new list inside an empty `<p>` and leaves text loose in the wrapper after leaving a list;
  `DocBody.fixNesting` lifts both out, moving nodes and restoring the caret by node, not offset.
  Table cells override `.md`'s `overflow-wrap: anywhere` with `break-word` and a `min-width`:
  `anywhere` lowers a cell's min-content width to one character, so auto table layout would
  squeeze short columns to a letter per line.
- **Mentions**: a save (create or update) that adds `@handle`s the body did not have records
  `doc.mentioned` (`after` = `{doc_id, title, mentions, excerpt}`; autosave resends the body, so
  only new handles count). It is in the inbox's and the check-in's `MENTION_TYPES` (inbox entries
  carry `doc_id`, `item_title` = the page title), webhooks' "about me" filter and push
  (`/#/docs/<id>`). Opening a page reads its inbox entries (`inbox.readDoc`). A push agent
  named by a new mention is also woken (`doc.mentioned_agent`, a doc turn) and its answer starts
  a discussion thread on the page.
- **Phone**: under 760 px the tree is the first screen and a page opens over it with an "All pages"
  link; the toolbar scrolls sideways and the outline is a button. Every action is reachable from a ⋯
  menu as well as by drag. `e2e/docs.spec.ts` checks both.
- **Facts (not a doc kind of its own):** a page whose `body_md` starts with a
  fenced ```` ```facts ```` block (blank lines before it allowed, tag case-insensitive) of `key:
  value` lines is a record. `docs.parse_facts` reads it on every view into `facts: [{key, value}]`
  (trimmed, in order, duplicates kept, empty values allowed, lines without a key skipped; an
  unclosed block or one anywhere else is ordinary code) and `doc_out` carries it (`DocOut.facts`, so
  REST and MCP agree); the block stays in the body, so `GET /docs?q=` finds a value and
  agents write it with the ordinary `update`. `DocPage.svelte` renders `doc.facts` as a two-column
  table (`.doc-facts`, `docs.css`) above the body; the in-place editor shows the block itself as a
  code block, which `richdoc.preMd` round-trips, so there is no facts editor. No table, no
  migration. Tests: `tests/test_doc_facts.py`.

### Collaboration correctness

- Migration `a149dc02b001` adds nullable `doc_comments.anchor_page_revision`. Page comments use the
  monotonic `Doc.revision`, not folded `version_no`, for staleness; an anchor without one reads as
  stale, because folded history cannot prove its identity. The history-version stamp
  (`anchor_revision`) is kept for display. File comments accept optional `viewed_revision` through
  the shared REST/MCP schema; the board sends the selected/pinned revision. Replies inherit root
  revision identity.
- Accept fails closed on any intervening page save, a mismatching exact offset span, or a non-unique
  quote without offsets; it never searches another duplicate as fallback. Resolve/Accept are
  explicit completion; doc replies do not auto-resolve Asks. A reply emits one
  `doc.mentioned` with deduped mentions + root author/assignee/previous repliers, excluding the
  sender, preserving comment id/excerpt/source routing; there is no separate reply event.
- Needs you includes historical document editors from `doc.updated|revision_added|version_restored`
  events, not only `updated_by`. Pin/unpin IN USE events are included in document activity.
- Page drafts persist per account/document in localStorage with in-memory fallback. Restore requires
  a choice, Download my draft provides recovery, and Keep mine uses the displayed remote revision as
  `base_revision` (never force-writes). A navigation generation guard prevents late open responses
  from selecting an old page.
- Document cache ownership is independent of `board.me`: the payload-free `board.onSignOut` hook
  synchronously preserves departing text under its original account, clears
  document/index/activity/file URL state, cancels timers and invalidates pending work before
  authentication is removed (including HTTP 401, which does not reload the page). Epoch guards
  reject stale save/list/create/upload completions; open generations guard success, catch and
  finally. Reopening the already-current route must not invalidate its pending activity load.
  Reverting to saved text removes recovery immediately unless a save is in flight; that undo remains
  durable until written against the acknowledged revision.
- Discussion/review/reply composers reuse `persisted` and `draftKey.doc`, partitioned by
  account/document/field and thread or review context. A review key includes the original viewed
  revision and anchor, preventing recovery onto a different span. Closing the phone panel retains
  text; sending or explicit Cancel clears that form. After reload, reselecting the same span on the
  same revision recovers its review fields. This is browser storage, not a server note or an event
  payload. Regression gates: `docs-store.test.ts`, `drafts.test.ts`, and
  `doc-session.spec.ts` (synthetic accounts/database, 401/403 and delayed responses).
- The drawer filter matches names in the current folder only and is labelled so ("Filter this
  folder by name"). Phone drawer entry opens content, not the discussion overlay; the toggle and
  thread deep links remain reachable. Task destinations exclude drawers; success is announced only
  after the API returns the saved item.
- **Extraction cap and compare-from-storage (`docs/collaboration-security.md`):**
  `SLATE_DOC_EXTRACT_MAX_BYTES` (`settings.doc_extract_max_bytes`, default 64 MiB of *inflated*
  input) bounds every parser that reads inside an upload; it never blocks an upload (a small
  workspace uploads few spreadsheets and should meet no friction): an over-cap file is stored and
  viewable and simply not indexed (`has_text` false, like an unreadable file).
  `doc_compare.extract(name, ctype, data) -> (text, tables)` reads a revision once at upload:
  `_check_archive` measures an XLSX's zip
  members (declared sizes, total, a 200x inflation ratio over 4 MiB, then a chunked inflate under
  the same budget) before `openpyxl` opens it (openpyxl loads `xl/sharedStrings.xml` whole even
  read-only; a 1 MB bomb measured 146 s and 1.5 GB); DOCX uses the same cap. Cells read out of a
  table are cut to `MAX_CELL` (1,000) characters and all of a revision's tables share one
  `MAX_TABLE_CHARS` (4 M) budget, because every cell of a workbook may point at one long shared
  string; the searchable text is written up to `MAX_TEXT` and no further. `_pdf_text` reads at
  most `MAX_PDF_PAGES` (300) pages, stops at a `PDF_SECONDS` (15 s) deadline checked between
  pages, and before opening a page measures its content streams and nested form XObjects through
  their whole filter chain (abbreviated names such as `/Fl` normalised as pypdf reads them; Flate
  inflated with an output cap) against `min(cap, PDF_CONTENT_BYTES)` (16 MiB); a stream it cannot
  measure (LZW, run-length, broken data) leaves that page unread. Extraction still runs in the
  server process: a subprocess with OS limits would bound it harder. CSV/TSV/XLSX
  tables are stored as JSON in `doc_files.tables_json` (migration `7c2d9e4b1f60`, `[[sheet, rows], ...]`,
  64 sheets max); **`compare` reads only `text` and `tables_json` and never opens a file** (opening
  two 2 MB workbooks measured 7 s per GET): a revision with either NULL answers `kind: none` with a
  `message` naming the unindexed revision. `slate backfill-doc-text` fills both columns for rows
  that lack them. Raw upload routes (attachments, `/docs/{id}/files`, `/docs/{id}/revisions`) read
  the body through `api/uploads.read_body`, which refuses a declared length over the cap, counts
  `request.stream()` as it arrives and stops at the cap with the same 422; `Content-Length` is not
  trusted (a chunked request has none). Board: `notIndexed` in `lib/docs.ts`, "not
  searchable" in `FileRevisions`, the compare `message` in `RevisionCompare`.
- DOCX text extraction uses stdlib ZIP + ElementTree, reads only `word/document.xml`, never extracts
  paths/resolves relationships, rejects DTDs and bounds that part to the extraction cap plus the
  300k text cap. Layout/headers/images are not compared; failed extraction returns download
  comparison, not false identical text.
- XLSX compare reads formulas (`data_only=False`) without executing them; row/column iteration is
  bounded. Formula changes with no cached Excel values are visible.
- Production `npm audit` reports moderate advisories (no high or critical) via exceljs. The
  browser uses its `dist/exceljs.min.js` entry, `Workbook.xlsx.load(ArrayBuffer)`; the Node
  archiver/glob filesystem writer path is not called. The uuid advisory concerns v3/v5/v6 buffer
  arguments; exceljs's inspected call sites use v4 for write-side conditional formatting IDs. This
  is a reachability assessment, not a clean audit or a complete malicious-workbook resource audit.
