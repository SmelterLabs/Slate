# Chat

Channels per list and section, General, threads, exact read exposure, the chat panel, rich
bodies, files on messages, and each agent's own chat. A chat @mention that starts an agent is in
`docs/agents-work-loop.md` ("Chat mentions wake an agent").

## Chat

One channel per list and per section, plus `general` (`service/chat.py`, `api/chat.py`, MCP
`slate_chat`, board `components/chat/`).

- **No channels table.** A channel is a key: `general`, `list:<id>`, `section:<id>`. It lives,
  renames and archives with its list or section; posting to an archived one is a 422.
  `messages.channel` holds the key.
- **Message ids are time-ordered** (`new_event_id`). `channel_reads.exposed_ids` (JSON, default `[]`)
  holds exact exposures beyond the intentional `read_through` cursor. `POST /channels/{key}/read {message_ids: [...]}` unions IDs under the
  change lock; empty lists are no-ops, cross-channel IDs and mixing `through` with IDs are rejected.
  Cursor/read-all calls are intentional prefix reads. `chat.unread_messages()` is the
  shared read predicate: callers such as push re-nudges must honor exact exposures, not just the
  cursor. Channels return `first_unread_id`, `first_unread_thread_id` and persistent
  `unread_threads` counts. Own/deleted messages are never unread.
- **Exposure is not mounting.** `exposure.ts` requires 99% intersection, an uncovered message,
  visible document and focused window continuously for 500ms. The chat store rechecks lifecycle
  before an exact write; opening a channel never clears its Inbox wholesale. Oversized messages
  conservatively stay unread. First unread pages back or opens the thread. There is no recipient
  seen-receipt UI. Project home reuses loaded, access-filtered items and `myWork` scoped to its
  non-drawer lists; DocsHub is the source for document Needs you. The mention picker warns
  because `participant_ids`/the directory describes visible identities, not exact channel access.
- **Inbox navigation is not bulk acknowledgement.** Chat groups collect events from multiple roots
  but open only the newest thread; `Inbox.open` therefore leaves chat events untouched. Exact
  message exposure/read persistence drives their read state. This is chat only: task, doc and
  digest entries keep their own open policies, and explicit read-all reads everything.
- **Overlay dismissal at unchanged geometry:** `exposure.ts` shares one body `MutationObserver`
  (child changes and class/style/hidden/open attributes), coalesced per animation frame, plus
  transition/animation completion events. It exists only while message actions are mounted and
  disconnects/cancels queued work after the last unmount. Each change rechecks intersection, hit
  testing, attachment, focus and visibility; unrelated DOM churn does not reset an already eligible
  500ms dwell. No polling or task-dialog coupling. Browser regression uses the actual task scrim,
  proves identical message rectangles before/after close, and checks hidden/unfocused dismissal
  separately.
- **Sparse exposure retention:** `chat._compact` runs on every exact read. Safe step:
  the cursor moves up to the newest exposure below the first message still unread (live, by someone
  else, not exposed), so nothing unread is ever covered and the ids it now covers are dropped; in
  ordinary reading the list is empty or a handful of ids above one gap. Cap: at most `EXPOSED_KEEP`
  (500) ids are kept above that; beyond it the oldest become the cursor, which does cover whatever
  unread sat below them (a message skipped while 500 later ones were read counts as read; the
  bounded loss chosen over resurrecting dropped ids as unread, which would also re-nudge push).
  `channel.read` carries `after.through` when the cursor moved. `unread_messages()` answers the
  same either way, so push re-nudges and reminders see the same answer. Without compaction the
  list would grow with reading history until a read-all.
- **Threads** are one level: a reply's `parent_id` is the root, and replying to a reply lands on the
  root. `message.posted` for a reply carries `thread_followers` (account ids of earlier authors in
  the thread).
