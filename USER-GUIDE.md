# Slate — User Guide

Slate is your task board. Agents file, update and complete the work; you look at the board and steer. This guide is for the people who use it: the owner, members and guests. How to connect an agent is in [docs/agents/](docs/agents/README.md); how Slate is built is in [TECHNICAL-GUIDE.md](TECHNICAL-GUIDE.md).

## The shape of things

- **Sections** are the big separations, for example Personal and Work. A new section is for a different audience or rhythm, not for a topic.
- **Lists** live inside a section (Todo, Read / Watch, Projects…). Each list has its own statuses, which are the columns on its board.
- **Items** are tasks. An item has a title, a Markdown description (every edit is kept as a version), a status from its list, a priority (urgent, high, normal, low or none), start and due dates, assignees (people or agents), who it is waiting on, what "done" means, tags, subtasks (four levels deep), comments, attachments and a full history of who changed what. Filing an item never makes subtasks; an agent splits an item only when asked.
- **Documents are not items.** Pages and files live in **Docs & Files** and in a section's drawers.
- **Dates.** A due date given as just a day means 5 pm that day in the workspace timezone (set by the server's administrator).
- **Three ways off the board**, each said when you press it. **Complete** means done: the card stays on the board until midnight, then sits under Archive › Done, still searchable. **Put away** takes it and every subtask under it off the board, history kept; bring it back from Archive. **Move to trash** deletes it for good after 30 days unless you restore it. A task you made in the last 10 minutes also has **Delete (undo create)** in its ⋯ menu; that is an undo, not a way to tidy up.
- **Tags** cut across everything. **Recurring rules** create items on a schedule.

## Signing in and finding your way

Open Slate's address (for example `https://slate.example.com`) in any browser, on a computer or a phone.

**Signing in.** Members sign in with a Slate token: the first visit asks for it. Paste it once; the browser keeps it, also after it is closed, until you press **Sign out** at the bottom of the left menu. The token is a credential: the board keeps it only in the browser's storage and never puts it in an address, a link or a log line. If the token is refused (revoked, mistyped), the board goes back to the token screen. A project guest opens the private invitation link from the project owner and presses **Accept invitation & sign in**; the browser stays signed in for up to 30 days (see *Sharing a private project*).

**The left menu.** Four entries stay at the top: **My Work, Inbox, Chat, Everything**. The other pages (**Docs & Files**, **Agents**, **Archive**, **Members**, **Audit log**, **Health**) are under **More**, which remembers whether it is open. Below come your sections, each with its lists, open-item counts and its own pages. At the bottom are your name and four buttons: **Notifications** (the bell), **Tags**, **Archived** (archived sections and lists) and **Sign out**. On a phone the menu hides behind ☰.

**The look.** Slate follows your system's light or dark setting; **Customize → Theme** picks one for your account on every device. The small `r129`-style number by the logo is the build that is running (hover it for the commit), and the dot beside it is green while the board is live and amber while it reconnects. Animations are short and stop if your system asks for reduced motion.

**Live.** Changes from agents and other screens appear within a second; you never reload. If the connection drops, a yellow "Reconnecting…" bar appears; when it clears, everything that changed meanwhile has been applied. After a new version of Slate is deployed, an open board reloads itself into it, on a computer and a phone alike; if you are typing, it waits until you leave the box.

**Nothing you type is lost.** Text you start and leave stays where you typed it until you send it: a comment or question on a card, a chat message, a new task in a column or the quick add bar, a subtask, a checklist item, a hand-off, an edit to a comment or message. Close the card, switch views or reload and it is still there. A card holding unsent text shows a **Draft** tag, and so does a column's **+ Add Task**. Sending clears the draft; **Cancel** (or Esc) throws it away. Drafts live in that browser only and are dropped after 30 days untouched. A title typed and left when the card closes is saved; descriptions and pages save themselves as you type.

**How much text fits.** A task's description and its *Done means* hold up to 200,000 characters each, a comment 50,000, a chat message 20,000 and a page 1,000,000. A file is up to 25 MB. Slate refuses anything longer and says why.

## Sections and lists

Nothing is hard-coded:

- **+ Section** (under the last section) adds a section: give it a name and who it is for.
- Hover a section's name for **+** (add a list) and **⋯** (add list, rename, move up or down, archive). A new list starts with three columns, **To do · In progress · Done**, and opens straight away.
- Hover a list for its **⋯**, or right-click it: rename, **List settings…**, move up or down, **Move to** another section, archive.
- **Drag a section heading** above or below another to reorder sections, or use **⋯ → Move up / Move down**. Guests cannot reorder sections.
- **Drag a list** onto another list to put it just above, or onto a section's name to put it at the end of that section.
- **Archive** asks first, then hides the section or list and its items. Nothing is deleted: **Archived** at the bottom of the menu lists them, with **Restore** next to each.

**List settings** edits a list's name and its columns (statuses): rename, add, reorder with ▲▼, pick a colour from the dot, choose the **default** (where new items start) and the **kind**, which tells Slate what the column means: *Open* (not started), *Active* (in progress), *Done* (what **Complete** uses) or *Cancelled*. Removing a column that still has items asks where to move them. **Kind** at the top turns an empty list into a drawer (see *Docs & Files*).

## The board

**Everything** is where Slate opens. Its first view, **This week**, is the due board: **Overdue** (red) · **Today** (yellow) · one column per day for the next six days · **Future** · **No due date** · **Done** (finished today). Days with nothing due fold into a thin strip; click one to open it, or use a column's ⋯ → **Collapse column** (remembered per view on your account). Days roll over at midnight workspace time without a reload. The second view, **Agenda**, lists what is overdue, due today and waiting on you, across every section, most urgent first (`#/today` opens it).

**A list** opens as a board: one column per status. Each card shows:

- the title (two lines, then "…");
- small counts: ≡ for a description, subtasks, comments, checklist progress and attachments;
- pills: the due date ("Today, 4:00 pm", "Sun", "Nov 1"; red once overdue, and past a week "Aug 2 · 82d late"), the priority flag, tags, status, assignees (🤖 for agents), its list (in Everything and sections), who it is waiting on, and **Recurring**.

**Comment dots.** A dot in a card's bottom-right corner means someone commented since you last opened it: **gray** when only agents did, **blue** when a person did, **pulsing blue** when someone mentioned you or asked you a question. Your own comments and field changes never light it. Opening the card clears it on every device; reading the comment in the Inbox does not.

**Card tools.** Hover a card: the box at its top left selects it; the circle at the top right completes it (the note at the bottom offers **Undo**); ⋯ (or right-click) has Open, Complete, due Today / Tomorrow / a date / clear, priority, select, copy title or ID, **Move to…**, put away and move to trash. The small copy icon in the bottom-right corner copies the task ID. Click the date pill to reschedule and the priority pill to change priority.

**+ Add Task** at the foot of each column files a task into that column: typed into Tuesday it is due Tuesday, into High it is high priority, into a status column it gets that status. Enter adds it and keeps the box open; Esc closes it.

**Dragging a card** to another column changes what the columns stand for: its status, due date (it keeps its time of day, or 5 pm; **No due date** clears it; **Future** puts it on the first day after the named days), priority, assignee (replacing whoever was on it), list, tag or section. Dropping on **Done** completes it. Folded columns and the empty space under a short column take drops too. A column that cannot take a card (**Overdue**, **No status**) gets a grey outline and a note saying why. When the view is sorted **Manual** (the default for a list), a violet line shows where the card will land. If Slate refuses a change, the card jumps back and a red note says why. Columns with many cards show the first 25 and **Show more**.

### Adding tasks

**Quick add.** The box at the top files one item. In Everything or a section, a list picker sits beside it. Type the title with any of these:

| Type | Means |
|---|---|
| `#home` | tag it "home" (new tags are created) |
| `!urgent`, `!high`, `!low`, or `!1`..`!4` | priority |
| `@my-agent` | assign it to that account |
| `~bills` | put it in that list |
| `due:friday`, `due:2026-11-01`, or plain words: `tomorrow 4pm`, `by fri`, `jan 5 at 9`, `in 3 days` | due date (5 pm unless you give a time) |
| `every monday at 7am`, `every other friday`, `every 15th` | make it recurring |

The preview under the box shows what was understood; anything it does not recognise stays in the title. Put `\` before a word to keep it in the title (`Watch \friday night lights`). The same words work in a column's **+ Add Task**.

**AI add.** When the server has an AI model configured, quick add and every **+ Add Task** have a small **✨ AI** switch, lit to start with. Describe the task the way you would say it: *"Figure out why the backup job keeps failing, check the logs and the disk space, get it done by Friday afternoon, it's important."* The card appears at once with your words; a few seconds later Slate fills in a clean title, the owner, dates, priority, status, tags (ones already in use, or ones you name) and a tidy description with your steps as checkboxes. The history shows those changes as **Slate**'s. Anything you change before it finishes stays as you set it; if the AI cannot answer, the card stays as you typed it. It only offers people you can see as owners. A card filed in a column still starts with that column's meaning, and what you said wins over it. Click **✨ AI** to switch it off on this device and use plain quick add. The model is the one your administrator set; when it runs on your own server, nothing you type leaves it.

## The task window

Click a card to open it in a large window (full screen on a phone): the task on the left, its activity on the right. Every change saves as you make it. Drag the corner at the bottom right to resize it (double-click to reset), drag the edge between task and activity, or use the window button next to Close to park it on the **right third** or **right quarter** of the screen, where the board beside it stays live. Slate remembers the size and place on this device. The address bar carries `?task=<id>`, so a reload keeps the task open and a copied link opens it for anyone signed in. Escape, ✕ or a click outside closes it.

**On a card an agent is on** (assigned, reporting or has worked it), the conversation comes first: status, title, then the thread and the comment box, with **Ask**, **Run now** and **Hand to an agent** together by the box. Assignees, dates, tags, list, repeats, estimate and dependencies fold into one **Details** line ("My Agent · Due Nov 20 · High · #example"); click it to open them. Cards with no agent show the fields in a grid under the title.

- **Status**: the coloured button at the top. Click it for the list's statuses, each a pill in its own colour (the current one ticked); **›** moves to the next column; the **✓** square completes it (click again to reopen).
- **Title**: click and type; Enter or clicking away saves.
- **Assignees, Priority, Dates, Tags, Waiting on, List, Repeats.** Dates have quick picks (**Today**, **Tomorrow**, **Next Mon**, **+1 week**, **Clear**); **+ time** sets a time other than 5 pm. **+ assign** opens a searchable list of people and agents; each tick saves at once and the list stays open for more. Tags: type to see matching tags, Enter adds what you typed, Backspace on an empty box removes the last one.
- **Description**: click and type; it saves a second after you stop ("Saving…", then "✓ Saved"). Markdown works: Ctrl+B, Ctrl+I, Ctrl+K for a link, Enter continues a list, `- [ ]` makes a tick box you can tick in place. Headings, nested lists, tables, quotes, dividers, struck text and code render here, in comments and in chat. A pasted web address becomes a link by itself.
- **Done means** (optional): what has to be true for the task to be finished. An agent reads it as part of its brief.
- **Subtasks** show as rows with a completion circle, status, assignees, due date and priority, and a progress bar. Click one to open it; its window links back to the parent. Subtasks always live in their parent's list.
- **Checklists** are tick lists inside the task, not separate tasks. Click a checklist's name to rename it; its ⋯ hides checked items, checks everything or deletes it.
- **Attachments**: drop, paste or pick files (up to 25 MB each). Images show a thumbnail. Images, videos, PDFs, Word files (.docx), Excel workbooks (.xlsx, the first 2,000 rows of each sheet), CSV and text files open in a near full-screen viewer (← → step through the task's files, ↓ downloads); it is a view, not an editor. HTML, SVG and XML files always download instead of opening, as do old .doc and .xls files, PowerPoint and anything else. An image an agent pastes as a block of base64 text is saved as an attachment and the text says "📎 name (attached)". **Move to Docs & Files** (hover an attachment) turns it into a file document in the task's section; the task keeps a comment linking to it.
- **Activity** (right): comments as cards, with every change between them in small grey lines ("My Agent changed status from To do to In progress · via MCP"). **All / Comments / History** filters it. Comments that mention you have a blue edge.
- **Comment** at the bottom: Enter sends, Shift+Enter makes a new line, `@` mentions someone. Paste a screenshot (Ctrl V) or drag a file onto the box: it shows as a thumbnail above the box (× removes it), and the comment shows the picture; the file also lands in Attachments. Hover a comment to react, **Reply** (a comment mentioning its author) or **Edit** your own. **Delete** removes a comment for good after asking: your own, or anyone's if you are the owner or an admin. Its reactions go with it, a question it asked closes, and the history keeps a line saying who deleted whose comment.
- **Header**: where the task lives (click the list to go there), its ID (click to copy), a copy-link button, **Move**, and ⋯ (copy link, ID or a Markdown line, attach, add a checklist, put away or bring back, move to trash).

**Watchers.** The 👁 at the top of Activity shows who is watching. Click it to **Watch** or **Stop watching**, remove anyone (×), or add a person or agent; anyone who can edit the task can add or remove others, and they are told. With view or comment access you can only watch or stop watching yourself. A watcher hears about everything on the task (comments, status, dates, assignees, files, subtasks, an agent starting or finishing) in the Inbox and, unless switched off, on their devices. An agent starting work shows in the Inbox only; the push comes when it finishes, asks you something or hands the task back. You start watching a task when you create it, are assigned to it or comment on it; **Notifications → Watch tasks automatically** turns each off. Agents do not watch on their own, but anyone can add one as a watcher.

## Views, filters and layouts

**Views.** Every page (Everything, a section, a list) has view tabs along the top. A view is a saved way of looking: layout, grouping, sorting, filters and what cards show. Until you save one, a page shows its **Default** (lists: their status columns; Everything and sections: the due board). Change anything in the toolbar and an orange dot appears on the tab with **Save**, **Save as new** and **Revert**. Slate remembers where you left off, on every device, until you Save or Revert. **+ View** saves the current setup under a new name; the tab's ⋯ renames, duplicates or deletes it. A view is changed or deleted only by whoever made it or by an admin; the built-in **This week** and **Agenda** only by an admin. A view an agent saves as not shared is seen by its maker alone. The address bar names the view (`#/everything/v/…`), so a bookmark opens exactly that view.

**Layouts.** A view shows its items as a **Board** (columns), a **List** (rows in collapsible groups) or a **Table**; `b` switches between Board and List.

- In the **List**, click **Title**, **Due** or **Priority** to sort (again to flip). Each row's left edge is its priority colour, red when overdue. Grouped by status, each group header shows the estimates it holds.
- The **Table** is a spreadsheet: hover a row and every cell (status, dates, priority, assignees, tags, estimate) edits in place. Click a title to rename it, **Open** for the full item, a header to sort. Each group ends with a totals row: item count and total estimate.
- A view saved with a layout Slate no longer has opens as a List, filters and grouping kept.

**Toolbar.**

- **Group**: status (in a list), status kind (Open / Active / Done / Cancelled, across lists), due date, priority, assignee, tag, list, section, or none. An item with two assignees or two tags shows under both.
- **Sort**: one or more keys, each up or down; items with no value go last.
- **Filter** (`f`): rules such as "Tag has any of #home", "Due date is in the next 7 days", "Assignee is me", combined with all/any and nested ("high priority **and** (tagged home **or** assigned to me)"). Only complete rules count; an unfinished one says **Incomplete — choose a value** and changes nothing. **Me (current viewer)** follows whoever opens the view; a named person always means that account. **Clear filters** removes them all.
- **Subtasks** shows subtasks as their own small cards, "Subtask of …" above the title and a thin line to the parent card; hover either to light the line up. **Closed** brings back items finished on earlier days. **Me** shows only what is assigned to you.
- **Customize**: which details cards show, title lines, compact cards, fold or hide empty columns, how many days the due board names, and the theme.
- **Search** (`/`) narrows the view; it is not saved with it.

**Moving a task to another list.** Pick **Move to…** in a card's ⋯, press **Move** in the task window, or click its **List** field. Type part of a list or section name, use the arrow keys and press Enter. The task lands in the new list's column of the same kind, its subtasks move with it, and **Undo** puts it back.

**Selecting many.** Tick a card's box (or press `x` over it); shift-click selects a run; on a phone, hold a card and pick **Select**. A bar appears with **Status** (each task gets its own list's column of that kind, all or nothing), **Due**, **Priority**, **Tags**, **Assign**, **Move**, **Put away** and **Trash**; the last two ask once. **Undo** brings a trashed selection straight back. `Esc` or **×** clears the selection.

**Monitor mode.** Add `?kiosk=1` before the `#` (`https://slate.example.com/?kiosk=1#/everything`) for a wall screen: no menu or toolbar, bigger type, every column fitting the screen, a clock and a live dot. The pointer hides after three seconds still.

**Keyboard.** `Ctrl K` (⌘K) search everything · `/` search this view · `c` quick add · `b` board or list · `f` filter · `j`/`k` move between tasks · Enter open · `e` complete · `x` select · `s` star the open task or the card under the pointer · `Ctrl Z` undo · `g` then `h` My Work, `i` Inbox, `e` Everything, `m` Chat, `p` Docs · `Esc` close · `?` lists them all.

## Dependencies, estimates, duplicates and tags

**Dependencies.** **+ Add dependency** in the task window links it to another item: *Depends on* (this cannot start until that is done), *Blocking*, *Linked to* or *Duplicate of*. (*Waiting on* is always a person, the task field of that name.) A task that depends on something still open shows a red **Depends on** pill (hover it for the list), a banner in its window and a **Depends on** box on My Work. You never clear it: when the last thing it depends on is done, cancelled, put away or unlinked, it clears everywhere at once and the assignees get **Ready to start** in their Inbox. Reopening a finished blocker blocks it again. **Filter → Depends on an open task** finds them.