- **Routing reuses the inbox.** `message.posted`/`message.edited` are in the inbox's and check-in's
  `MENTION_TYPES` (they read `after.mentions`), and a reply reaches thread followers as inbox reason
  `reply` / check-in `why: reply`. Push (`notification_for`) links to `/#/chat/<key>?thread=<root>`;
  webhooks with `assignee_me` hear mentions and replies. Inbox entries for chat carry `channel` and
  `item_title = "#<channel name>"`.
- **Task links:** `?task=<id>` URLs and `#<id>` in a body become `linked_items` (existing, untrashed
  items only), as does the task made from the message (`item_id`). The board renders `#<id>` as the
  task's title and every linked item as a card.
- **Board:** `lib/chat.svelte.ts` keeps the channel list (counts), loaded pages and threads; each
  `message.*` event refetches that one message (`GET /messages/{id}`) into whatever has it loaded,
  and counts refresh (300 ms debounce; your own `channel.read` at once). Looking at a channel
  (`chat.view`) reads it and keeps reading what arrives. Routes: `#/chat[/<key>][?thread=<id>]`
  (Chat page) and `#/list/<id>/chat`, `#/section/<id>/chat` (the scope's Channel tab, `ScopePage`
  `chat`).
- **"<agent> is working…":** while an agent's chat turn is open,
  `components/chat/AgentWorking.svelte` shows one quiet line under the last
  message (the agent's robot avatar, pulsing dots): **will answer…** between the mention and the
  runner's take, **is working…** from the take to the end. Two states on purpose: "will answer"
  that lingers says the runner has not picked the mention up (runner off), which "working" would
  hide. A thread's turn shows in the thread pane only (`turnsIn(turns, rootId)`), a channel's
  under the channel's messages only; `ChannelView` serves the Chat page, the Chat tab and the panel
  alike. State: `chat.turns[channel]` (`chat.svelte.ts`), seeded from `GET /channels`
  `agent_turns` (`chat.agent_turns()`: a `ChatWake` row not taken is `waiting`, a taken one with a
  live lease `working`; an expired, quarantined row is neither) and kept by `applyTurnEvent`
  (`lib/chat.ts`, pure, idempotent set-to-value per agent and thread): `mentioned_agent` `for` →
  waiting, `queued` → working; `agent_turn` `started` → working, any end (`done`, `released`,
  `expired`) removes it unless `after.pending` (a mention arrived during the turn), which waits
  again. The agent's own post does not clear it: a runner-taken turn is fenced (`run_id`), so only
  `/work/chat/finish` ends it, and its `done` arrives in the same change as the reply; an interim
  message mid-turn keeps the line. Race: every turn event bumps `#turnSeq`, and a channel list
  fetched before one does not overwrite the turns. The two event types do not refetch a message.
  Tests: `tests/test_chat_wake.py` (`agent_turns`), `lib/chat.test.ts`, `lib/chat-turns.test.ts`,
  `e2e/agent-working.spec.ts`.
- **Gotcha:** the board's EventSource only hears event types listed in `store.svelte.ts`
  `EVENT_TYPES`; `tests/test_board_event_types.py` fails when a new server event type is missing
  there.
- **Naming:** to a person the thing is **Chat** everywhere
  (the scope's tab in `ViewTabs.svelte`, the project home's "Open chat", the Chat page's list and
  filter, the view's `aria-label` "#Name chat", the palette kind label, the user guide). Internal
  names stay `channel`: `channelKey`, `chat.channel()`, REST `/channels`, `data-view="channel"`, the
  `message.*` events and the agent docs, which describe the API. Do not rename those to match the UI
  word.
- **Sidebar:** each section row has an always-present chat link (`Nav.svelte` `sectionChat` snippet,
  `.nav-chat.quiet` in `chat.css` until something is unread, then the unread pill with the
  count; `data-section-chat`), so a section's chat is one click away. List rows keep the badge-only
  behaviour (`chatBadge`). Gotcha: the row's name link is styled by `.nav-heading > a` (design.css,
  `flex: 1`) and the chat link is also a direct `a` child, so `chat.css` pins `.nav-heading >
  a.nav-chat` to `flex: none`; without it the two split the row and every name is clipped. The
  section's hover actions (☆, +, ⋯) overlay the row's right end like a list row's do (`.nav-heading
  .nav-actions` absolute in app.css; the chat link steps left on hover), instead of holding 90 px
  open while invisible. E2E: `chat.spec.ts` "a section row always has a chat icon" also checks the
  name is not clipped.
- **Chat panel:** on a wide screen (`panelFits`: `innerWidth >= 900`) chat is a panel
  over the right edge of whatever page is open, not a page of its own. `lib/chatpanel.ts` (pure:
  `?chat=<key>[&thread=<id>]` parsing, the localStorage shape, the width rule; unit-tested),
  `lib/chatpanel.svelte.ts` (`chatPanel`: `open`, `channel`, `thread`; `show(key?, thread?)`,
  `hide()`, `toggle()`, `pick()`, `setThread()`; saved per device under `slate.chatPanel`,
  deliberately not in the account prefs so the phone, which never shows it, is untouched),
  `components/chat/ChatPanel.svelte` (the picker, "open as a page", Close, then `ChannelView` with
  `compact` and `onthread`). `App.svelte` mounts it when `chatPanel.open` and the page is not the
  Chat page or a scope's Chat tab, and `readHash` turns `?chat=` into `show()` (or, narrow, into
  `#/chat/<key>`). Entry points: the sidebar's Chat entry (toggle), the section chat links and list
  badges (`Nav.svelte` `openChat`), the scope's Chat tab (`ViewTabs.svelte`, lit while the panel
  shows that scope's chat; a click on the lit tab closes it) and the project home's Open chat; each
  keeps its `href` to the Chat page for a phone, a modifier-click or a middle-click, and
  `preventDefault`s otherwise. `ChannelView` `compact` hides its own head (the panel's head names
  the channel) and shows a thread over the messages (the phone rules, under `.chat-view.compact`);
  `onthread` keeps the thread in the panel state instead of the URL, so the page's URL never changes
  because of the panel. Back from the page: the open-as-page icon calls
  `chatPanel.expand(location.hash)` (remembers `returnTo`, closes), and the Chat page's channel head
  has a **Panel** button (`ChannelView`, not embedded or compact, only when the panel fits) that
  calls `chatPanel.dock(key, thread)`: it opens the panel on that chat and returns the hash to go to
  (`returnTo` unless it was itself a Chat page, else `#/home`). Width:
  `chatPanel.width` (default 420, `clampPanelWidth` keeps it between 320 and the window minus the
  sidebar and 160px of page; stored with the rest; a stored state without it reads as the
  default). The grip is a `<button class="chat-panel-grip">` on the panel's left edge (a button
  rather than a `role=separator` div only to satisfy Svelte's a11y checks): pointer capture on
  `pointerdown`, width = `innerWidth - clientX` on move, arrow keys ±24px, Home or double-click
  resets; while dragging the view gets `pointer-events: none` so the messages cannot grab the
  pointer. Layout: `.chat-panel` in `chat.css` is `position: absolute; grid-row: 3; grid-column: 1 /
  -1; right: 0; width: var(--chat-panel-w); z-index: 20` (under menus, settings and the task window
  at 40+). It overlays, it never pushes: the page must not shrink. So
  `.doc-page`, `.doc-main`, `.docs-home` and `.doc-loading` are left-aligned (`margin: 0`,
  `justify-self: start`; the 760px reading width stays), which on a desktop monitor leaves the panel
  in the empty right third; on a 1280px laptop the panel covers the doc's outline column and right
  edge. The Chat page (`#/chat`) and the Chat tab page (`#/list/<id>/chat`) are
  what links, the palette and phones open. Fallback: `ChatPanel.svelte` settles the
  channel once `chat.loaded` through `panelChannelFor(current, channels)` (pure, in `chatpanel.ts`):
  the remembered channel if the account can see it, else the first with a mention, else the first
  with unread, else the first. A guest's new browser starts from the default `general`, which
  guests cannot read; without the fallback the sidebar's Chat entry would show "channel general
  not found". `windowBadge(n)` (same file) is the title `(n) Slate` and,
  where the Badging API exists (Slate installed as an app in Chrome or Edge),
  `navigator.setAppBadge`; `n` is the Needs you count, not unread chat (`docs/task-window.md`,
  "The number means Needs you"). While the window is not in front
  (`focus`/`blur`/`visibilitychange` in `App.svelte`) and chat is unread, the title flips every
  `FLASH_MS` between that and `flashTitle(channels)` (`New in #Todo +2`, the channel with the newest
  unread first). E2E: `chat-panel.spec.ts` (1920px desktop: tab → panel, board stays; Docs, reload,
  thread, picker, Close, sidebar toggle, `?chat=` link, section icon; phone: the tab and `?chat=` go
  to the page). `chat.spec.ts` and `drafts.spec.ts` reach the Chat tab page by URL.
- **Chat sizing:** `.shell` is the positioning context and `.chat-panel` is an
  absolutely positioned grid child in row 3 (spanning both columns), so it starts below the actual
  favorites header without a brittle pixel offset. The width arrow toggles 420/720px through the
  clamp; `fullscreen` fills that row, preserves the docked width, and resets on close. Size
  changes keep the same `ChannelView`/composer mounted (no route change, draft or thread loss).
  `AgentChatButton` is a labelled 36px pill (40px on
  phones); small phones keep the Slate tile without the wordmark or redundant page title, with
  tighter gaps to fit the label and populated badges. E2E `chat-window.spec.ts` checks geometry and
  pointer hit targets, size round trips, composer continuity and reload; `topbar-width.spec.ts`
  covers populated badges at 390/360/320px.
- **Rich bodies:** `lib/markdown.ts` (descriptions, comments, chat) renders what
  `docmd.ts` renders minus the page-only parts (anchors, images, embeds): pipe tables with alignment
  and `\|` escapes (`.table-wrap`), `>` quotes, `---` dividers, lists nested by indent that survive
  one blank line and take a wrapped continuation line, `start=` on numbered lists, `~~del~~`.
  Headings start at h3 (inside a card). The output shape for flat lists and task boxes is
  `<ul><li>…` and `input.md-task[data-line]` keyed to the source line, which `AutoText`
  toggles. Styles live under `.md` in `app.css` (the doc page keeps
  its own `.doc-rendered` copies). Unit tests in `markdown.test.ts`.
- **Bare URLs are links:** `inline()` in `markdown.ts` turns any `http(s)://…` run into
  `<a target=_blank rel=noopener>` wherever it was typed (description, comment, chat, doc
  discussion, table cell, list), matching what `docmd.ts` does on pages. `trimUrl` drops
  trailing `.,;:!?`, an unbalanced `)`/`]` and escaped quotes/angles so `(https://x/y).` links
  `https://x/y` but `…/Slate_(rock)` keeps its parens. Code spans, markdown links and the autolinks
  are stashed as `\u0000n\u0000` placeholders before the emphasis passes (same device as
  `docmd.ts`), so `_` and `*` inside a URL never become `<em>`. Only `http`/`https` autolink;
  `mailto:` and `obsidian:` need `[text](url)`.
- **Page and list links:** the `#` picker
  (`ChatComposer.svelte`, grouping in `lib/chat.ts` `hashPicks`, unit-tested) offers tasks, pages
  (`docs.list`: only pages the writer can see) and lists. A task inserts `#<id>`; a page
  inserts `[[doc:<id>]]` and a list `[[list:<id>]]`, the same token the doc editor uses for pages,
  chosen over a plain `[title](#/docs/<id>)` link because the renderer shows the *live* title and
  icon, and a reader who cannot see the page gets no title at all. `lib/markdown.ts` renders both
  tokens for every message and comment body (`RefResolvers`: `lib/refs.svelte.ts` `liveRefs()` reads
  the docs index and `board.lists`; unknown → "📄 a page" / "a list", the link still opens so the
  usual gone screen answers). Server side nothing resolves them: `chat.readable()` (push text) turns
  the tokens into "a page" / "a list", never a title, because the recipient may not see that page.
  E2E: `chat.spec.ts` "# links a page and a list".

## Files on messages

A message carries the files sent with it: a screenshot pasted from the clipboard, a dropped or
picked file (`service/chat_files.py`, `message_files`, migration `b4f8d2e6a1c9`).

- **Upload, then send.** `POST /channels/{key}/files?filename=` (raw body; `/files/base64` for
  JSON-only clients, MCP `slate_chat` `upload_file`) stores a `MessageFile` with `message_id`
  null and answers `{id, filename, content_type, size}`. `POST /channels/{key}/messages` with
  `file_ids` sends them: `chat.post` takes only the caller's own unsent uploads to that channel
  (anything else is a 422), at most `chat_files.MAX_FILES` (10), and sets their `message_id`
  inside the message's change. `message.posted` `after.files` and every message view (`files`)
  carry the metadata, so a woken agent finds the files on the mention it is handed. With files
  the body may be empty, on post and on edit.
- **Checked like attachments** (`attachments.clean`): a name, not empty, at most 25 MB, a plain
  `type/subtype` content type or the one guessed from the name. Bytes live in
  `message_files.data` (deferred), so the database backup covers them and the health report's
  `db.blob_bytes` counts them. The body is read under the cap (`api/uploads.read_body`).
- **Read where the message is read.** `GET /message-files/{id}` (`chat_files.read_bytes`) loads
  the file's message through the caller's read filter, so channel access, a project agent's
  share date and members-only apply to the file exactly as to the message; anyone else gets the
  404 of a file that does not exist. Served by `api/uploads.file_response`: `nosniff`,
  `no-store`, the sandbox CSP, inline only for images, PDFs and text.
- **Unsent is private.** Until it is sent only its uploader reads or discards a file
  (`DELETE /message-files/{id}`). `message.file_uploaded` / `message.file_discarded` have the
  uploader as subject and no channel, so `access` shows them to nobody else. An upload never
  sent is swept on its uploader's next upload once it is `UNSENT_KEEP` (24 h) old.
- **Deleting the message deletes its files** (rows and bytes, in the same change;
  `message.deleted` `before.files` keeps the metadata). **Make a task** copies the files onto
  the task as attachments; the message keeps its own.
- **Board.** `ChatComposer`: `onpaste` takes `clipboardData.files` (a screenshot has no text,
  so plain text pastes as before), drop on the box, or the paperclip. Each file uploads at once
  (`chat.upload`) and waits above the box as a chip (images as thumbnails) with a remove
  button; Send is enabled by text or a file and is held while an upload is in flight. A
  clipboard image arrives named `image.png`: `pastedName` names it
  `Screenshot <date> <time>.png`. Changing channel takes back what was not sent.
  `MessageItem` shows images inline and other files as chips; a click opens `MediaViewer`
  (`task.open(file, {files, path})`, the same viewer as task attachments, mounted in `App`
  whether or not a task is open) and steps through that message's files.
- **Agents.** The Hermes adapter's chat prompt lists each message's `files` and where to read
  them. Tests: `tests/test_chat_files.py`, `web/e2e/chat-files.spec.ts`.

## Agent chats (`agent:<id>`)

Each enabled agent (not the `slate` system account) has a channel of its
own, keyed `agent:<agent account id>`, so a person can ask it a question or ask for something in
Slate without a card.

- **No table or migration of its own.** It is one more channel kind in `service/chat.py` (`AGENT`,
  `parse_channel`: the account must be a non-system agent, else 404; archived while the agent is
  disabled, so a post is a 422 and the channel leaves the list). `list_channels` puts the agents'
  chats right after General, by display name; rows carry `account_id`. REST `ChannelOut.kind`
  includes `agent`.
- **The wake is the mention path.** For a workspace agent (`role != guest`), `chat.post`
  adds the agent's handle to `mentions` when someone else posts in its chat: inbox, check-in and
  push-agent runner keep direct-chat addressing. A **guest-role project agent requires an explicit
  `@handle`**, from owners and guests alike. Plain messages, mentions to other people/agents and
  untagged follow-ups (including thread replies) do not wake or queue that channel agent. There is
  no intelligent monitoring. The role of the channel agent, not the sender or a specific handle,
  is the distinction; sharing a workspace agent does not change its direct-chat behavior. Edits
  wake only newly added explicit mentions; keeping a mention in an edited body does not wake
  twice. The agent's own posts do not mention itself. Open turns are not cancelled or rewritten.
  `AgentWorking` is driven only by server `agent_turns` and wake/turn events, never by a plain
  message or the fact that this is an agent channel. Channel metadata `mention_required`
  drives the role-specific guidance (the scoped roster intentionally omits roles). The board
  uses the already-loaded scoped roster for identities so a new, empty project still has its
  agent in the mention picker and waiting/working line; no additional access is granted.
- **Who can read it** (`Access.channel_level` for `agent:<id>`): the agent itself (even a guest-role
  agent); owners, admins and members, all of them (whoever sees General); and a **guest or
  list-limited token, for each agent shared into a section or list it can see**, at the caller's
  best level there (`Access.agents`, filled by `access._project_agents` from the agents' `shares`
  rows against the caller's final `lists`/`sections`). So a project's guests read and talk in its
  agent's chat and see no other agent's: what the project agent says, the project's guests can
  see. Only a share counts as "in the project", not being assigned a card there. A view-level
  guest reads but cannot post (403). It is one channel per agent, not a private DM: what a member
  says to the project's agent there, the project's guests read too. The read filter matches member
  access with `LIKE 'agent:%'` (a never-matching pattern when General is not visible) and guests
  through `visible_channels()` (their project agents plus their own); `list_channels` lists
  exactly the agent chats `channel_level` allows. The live gate (`EventGate`) closes a stream when
  an agent's chat drops out of reach (an unshare), like any narrowing.
- **Board:** `components/AgentChatButton.svelte` (a `Dropdown` with the agent avatar's gradient;
  badge = unread across agent chats; `lib/chat.ts` `agentChats`, `channelIcon`, `isAgentChat`).
  Mounted at the right end of the favorites bar (`.favbar-end`, sticky, hidden on a phone) and in
  the phone top bar. Picking an agent calls `chatPanel.show('agent:<id>')` when the panel fits and
  the Chat page is not open; otherwise the link opens `#/chat/agent:<id>`. `ChannelView` names an
  agent chat without `#` (`Message My Agent`) and wears the robot. The chat store refreshes the
  channel list on `account.created|updated|disabled|enabled`. Notifications call it "chat with My
  Agent" (`chat.place`).
- **Phone width:** `.topbar` is a grid item and needs `min-width: 0` so its ellipsized
  title can yield space to Needs you, agent-chat and Inbox badges. Without it the grid's automatic
  minimum widens the entire phone layout when all controls are populated. `e2e/topbar-width.spec.ts`
  asserts populated controls stay reachable without horizontal overflow at 390 and 360px.
- Tests: `tests/test_agent_chat.py` (members, project guests, view-only, a list share, unsharing, a
  guest-role agent, the live gate), `web/src/lib/chat.test.ts`, `web/e2e/agent-chat.spec.ts`
  (desktop, phone, a project guest).