**Duplicates.** When the same thing was filed twice, add **Duplicate of** on the copy: its card shows a *Duplicate* badge and the original lists it under *Duplicates*. Put the copy away when you are done with it.

**Time estimate.** Every item can carry an estimate (`90`, `45m`, `1.5h`, `1h 30m` or `2d`). It is a plan, not time tracking.

**Tags.** **Tags** at the bottom of the menu opens the tag manager: how many tasks and docs use each tag, a colour, rename, **Merge** one tag into another (every task and doc moves over) and **Delete** (it comes off every task and doc; they stay). Tags are one list for the whole workspace, so only the owner or an admin recolours, renames, merges or deletes one; anyone can make a tag and put it on a task they can edit. A guest sees only the tags on tasks and docs they can reach, plus any they made.

## Favorites bar

The thin bar across the top holds your favorites: one click opens a list, a section, a saved view or a task. Star a list or section with the ☆ that appears when you hover it in the menu (or **Add to favorites** in its ⋯, or drag the list onto the bar). Pin a view from its tab's ⋯; a pinned view shows a small ★. Press `s` to star the open task or the card under the pointer. Drag chips to reorder them; hover a chip and click × to remove it. Favorites are yours alone. Archiving a list or view hides its chip until it is restored. On a phone the bar is one sideways-scrolling row, hidden when you have none.

## Search, My Work, Needs you and Inbox

**Search everything.** Press **Ctrl K** (⌘K on a Mac), or tap ⌕ on a phone. Type part of a task, list, view or tag. After two letters Slate also searches task descriptions, doc pages, the text inside files, and chat, grouped under **Tasks**, **Docs** and **Chat**: a doc opens on its page, a chat hit opens its thread. Start with `>` for commands (go to Inbox, dark mode, undo, manage tags, health), `#` for tags. If nothing matches, the last row creates the task.

**My Work** (`g h`) says what to do next: **Needs you** first, then your tasks by due date, what depends on another task, what you handed to others, your newest notifications and what you finished this week.

**Needs you** is the cards that cannot move until you act. Every page uses the same four words for why:

- **Waiting on Alex** (your name): a person has to answer, usually because an agent needs your decision. Elsewhere it names whoever it waits on. The card has a red ring and a red **Waiting on Alex** chip. In the task window the question sits in a red box: **Answer** jumps to the message and puts you in the reply box (your reply goes to that agent and the card stops waiting); **Not waiting on me** clears it when there is nothing to answer.
- **Agent blocked**: an agent on your card stopped and named nobody. A run that only ran out of steps reads this way too; nothing is asked of you, and **Run now** in the red box carries it on.
- **Asked of you**: an open question addressed to you.
- **Ready for review**: an agent says the work is done.

*Depends on*, agents still working and overdue dates are not counted. **The number** on My Work in the menu, the pill (bottom right on a computer, top bar on a phone), the window title **(3) Slate** and the installed app's taskbar or dock icon is this same count. Click the pill for the list. Each list in the menu shows a small count when it holds cards that need you. Unread chat has its own badge on **Chat**; overdue and due today are the badge on **Everything**.

**Inbox** (`g i`): mentions, questions, assignments, reminders, due nudges and changes on tasks you are assigned to or watch, from people and agents. On a computer the bell at the top right shows **@** and a count when someone mentioned or asked you something, and a chat bubble with a count for unread replies and chat; click it to jump straight there or mark everything read. Opening a task marks it read; **Clear** removes it.

**Undo.** **Ctrl Z** puts back the last change you made on the board (status, dates, priority, moves, put away, bulk edits, a task you just added). Completing, putting away, trashing and moving also show **Undo** next to a sentence that says what happened.

## Notifications and reminders

**Reminders.** **Remind me** in the task window sets reminders; a task has none until you add one. For a task with a due date, **Before due** switches on as many alerts as you like: at the due time, 5, 15 or 30 minutes, 1 or 2 hours, 1 or 2 days or a week before, or any other amount. Below that are one-off times (in 20 minutes, in an hour, this evening, tomorrow morning, next Monday, or any date and time). You can add a note, and remind someone else if you can edit the task. Reminders show as chips under the task's fields; × removes one. A before-due alert follows the date if it moves; a task due on a day without a time counts as due at 5 pm, so "1 day before" is 5 pm the day before. Each new copy of a repeating task gets the same before-due alerts. A reminder lands in your Inbox and on your devices.

**Your day.** Every morning Slate sends you what is overdue, due today and waiting on you, with what the agents will do today. Nothing comes on an empty day. A digest follows what you can see now: a task no longer shared with you drops out, even from one already queued.

**Notifications** (the bell at the bottom of the menu, or search "notifications"):

- **Turn on for this device** makes this computer or phone show Slate's news even when Slate is closed (the browser must be running). Tapping one opens the task. Do it once on each device; **Send a test** checks it. On iPhone this works only in the installed app, on iOS 16.4 or later. On a Mac the browser also needs macOS's own permission: if pushes arrive silently, the panel after a test says where to look (System Settings → Notifications → your browser → Allow; turn off Focus).
- **Tell me when** lists each kind of thing (mentions and questions, replies, new messages in chats you can see, assignments, someone waiting on you, due dates, a task no longer blocked, reminders, your day, tasks you follow or watch, and Slate alerts for owners and admins) with a box for each way to be told: **Toast / push** and, when the server has email set up, **Email**. Type your address under the grid; **Send a test email** checks it reaches you. An email goes out only if you still have not seen the thing in Slate five minutes later, and a burst arrives as one email. A plain chat message only notifies; the channel's unread badge is its record. A busy channel replaces its own notification instead of stacking them.
- **Nudge me again about chat I still have not read**: one more push, **Still unread: #Example Project**, if a message is still unread after 5, 10 (the default), 30 or 60 minutes, or never.
- **Daily digest**: on or off, and the hour it comes. **Upcoming reminders** lists everything you will be reminded about.

These choices follow your account to every device. While Slate is not the window in front and chat is unread, the window title flips between **(n) Slate** and **New in #Example Project**, so an installed app's taskbar label changes too.

## Slate on your phone

Slate works in the phone's browser and installs to the home screen like an app. The phone must reach Slate's address over HTTPS; if your server is only on a private network (a VPN, say), the phone needs that network too.

- **iPhone:** open Slate's address in **Safari**, tap **Share**, then **Add to Home Screen**. Open Slate from the new icon and paste your token once: the installed app keeps its own sign-in. A guest pastes an invitation link made for this device into the same box.
- **Android:** open the address in **Chrome**, tap **⋮**, then **Install app**.

☰ opens the menu, swipe sideways through the columns, tap a card to open it full screen. New versions load by themselves.

**Sharing into Slate** (Android, installed app): tap **Share** on a web page, video or message and pick **Slate**. Quick add opens with the page's title, the link saved into the description (*drop the note* leaves it off) and the list you used last. A link shared on its own becomes "Read example.com/…" (or "Watch …" for a video). On iPhone, paste the link into quick add instead.

**Touch.** Press and hold a card, row, checklist item, favorite, list or page until it lifts, then slide it where it should go; near an edge the screen scrolls along. Hold and let go without moving for the card's menu. Swipe a row on My Work or in the List layout right to complete it, left to snooze it to tomorrow (with Undo). Pull down at the top of a page to refresh. Without signal, a grey bar says you are offline; changes made then are not saved, and Slate says so.

## Chat

**Each section and each list has its own chat**, and **General** is for every member. **Chat** in the menu (`g m`) shows them all with unread counts; a red `@` count means someone mentioned you. A list's or section's **Chat** tab, the chat icon by a section in the menu and a project home's **Open chat** open that one.

**Chat opens beside the page you are on.** On a computer the chat opens in a panel on the right and the page underneath stays where it was; on a doc the page sits at the left, so the panel covers none of it. The panel stays open as you move between pages and after a reload. At its top, the channel name is a menu to switch chats; the buttons beside it make the panel wider, fill the window below the top bar, or open the chat as a full page (`#/chat`, where **Panel** puts it back); × closes it. Drag the panel's left edge to resize it (double-click to reset). A thread opens inside the panel. A link with `?chat=<channel>` opens the panel on that chat. On a phone or a narrow window, chat is a page of its own.

- **Write** in the box at the bottom: Enter sends, Shift+Enter starts a new line. `@` mentions a person or an agent (it reaches their Inbox and devices). `#` and a few letters links a task (shown as a card with status and due date), a page or a list. Someone who cannot see a linked page sees only "a page".
- **Hover a message** (on a phone, the actions sit under it) to react, **reply in a thread**, **turn it into a task**, copy a link, or edit or delete your own. The owner and admins can also delete anyone's message (a mistaken post, a pasted password); only the author edits one. Everyone who wrote in a thread hears about new replies.
- **Turn into task** files one task in the channel's list (or one you pick), quoting the message and bringing its files as attachments; the message then shows the task's card.
- **Send a screenshot or a file**: paste (Ctrl V), drag a file onto the box or click the paperclip, then press Enter (a file can go on its own). Up to 10 files a message, 25 MB each. Pictures show in the message, other files as a chip; click to view full screen (← → steps through them) or download. A file you add but never send is removed after a day, and you can hold at most 20 such files (100 MB) at a time. Whoever can read the message can open its files, and no one else; deleting the message deletes them.
- **Read and unread.** A message counts as read only when it has been fully on screen for a moment in the focused window; a channel left open in the background stays unread, as do thread replies you have not opened. **First unread** jumps to the oldest unread message or thread. **Mark all read** catches up on purpose. A very tall message has **More actions → Mark this message read**. Read state is yours alone: nobody sees a "seen" receipt.
- The mention picker is a directory, not proof that the person can see the channel, and a mention grants no access.

### Agents in chat

**@mention an agent and it answers there.** An agent whose runner Slate can start (wake mode `push` on the Agents page) is started by your mention, and its reply appears in the same channel or thread. While it is on the way, a quiet line under the last message says **My Agent will answer…**, then **My Agent is working…** until the reply lands. One run at a time per place: a second mention waits for the next turn. If a run ended without an answer, the line says **My Agent stopped answering** with **Clear**: Clear ends that turn and any mention that waited goes back to the agent; mentioning it again also asks once more. Agents never start each other this way, and an agent that cannot post in that place is not started (its unanswered mentions there are dropped when its access is lowered).

**Chat with an agent, no card needed.** The blue-green **Agents** button with a robot at the top right (in the top bar on a phone) lists your agents; pick one to open its own chat. For a workspace agent, everything you write there reaches it without typing `@`. A project's own agent (a guest-role agent) answers only when you type its `@handle`; plain follow-ups, threads and mentions of other people do not start it, and adding its mention in an edit does. The robot shows a red count when an agent answered and you have not read it. Agent chats are also on the Chat page under General; a disabled agent's chat disappears.

**Who reads an agent's chat.** Owners, admins and members read the chat of every workspace agent. A project's own agent works in one project only, and its chat belongs to that project: the project's guests and the members who can see the project read it and talk to it there, and nobody else does. What you say to a project's agent in its chat, that project's guests read too. Slate refuses to add a project's agent to a second project; add another guest agent for that one.

**Your workspace agents stay out of a guest's sight.** A project guest sees no trace of a workspace agent such as `my-agent`: not its comments or your Asks to it, its working badge or session link, its name among assignees or in history, its page comments, or in any @ or Ask list. A guest cannot ask, assign, run or wake it. On a card assigned to it, a guest cannot change the title, description, *Done means*, checklists or dates, or reopen the card; they can still comment, set status, priority and tags, and attach files. The cards it made stay, because they are the work. In a shared project's chat, the workspace agent's messages, your messages that mention it, and every reply in such a thread are marked **· hidden from guests** on your screen and never reach a guest (not in the chat, an unread count or a notification); the project's own agent cannot read them either. Adding a workspace agent's mention to a message by editing hides it from then on.

## Docs & Files

**Docs & Files** under **More** (`g p`) holds pages and files next to the work: plans, meeting notes, runbooks, and the PDFs, images and spreadsheets a project runs on. A page or file lives in the Workspace, a section or a list, and pages nest inside pages; a page with things under it is the folder. One tree holds every page and file you can see.

**Who sees a document.** A document in a list or section is seen and edited by whoever can see and edit that list or section (its subpages too); documents at the Workspace level are for members, not guests.

**Finding and sorting.** Chips above the tree narrow it to **Pages** or **Files** (and files to PDFs, Sheets, Images, Text, Other), or to a tag. The sort menu orders every level by tree order, latest change or name. **Ctrl K** finds pages by title and text, and files by name and by what is inside them.

### Pages

- **New page** (or the + by a place in the sidebar, or a list's ⋯) starts blank or from a template: meeting notes, project plan, decision record, runbook, agent log.
- **Click anywhere and type.** The page stays formatted while you write, like a word processor; you never see Markdown, and it saves as you type. Use the toolbar (Ctrl+B, Ctrl+I, Ctrl+K), or select text for a small bar with Bold, Italic, Link, Create task and Comment. Type `/` for headings, checklists, tables, quotes, code, dividers, images, a task card, a new task or a link to another page; or start a line with `# `, `- `, `1. `, `[] ` or `> `. Tab moves between table cells. Paste or drop an image to upload it. While typing, Ctrl+click (⌘-click) opens a link. Agents read and write the page as Markdown, and the parts you did not touch stay exactly as they wrote them.
- **The page sits at the left** at a reading width, leaving room on the right for the chat panel.
- **Tasks on a page are live cards** showing status, due date and assignees; tick one to complete it, click to open it. Select text and press **Create task** to file one task in a task list; the text becomes its card. The task window lists the pages that mention a task under **In docs**.
- **Facts.** Start a page with a fenced block tagged `facts`, one `key: value` per line (`Policy number: 12345`, `Renews: 2027-03-01`), then write below it. The page shows those lines as a table above the text, search finds the values, and agents read them as fields. It is still an ordinary page with history and discussion. A **Records** drawer suits warranties, policies and account numbers.
- **Page history** (the clock) keeps one version per sitting of edits, with what changed and a **Side by side** view. **Restore** brings an old version back as a new one; **Name this version** marks a checkpoint.
- **Organize**: drag pages in the sidebar to reorder or nest them, or ⋯ → Move. Pages can be favorited, duplicated, copied as Markdown, downloaded and tagged (under the title). **Archive** hides a page and its subpages; restore or delete them from **Archived pages** at the bottom of the sidebar.
- **Someone else changes the page while you type**: a banner offers **Take theirs**, **Keep mine** or **Download my draft**. Unsaved text is kept in this browser per account and page across navigation and reloads; a recovered draft asks before it overwrites. Signing out never carries your draft to the next account.
- `@` on a page mentions someone; they get it in their Inbox and on their devices.

On a phone the page list comes first and a page opens over it.

### Files

**Upload** (top of the sidebar, the home page or the New page sheet) puts a file in as a document of its own, up to 25 MB. It opens like a page: the title, a viewer (PDFs, images, Word files as a page, Excel workbooks as sheets, text with line numbers, CSV as a table; HTML, SVG, XML and anything else as a download card) and **Notes about this file** underneath. The sidebar shows a file by its type with its revision number (`r3`).

- **Revisions.** A file is never replaced; it gets its next revision: **Upload new revision**, or drop a file onto the page. You say **what changed** in one line, shown under the file (`Rev 3: Updated the totals on page 2`). The old revision stays.
- **Revisions panel** (the clock): every revision with who, when, size and its note. Click one to view it (*Viewing revision 2 of 4*), **Download** it, or **Make current** to bring it back as a new revision. Nothing is ever deleted.
- **Compare with current** shows what changed the way the type allows: text side by side or inline; a spreadsheet or CSV with each changed cell painted (old struck through, new after it, added rows green, removed rows listed), one tab per sheet; a PDF or DOCX by its words and numbers, labelled approximate; two photos with a swipe slider; anything else as the two revisions and their notes. The From and To pickers compare any two revisions.
- **Search inside files.** Text, spreadsheets (formulas too) and the text of PDFs and DOCX files are read on upload. A very large one (past the server's limit, 64 MB unpacked by default) still uploads, opens and downloads, but is not searchable or comparable, and its row says **not searchable**.

### Drawers

A section holds three kinds of thing: **lists** of tasks, **drawers** of documents, and **pages**. A drawer is where you keep the documents you and your collaborators actually reference: Contracts, Notes, Meetings. You make one on purpose (the + by a section, then **Drawer**, or **New drawer** on a project home) and put things in it on purpose; nothing is gathered from elsewhere. A drawer takes no tasks.

- **The Files table**: folders and documents with name, **created** date, revision strip, last change and its note, tags and open threads. Chips narrow it by type or tag; **Filter this folder by name** matches names in the current folder (use search for contents). **Upload**, **New page** and **New folder** add to it, and a file dropped anywhere on the page uploads into it. A folder opens in place with a breadcrumb; the folder button on a row moves it. **Pin to top** keeps a row first in its folder for everyone (📌). **Created** reads a date from the file's name when it has one (`2026-10-30 - Project brief.pdf`, `Budget 10-30-26.xlsx`); the upload time stays on the revision.
- **Reading**: click a document and the drawer's files sit on the left, the document in the middle, discussion alongside on a desktop (on a phone, the speech-bubble button opens it). **All files** goes back.
- **Pinned revision**: in the Revisions panel, **Pin as in use** marks the revision actually in use, with a note. It shows as an amber dot in the table and under the file's name, so a newer upload is never mistaken for what is in use. One per document; **Unpin** removes it.
- **Converting**: a page with subpages becomes a drawer from its ⋯ (**Turn into a drawer**). List settings' **Kind** turns an empty task list into a drawer or back.

A private section's **project home** lists its drawers with document counts, its pages, and **Needs you**: Asks addressed to you and review threads others opened on documents you touched.

### Discussion and review

The speech-bubble button on any page or file opens the **Discussion** panel (its badge counts comments, or open review threads). Two tabs:

- **Discussion** is the running conversation about the document: `@` mentions someone, **Ask** addresses it to one person or agent and stays open until resolved. Small grey lines between the comments say what happened to the document (a revision, a rename, a named version, a resolved thread). Each comment is stamped with the revision or version it was written on (`on rev 3`), and the stamp turns amber when the document has moved on.
- **Review** pins a thread to a spot. On a page, select text and press **Comment**; you can add a **suggested replacement**, and **Accept** applies it as an ordinary save and resolves the thread. A suggestion applies only to the unchanged text it quoted; if the page changed or the quote appears twice, edit by hand. On an image, click the spot for a numbered pin. On a PDF, comment on a page number. Threads have **Reply**, **Resolve** and **Reopen**; resolved threads hide behind *Show resolved*. A thread from an earlier revision folds as **Outdated**.
- Replies notify the asker and earlier participants; a reply does **not** close a thread, only **Resolve** (or accepting a suggestion) does. A notification opens the document on that thread.
- **@mention an agent on a page** (in the text, a review thread or Discussion, or Ask it) and an agent whose runner Slate can start reads the part of the page around your mention and replies as a comment, in that thread or as a new Discussion comment. Nothing else happens: no task is filed and nothing on the board moves. The same one-turn-at-a-time rules as chat apply.
- Closing Discussion keeps unfinished comments, replies and review fields in this browser per account and document.

## Recurring tasks

A recurring rule makes a fresh item on a schedule: the bulk trash every other Wednesday, the filter change on the 1st. Tell an agent in plain words and it writes the rule, or set it yourself. Each item it makes is a normal item; finishing one never changes the rule.

**From the board.** Open an item and press **↻ Make recurring** (next to Repeats). Pick how it repeats:

| Choice | For example |
|---|---|
| Daily, every N days | water the plants |
| Every weekday (Mon–Fri) | stand-up notes |
| Weekly on chosen days, every N weeks | bulk trash every 2 weeks on Wednesday |
| Monthly on dates (or **Last day**), every N months | rent on the 1st |
| Monthly on a weekday: 1st/2nd/3rd/4th/last | the 2nd and 4th Wednesday |
| Yearly on a date, or on a weekday | March 15; the 2nd Sunday of May |
| Custom (RRULE) | anything else, as an RFC 5545 rule |

Then choose when it **ends** (never, after a number of times, on a date), what each new one is **called** (`{date}` adds its date: "Bulk trash {date}" becomes "Bulk trash Wed Nov 11"), who each is **assigned to** (the rule keeps its own list, starting as this item's assignees), what happens **if one is missed**, and how many days **ahead** each appears (two weeks by default). The box shows the rule in plain words and its next five dates as you choose. A rule Slate cannot use says why before you can save it, including one that matches no day at all, such as February 30.

- **On a schedule** (the default) makes one for every date, done or not. **When done** makes the next only when you finish this one, counted from that day, so "every 3 months" means three months after you last did it. With *When done*, **Reopen this task** reuses the same item: it goes back to open with the next date and its checklist unticked.
- **If you miss one**: *still create the next one* (the default; the missed one stays overdue), *move it forward* (the missed item's date jumps to the next date), or *skip while one is open*.
- A rule with no time of day makes items due at 5 pm workspace time; daylight saving never moves a given time.
- **Skip weekends** moves a Saturday or Sunday date to the Monday after. **Carry checklists over** (on by default) gives each new item the last one's checklists, unticked. Before-due alerts always carry over.
- The item you started from stays the current one with its own date; the rule makes the next ones. On an item that repeats, the Repeats line has **Edit** (future items only), **Pause** / **Resume** (resuming starts from today without catching up) and **End** (final; items already made stay).
- Cards show what repeats (**↻ Weekly**, **↻ Every 2 weeks**, **↻ Monthly · when done**; hover for the whole rule).

What you say, and the rule an agent writes (the start date anchors "every other"):

| You say | `rrule` | `dtstart` |
|---|---|---|
| "Bulk trash every other Wednesday, next pickup Nov 11" | `FREQ=WEEKLY;INTERVAL=2;BYDAY=WE` | `2026-11-11` |
| "Weekly review every Friday at 3 pm" | `FREQ=WEEKLY;BYDAY=FR;BYHOUR=15` | today |
| "Monthly audit on the last weekday of the month" | `FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1` | today |

## Agents at work

### Giving work to an agent

- **Hand to an agent** (🤖, by the comment box): pick the agent and say what it should do. Slate assigns the agent and posts your words as a comment that mentions it, in one step.
- **Ask someone** without handing them the task: pick a person or agent in the **Ask…** menu beside the comment button, then send. The question shows an "Asked … · Open" pill, lands in their Inbox, on their devices and under **Asked of you** on My Work, and an agent with a runner is woken to answer. A card with a question waiting for you shows a small orange "? 1".
- **Asks stay open until someone presses Resolve** (the asker or the person asked). A reply such as "I need more time", completing the task or reading the notification does not settle it. An agent that was only asked (not assigned) closes the questions it answered when it says it is done.

The **Ask** picker starts on the card's agent (on a shared project's card, on that project's own agent), else on the agent you asked last. Typing an agent's `@handle` switches the picker to it unless you picked someone by hand. A comment that mentions only people is a plain comment and wakes no agent; pick **No one (comment)** for a plain note.

### When agent tasks run

A task assigned to an agent with a runner starts on its own: at once if it has no due date; at 6:00 in the morning on the due day if the due date has no time; or at the due time. **Run now** starts it sooner: the robot button on the card (hover), the card's ⋯, or by the comment box. It is offered when an agent is assigned and the task is open. The card shows **Waiting for pickup…** until the agent reports working. Each agent runs several cards at once, up to its runner's limit, and the rest wait their turn. A task requested while the runner is off waits for it to reconnect. If nothing picks it up, the card says **Not picked up yet. Check agent status.**, or **No live connection seen…** when the agent has not connected recently; both link to Agents. **Run now** on a card the agent is already working says so ("My Agent already has it, working 4 min"); on a card waiting on you, it tells the agent to go on without an answer.

### Following the work

The agent reports as it goes: working (with how far along), blocked, ready for review or done. The report is a small badge on the card, "🤖 My Agent · Working 40%", and in full at the top of the task window with its note. A working badge pulses and fades if nobody refreshes it for six hours; blocked is red (**Waiting on Alex**, or **Agent blocked** when it named nobody); ready for review is amber. Done clears the badge and completes the task. Each report is in the history, and its note is a comment from the agent.

- **Later.** When the agent has done what it can and the rest waits for the due time (you already gave it the go), it says so in its comment and leaves no badge: the card is not waiting on you, stays assigned, and the due time runs the agent again.
- **Paused.** If a run stops without saying where things stand, the card waits on you with a "Paused" note; reply on the card to let it carry on.
- **A run that lost contact.** If the agent stops checking in, Slate does not start the task again blindly: the badge turns blocked with *Execution lease expired*, because the run may still be going. Check that it really stopped, then press **✕ Clear**; the agent can pick the card up again.
- **Ready for review** clears only when you press **Mark reviewed** on the badge or change the card's status yourself; opening and closing the window does not.
- **✕ Clear** on a blocked badge takes it off the card; it leaves the card's *Waiting on* field alone.
- **Open ↗** on the badge goes to where the agent says it is working (its chat, session or pull request). After the run, the task window keeps an **Open My Agent's session ↗** line for every agent that worked the card.

**Talking with an agent on the card.** An agent with a runner answers on the card: each run's reply is a comment, and Activity reads as a chat (older exchanges fold to one line). While it works, a bubble at the top shows what it is doing and when it last reported; three bouncing dots say "My Agent is typing…". When it needs you (a sign-in, a decision), the badge says **Waiting on Alex**, the card joins **Needs you**, and your phone gets "My Agent needs you on: …". Answer in the comments: your answer becomes its next turn and the badge says **Answered** until pickup. A follow-up question wakes it again even when the card is Ready for review or done. An agent you only asked something on another person's card never marks that card blocked on someone else.

**Command approval is not a comment.** When an agent's own application asks to approve a command, approve it there; typing "yes" or "approve" on a card authorizes nothing.

**Show an agent a screenshot**: paste it into the comment box. The agent is told which files are on the card and can open them.

**Agents in other apps.** Some agents live in another company's app and Slate cannot start them. On the Agents page an admin sets how each agent is woken: **a runner** (Run now), **its own app checks Slate** (the card says **Nudge**: you get a notification to open that app; the Agents page has the prompt to save as a scheduled task there), or **by hand** (the card says **Copy brief**: the task as a prompt to paste into the agent's app). Paste the agent's answer back as a comment. A last line `SLATE: done` completes the task; `SLATE: blocked waiting_on alex` marks it waiting on Alex. Details: [docs/agents/pull-agents.md](docs/agents/pull-agents.md).

### The Agents page

**Agents** under **More** shows every agent with a green dot when it is around, what it is on, how many open tasks it has, how busy it has been today, and a live feed of everything agents did (**Feed** on an agent narrows it to that agent). **Next up** above the feed says what each agent will do in the next 24 hours, soonest first: runs due, a **Run now** you pressed, questions asked of it, and the tasks it holds now. It follows the same rule agents use to pick work, so a task missing from it will not be picked up either (blocked, waiting on someone, or not due yet). Your morning digest carries the same list as **Agents today**.

## Automations

Automations are rules that do routine work: *when* something happens to a task, *if* some things are true, *then* Slate does one or more things. An agent sets them up for you, or you use the API; the board has no automations page.

- **When:** a task is created, its status changes, someone is assigned or unassigned, a tag is added or removed, its priority changes, its due date arrives or passes, a comment is added (or one that mentions someone), it is moved into the list, it is unblocked, or an agent reports a new state (working, blocked, review, done).
- **If** (optional): priority, tag, assignee (or nobody), status, kind of status or list is or is not something.
- **Then:** change the status, set the priority, add or remove an assignee, add or remove a tag, move it to another list, set the due date some days out, post a comment, archive it, or call a web address.

A rule can be switched off, run on one task straight away, and edited, and its run log shows every time it fired and what each step did. The **Health** page lists runs that failed in the last day. A change an automation makes shows in the task's activity as "by automation" with the rule's name. An automation's own changes never set off other automations.

## Sharing a private project

Sharing lets one outside person (a client or a collaborator) into one private section of your Slate: its lists, drawers, pages and chat, and nothing else. They sign in from a link you send them, with no account to create and no email involved.

**Invite.** Open the section → **Share & invite**, make it private, and fill **Guest name** and **Guest handle**; the invitation is **Guest · Edit**. Press **Create invitation link**, copy it and send it privately yourself. The link expires in seven days and can be accepted once. Anyone holding it can accept it, so never post it publicly. No email is sent and no agent is created.

**Project home.** A private section opens on its project home: its lists (make **Work** or **Ideas** lists as needed), drawers, pages, discussion, participants and an optional whiteboard link (the whiteboard keeps its own permissions). It also shows your tasks, open Asks and work waiting on you in that project. Guests can create and edit lists in the shared section, but cannot create sections or manage memberships.

**While they are in.** A guest sees everything shared with them: share a second section later and it appears at once. Access ends 30 days after they accepted; their menu says **Access ends <date>**. Three days before, both of you get an Inbox entry and a notification, and **Extend 30 days** in the invite panel pushes the end out from today. **Copy a link for another device** makes a fresh single-use link for the same person: every phone, laptop or installed app needs its own, opened in Safari or Chrome, not inside a messaging app (that uses it up). In an installed app the guest pastes the link into the token box.

**Ending access.** **Cancel invitation / session** cancels that link and the browser session it issued. To remove project access, also remove their share; to offboard fully, disable the account in Members. Nothing can recall what was already downloaded or a notification already delivered.

**If an invitation fails** (incomplete, expired, revoked or used), ask the person who invited you for a fresh link for that browser. For a connection or server failure the screen suggests retrying; the link is still good.

More detail: [shared-project collaboration](docs/shared-project-collaboration.md).

## Members, sharing and access

**Members** (owners and admins) lists every person and agent with their role, what they can reach and when they were last active. There are four roles: the **owner** and **admins** see and change everything; **members** see every section and list that is not private; **guests** see only what is shared with them. Change a role from the dropdown. Only the owner makes someone an admin, and only the owner issues tokens for, renames or disables an admin or the owner. Choosing **Owner** for someone hands ownership over (you become an admin). **Invite** makes a person or guest and shows their sign-in token once. **Access** on a row shows, list by list, what that account can do and lets you share more.

**Admin powers need an admin token.** An owner or admin acts as an admin only through a token with admin scope that is not limited to some lists. Signed in with a write token, or a list-limited one, the same person acts as a member.

**Share** at the top of every list and section (and in its ⋯) shows who reaches it and why, adds a person or agent at **Can view**, **Can comment** or **Can edit**, and has the **Private** switch. A private list or section disappears for members unless shared with them, and shows a lock in the menu. With view or comment access you see a note at the top of the list saying what you can do. Changes reach open boards at once: unshare a list and it vanishes from that person's screen.

**Agents follow the same rules.** An agent can be a guest, and its token can be narrowed to **only some lists** at most view, comment or edit; that token cannot see or touch anything else.

**Agents may not…** at the top of Members is one rule for every agent, whatever its token or role: tick what no agent may ever do here, and it is refused with "agents may not … here (workspace rule)". Out of the box agents may not delete things for good (purge, empty the trash, delete a doc, a file, or a task attachment it did not attach itself), archive sections or lists, change sharing (shares, invitations, roles, the Private switch), or issue tokens and manage accounts. You can also stop them trashing tasks, changing automations and webhooks, or exporting. Each box saves as you click it and is in the audit log. People are never limited by it, and Slate's own housekeeping carries on.

## Archive, trash and export

**Archive** under **More** is where work goes when it leaves the board: **Done** (finished and cancelled tasks, newest first), **Put away** (**Bring back** returns one), **Lists and sections** you archived (**Restore**), and the **Trash**.

**Move to trash** is in a task's ⋯, a card's ⋯ and search. The task and its subtasks leave every board at once; **Undo** in the toast brings it back. In the trash, **Restore** puts it back where it was, and **Delete forever** removes it after you confirm. Anything left in the trash is deleted after 30 days (the administrator can change this); each row says how long it has left. Admins can **Empty trash**. A trashed task can still be opened, with a red banner and Restore. Its history stays even after it is deleted.

**Export JSON** and **Export CSV** at the top of the Archive page download everything you can see; a list's or section's ⋯ exports just that. JSON keeps everything (comments, checklists, links, docs, and the chat of what you export) for a program to read; CSV is one row per task for a spreadsheet. Admins also get **Backup**, a copy of the whole database. A server installed with the deploy pack also backs itself up every night.

## The audit log

**Audit log** under **More** is every change ever made, newest first: who did it (person or agent), when (grouped by the workspace's day, like every other page), through which door (the board, the API, MCP, or Slate itself) and what it touched, with the before and after under **Details**. Filter by person, people or agents, kind of change, door, section or list, dates, or a word in the task title; **Export CSV** downloads what the filters show. Nothing in it can be edited or deleted. Owners and admins see everything; others see the history of the lists they can read and their own actions. An account's own settings, devices and tokens are visible only to it and to admins.

**Revert.** A change someone else made to a task (title, a date, priority, description, tags, status, assignees, a move) has **Revert** on its row, here and on the task's History tab. It asks first, then puts the field back as a new change by you; both changes stay in the log. If the field changed again since, Slate refuses and the button reads **Changed since**. Creations, comments and reports have no Revert.

## Health

**Health** under **More** (owners and admins; also Ctrl K → "health") says whether Slate is working. The top line is the verdict; below it every check, problems first: **Background jobs** (reminders, the digest, due nudges, recurring tasks, trash purge, with when each last worked and any error), **Agents and tokens** (each token's calls in the last hour, failures and slow calls), response times, failed automation runs in the last day, queues, database size and free disk. On a server installed with the deploy pack, a **Backups** line shows when the nightly backup last worked; it fails when the last backup failed or none has worked for 36 hours.

If a job fails three runs in a row you get a notification, and another when it works again. Anyone who can reach the server can read the short verdict at `/health`. The numbers start over when Slate restarts.

## Connecting agents

**Start here:** [Connect your own agent](docs/connect-your-agent.md) walks through **Agents → Add my agent**. Choose local execution (a runner on your computer), MCP or API access, or manual hand-off; they are different connections, and a token alone does not make an agent available to run. The connection panel tells apart an unused code, connected credentials, a passed agent test, an offline computer and a setup problem.

- **Every agent gets its own account and token.** **Add my agent** names it, sets what it may do (Read, Write or Admin; Write is usual) and labels the token with the machine that holds it. Slate shows the token once, with setup ready to paste into an MCP client, a Python or TypeScript agent, or a `curl` check. Slate keeps only a fingerprint, so a lost token is revoked and replaced, never recovered.
- **Manage** on an agent's card shows its tokens and when each was last used, how many changes it made each day for two weeks and through what, and lets you make or revoke a token or disable the agent (all its tokens stop at once; its history stays).
- **How agents pick work.** An agent asks Slate for its next task and gets the most important one it can do now: assigned to it, not waiting on an unfinished task, not taken by another agent, by priority, then due date.
- **Limits.** By default each token makes up to 1,200 reads and 300 changes a minute, and holds at most ten live connections at a time. A token narrowed to some lists cannot subscribe a webhook.
- **History** shows an agent's changes "via MCP" or "via API", whichever it used.

The very first account is created once, on a fresh install; [deploy/server/RUNBOOK.md](deploy/server/RUNBOOK.md) and [docs/agents/auth.md](docs/agents/auth.md) show how.

For developers, [docs/agents/](docs/agents/README.md) has the quickstart, MCP setup for each kind of client ([mcp.md](docs/agents/mcp.md)), the REST and MCP reference, the live event stream ([events.md](docs/agents/events.md)), the runner ([runner.md](docs/agents/runner.md)) and webhooks, the signed calls Slate makes when something happens ([docs/webhooks.md](docs/webhooks.md)). The server's `/docs` page is the interactive API reference.
