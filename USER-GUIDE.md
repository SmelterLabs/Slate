# Slate — User Guide

Slate is your task board. Agents file, update and complete the work; you look at the board and steer.

## The shape of things

- **Sections** are the big separations, for example Personal and Work. A new section is for a different audience or rhythm, not for a topic.
- **Lists** live inside a section (Todo, Read / Watch, Projects…). Each list has its own statuses, which are the columns on its board.
- **Items** are tasks. (Documents are not items: pages and files live in **Docs & Files** and in a section's drawers, below.) An item can have subtasks, tags, assignees, a priority, start and due dates, comments, attachments and a full history of who changed what.
- **What an item can hold:** a title; a markdown body (every edit is kept as a version); a status from its list; a priority (urgent, high, normal, low, or none); start and due dates; who it is assigned to (people or agents); who it is waiting on (for example you, when an agent needs your decision); what "done" means, for agents; tags; subtasks, which nest four deep (a subtask of a subtask of a subtask of a subtask); comments; and its full history. Filing an item never makes subtasks; an agent splits an item only when asked.
- **Dates:** a due date given as just a day (`2026-11-01`) means 5 pm that day in the workspace timezone (the server's `SLATE_TIMEZONE` setting).
- **Three ways off the board, each said when you press it:** **Complete** means done: it stays on the board until midnight, then sits under Archive › Done, still searchable. **Put away** takes it off the board with its history kept; bring it back from Archive. **Move to trash** deletes it for good in 30 days unless you restore it. (A task you made in the last 10 minutes also has **Delete (undo create)** in its ⋯ menu; that is only an undo, not a way to tidy up.)
- **Agenda** (a view of Everything) gathers what is overdue, what is due before midnight, and what is waiting on you, across every section, most urgent first.
- **Tags** cut across everything. **Recurring rules** create items on a schedule you or an agent define.

## Sharing a private project

Sharing lets one outside person (a client or a collaborator) into one private section of your Slate: its lists, drawers, pages and chat, and nothing else. They sign in from a link you send them, with no account to create and no email involved, and their access ends after 30 days unless you extend it.

Use one private section in this Slate instance. Open the section → **Share & invite** (or **Share**), make it private, then fill **Guest name** and **Guest handle**. The form names the section and fixes the invitation to **Guest · Edit**. Press **Create invitation link**, copy it and send it privately yourself. The link expires in seven days and can be accepted once. Anyone holding it can accept it, so do not post it publicly.

A private section's **Project home** (open sections keep their board at the top) brings together its lists (create normal **Work** or **Ideas** lists as needed), docs, discussion, participants and an optional HTTPS whiteboard link. Whiteboard permissions are separate; Slate links to it rather than embedding a native canvas. Private lists inside a section keep their own access restrictions. Guests can create and edit lists in the shared section, but cannot create other sections or manage memberships.

**While they are in.** A guest sees everything you share with them, not just the section they were invited to: share a second section later and it appears for them at once, no new link needed. Their sidebar footer says **Access ends <date>** (30 days after they accepted). Three days before that, both of you get an Inbox entry and a phone notification; from the invite panel, **Extend 30 days** pushes the end out from today. **Copy a link for another device** makes a fresh single-use link for the same person without retyping their name: every phone, laptop or installed app needs its own link, and a link must be opened in Safari or Chrome, not inside a messaging app (that uses it up). On an installed app, which has no address bar, the guest pastes the whole link into the token box instead.

**Cancel invitation / session** cancels that link and the browser session it issued. To remove project access across the person's credentials, also remove their share; use Members to disable the account or revoke other tokens when offboarding completely. This cannot erase information already downloaded. No invitation is emailed automatically and no project agent is created. Technical/security details: [shared-project collaboration](docs/shared-project-collaboration.md).

**Digests follow access.** Daily digests respect your current project access, including old digests and pending phone
notifications. If some tasks are no longer shared, only the still-visible snapshot entries
and their counts remain. A pending digest with nothing visible is not sent. This cannot
recall a notification already delivered to a device.

**Invitation recovery.** If an invitation is incomplete, expired, revoked or already used, ask the person who invited you for a fresh link for that browser. Use your own access, not another person’s token. For a temporary connection or server failure, the screen suggests retrying instead; a valid link does not need replacing just because the connection failed.

## Using the board

Open Slate's address (for example `https://slate.example.com`) in any browser, on a computer or a phone.

**Signing in.** Project guests open the private invitation from the project owner and press **Accept invitation & sign in**. The browser stays signed in for up to 30 days; after sign-out/expiry, ask for a fresh link. An invitation proves possession of the link, not an email identity. Everyone else signs in with a Slate token: the first visit asks for it. Paste it once; the browser keeps it until you press **Sign out** (bottom of the left menu). If the token is ever refused (revoked, mistyped), the board goes back to the token screen.

**Finding your way.** Four entries stay at the top of the left menu: **My Work, Inbox, Chat, Everything**. The other destinations (**Docs & Files**, **Agents**, **Automations**, **Archive**, **Members**, **Audit log**, **Health**) live under **More**, keeping the same access restrictions. More remembers its expansion for this account in this browser; a direct link to a secondary page opens it and shows the selected destination in its label. Below are sections (click a name for its project home and work) with their lists and open-item counts, then the section's own pages (a doc filed "here" is one click away; its subpages live inside the page tree on Docs). On the phone the menu hides behind the ☰ button. The browser tab shows your **Needs you** count, e.g. "(3) Slate" (see *The number means Needs you*).

**Nothing you type is lost.** Text you start and leave stays where you typed it until you send it: a comment or a question to an agent on a card, a channel message, a new task in a column or the quick add bar, a subtask, a checklist item, a hand-off, an edit to a comment or message. Close the card, switch views or reload the page and it is still there. A card holding unsent text shows a **Draft** tag, and a column's **+ Add Task** does too. Sending clears the draft; **Cancel** (or Esc where Esc means cancel) throws it away. Drafts live in that browser only, so a draft typed on the phone stays on the phone, and one left alone for 30 days is dropped. A task's title typed and left when the card closes is saved as the title; descriptions and doc pages already save themselves as you type.

**Sections and lists are yours to shape.** Nothing is hard-coded:

- **+ Section** (under the last section) adds a section: give it a name and who it is for (Personal, Work, AI, Family).
- Hover a section's name for **+** (add a list) and **⋯** (add list, rename, move up or down, archive). A new list starts with three columns, **To do · In progress · Done**, and opens straight away.
- Hover a list for its **⋯**, or right-click it: rename, **List settings…**, move up or down, **Move to** another section, archive. Right-clicking a section's name opens its menu too.
- **Drag a section heading** above or below another heading to reorder whole sections. It saves across reloads and other boards. On a phone or with a keyboard, use its **⋯ → Move up / Move down** instead. Guests cannot reorder global sections.
- **Drag a list** to reorder it or move it to another section: drop it on a list to put it just above that one, or on a section's name to put it at the end of that section.
- **Archive** asks first, then hides the section or list and its items. Nothing is deleted: **Archived** at the bottom of the menu lists what is archived, with **Restore** next to each.
- Every change shows up on your other screens within a second, like everything else.

**List settings** edits a list's name and its columns (statuses): rename them, add one, reorder with ▲▼, pick a colour from the dot, choose the **default** (where new items start) and the **kind**, which tells Slate what the column means: *Open* (not started), *Active* (in progress), *Done* (finished; this is what "Complete" uses) and *Cancelled*. Removing a column that still has items asks where to move them before it saves.

**Everything** is where Slate opens. Its first view, **This week**, is the due board: **Overdue** (the red column) · **Today** (the yellow column) · one column per day for the next six days · **Future** · **No due date** · **Done** (what you finished today), sorted by due date. Days with nothing due fold into a thin strip with their name and count; click one to open it, or use a column's ⋯ → **Collapse column** to fold it (remembered per view on your account, so your phone and PC fold the same columns). Hover a column's name for ⋯ (collapse, select all, add) and **+**. Days roll over at midnight workspace time without a reload. The second view, **Agenda**, is a list of what is overdue, due today and anything waiting on you (the address `#/today` opens it).

**A list** opens as a **board**: one column per status, in the list's own order. Each card shows:

- the title (two lines, then "…"; **Customize** changes that);
- a row of small counts: ≡ when it has a description, subtasks, comments, checklist progress and attachments;
- pills: the due date ("Today, 4:00 pm", "Sun", "Nov 1", red once overdue, "4 days ago", and past a week the date with how late it is, "Aug 2 · 82d late"), the priority flag, tags in colour, status, who it is assigned to (🤖 for agents), its list (in Everything and section pages), who it is waiting on, and **Recurring**.

**Comment dots.** A small dot in a card's bottom-right corner means someone commented since you last opened it: **gray** when only agents did, **blue** when a person did (guests on a shared project count), and **pulsing blue** when someone tagged you (`@you`) or asked you a question, whether a person or an agent. Only one dot shows, the strongest. Your own comments never light it, and status or field changes never do. Opening the card clears it, on every device; reading the comment in the Inbox does not. The dot steps aside for the copy icon when you hover.

**Customize** (top right) picks which of these a view's cards show, how many title lines, comfortable or compact cards, whether empty columns fold or hide, and the theme (Auto, Light, Dark). Like any toolbar change, Save keeps the card settings with the view; the theme applies at once and follows your account to every device.

Hover a card for its tools: the box on its top-left corner selects it; at the top right, the circle completes it (it moves to Done; the note at the bottom offers **Undo**), and ⋯ (also on right-click): Open, Complete, due Today / Tomorrow / pick a date on a calendar / clear (the current choice is highlighted), priority, select, copy title or ID, put away (it asks once, and says what it does), move to trash. The small copy icon in each card's bottom-right corner copies the task ID in one click and shows **Copied**; it never opens or moves the card. Click the date pill to reschedule (Today, Tomorrow, This weekend, Next week, 2 weeks, pick a date, clear) and the priority pill to change priority.

**+ Add Task** at the foot of each column files a task straight into that column: typed into Tuesday it is due Tuesday, into High it is high priority, into a status column it gets that status. Quick-add words work there too (`#tag !urgent @my-agent`). Enter adds it and keeps the box open for the next one; Esc closes it. In Everything and section pages a list picker sits under the box.

Drag a card to another column to change what the columns stand for: its status, its due date (it keeps its time of day, or 5 pm if it had none; **No due date** clears it; **Future** puts it on the first day after the named days; **Overdue** is not a drop target), its priority, its assignee (replaces whoever was on it), its list, its tag (the card's tag for the column it left is swapped for the new one; **No tag** takes it off) or its section (when that section has one list; with several, group by List instead). Dropping on **Done** completes it. Folded columns are drop targets too, and so is the empty space under a short column: let go anywhere below a column and the card goes into it. A column that can't take a card (**Overdue**, **No status**) gets a grey outline and a note saying why when you let go. When the view is sorted **Manual** (the default for a list), dropping a card between two others puts it there, and a violet line shows where it will land; the List layout and checklists reorder the same way. If Slate refuses the change, the card jumps back and a red note says why. Finished items stay visible until midnight, then leave the board. They are still in Slate, not deleted.

**AI add (when the server has a model configured; on by default).** The quick add box and every column's **+ Add Task** have a small **✨ AI** switch. With it lit, just describe the task the way you'd say it, typed or dictated: *"Figure out why the backup job keeps failing, check the logs and the disk space, get it done by Friday afternoon, it's important."* Press Enter. The card appears at once with your words, and a few seconds later Slate's built-in AI fills it in: a clean title, the owner, due and start dates, priority, status, tags and a tidy description with any steps you listed as checkboxes. It works the same for everyone, guests on a shared project included, in every section. The history shows those changes as **Slate**'s. Anything you change on the card before it finishes is left as you set it; if the AI can't answer, the card simply stays as you typed it. It only offers people you can see as owners. A card filed in a column still starts with that column's meaning (its day, status, owner…); what you said wins over it. Click **✨ AI** to switch it off on this device and get the plain quick add below (the `#tag !high @who` tokens); click again to turn it back on. The AI is the Ollama model your administrator set for this server (`SLATE_AI_URL`); when that model runs on your own machine, nothing you type leaves it and nothing is billed.

**Quick add.** The box at the top files one item. In Everything or a section page, a list picker sits next to it (remembered per view). Type the title plus any of:

| Type | Means |
|---|---|
| `#home` | tag it "home" (new tags are created) |
| `!urgent`, `!high`, `!low` | priority |
| `@my-agent` | assign to that account |
| `due:today`, `due:tomorrow`, `due:friday` (or `due:fri`), `due:2026-11-01` | due at 5 pm workspace time that day; a weekday means the next one, today if it is that day |

The preview under the box shows what was understood. Anything it does not recognise stays in the title. Press Enter; the card appears on the board a moment later.

**The task window.** Click a card to open it in a large window in the middle of the screen (full screen on the phone). The task is on the left, its activity on the right. Every change saves as you make it; there are no Edit or Save buttons. **Make it yours:** drag the little corner at the bottom right to make the window bigger or smaller (it stays centered; double-click the corner to put it back), drag the edge between the task and its activity to give the activity more room, and the window button next to Close parks the whole window on the **right third** or **right quarter** of the screen, where the board beside it stays live (click another card and it opens there), or puts it back in the **center**. Slate remembers the size, the activity width and the place on this device.

**On a card an agent is on** (it is assigned, reporting, or has worked it), the conversation is the action: status, title, then the thread and the comment box, with **Ask**, **Run now** and **Hand to an agent** together by the box. Assignees, dates, tags, list, repeats, estimate and dependencies fold into one **Details** line (for example "My Agent · Due Nov 20 · High · #example") once any of them is set; click it to open them. On the phone the thread comes straight after the title. Cards with no agent show the fields in a grid under the title.

- **Status**: the colored button at the top. Click it for the list's statuses, each shown as a whole pill in its own colour (the current one is ticked); the **›** next to it moves to the next column; the **✓** square completes it (click again to reopen).
- **Title**: click and type; Enter or clicking away saves.
- **Assignees**, **Priority** (flags: Urgent, High, Normal, Low), **Dates** (start → due), **Tags**, **Waiting on**, **List** and **Repeats** sit in the grid under the title.
  - Dates: click for quick picks (**Today**, **Tomorrow**, **Next Mon**, **+1 week**, **Clear**) or any day. A date alone means 5 pm workspace time; **+ time** sets another time.
  - Assignees: **+ assign** opens a searchable list of people and agents (🤖). Each tick saves at once, and the list stays open so you can tick several in a row; click anywhere else or press Escape to close it. On a phone it opens as a sheet along the bottom.
  - Tags: type to see matching tags; Enter adds what you typed (new tags are created); Backspace on an empty box removes the last one.
- **Description**: click into the box and type. It saves a second after you stop, and when you click away or close the task ("Saving…", then "✓ Saved"). Markdown works: Ctrl+B bold, Ctrl+I italic, Ctrl+K link, Enter continues a list. `- [ ]` makes a tick box you can tick straight from the description. The same rich blocks as a doc page render here and in comments and chat: `## Heading`, bullets and numbered steps nested by indenting, pipe tables (`| a | b |` with a `|---|---|` line under the header), `> quotes`, `---` dividers, `~~struck~~` text and fenced code. Any web address you paste (`https://…`) becomes a clickable link on its own, here, in comments and in chat; `[words](https://…)` still works when you want your own wording. Paste or drop a file to attach it.
- **Done means** (optional): what has to be true for the task to be finished; agents read it.
- **+ Subtask**, **+ Checklist**, **+ Attach** under the description:
  - **Subtasks** show as rows with a completion circle, status, assignees, due date and priority, and a progress bar. Click one to open it; its window links back to the parent.
  - **Checklists** are small tick lists inside the task (not separate tasks). Rename one by clicking its name; ⋯ hides checked items, checks everything, or deletes it.
  - **Attachments**: drop files anywhere on the window, paste them, or pick them (up to 25 MB each). Images show a thumbnail. Click an image, a video, a PDF, a **Word file (.docx)**, an **Excel workbook (.xlsx)**, a CSV or a text file to open it in a near full-screen viewer (← → step through the task's files, Esc, × or a click outside closes, ↓ downloads). A Word file shows as a letter-size page with its headings, text, lists, tables and pictures; a workbook as Excel's grid with column letters, row numbers, number and date formats, merged cells, and a tab per sheet along the bottom (the first 2,000 rows of each). It is a view, not an editor: download the file to change it. Old binary .doc and .xls files, PowerPoint and anything else download. An image an agent pastes into a comment or description as a long block of text (a base64 image) is saved here as an attachment, and the text says "📎 name (attached)" in its place.
- **Activity** (right): comments as cards, with every change between them in small gray lines with the time ("My Agent changed status from To do to In progress · via MCP"). Repeated saves of the description fold into one line. **All / Comments / History** filters it. The 👁 button shows who is watching (see **Watchers** below).
  - **Comment** at the bottom: Enter sends, Shift+Enter makes a new line. Type `@` to mention someone; the mention reaches that person or agent (see Webhooks). Comments that mention you have a blue edge.
  - Hover a comment to react (😀), **Reply** (starts a comment mentioning its author) or **Edit** your own. **Delete** removes a comment for good after asking: anyone can delete their own, and the workspace owner and admins can delete anyone's. Its reactions go with it, and a question it asked closes. The history keeps a small line saying who deleted whose comment (not what it said), and it drops out of everyone's Inbox. Agents delete with `slate_comment delete`.
- **Header**: where the task lives (click the list to go there), its ID (click to copy), the link button (copies a link that opens this task), and ⋯ (copy link, ID or a Markdown line, attach, add a checklist, put away or bring back, move to trash, and for a task you made in the last 10 minutes, delete as an undo).

**What's waiting for you.** On a computer, the bell at the top right (left of Agents) shows **@** and a count when someone has mentioned you or asked you something, and a chat bubble with a count for unread replies, comments and chat. Click it to see what's waiting and jump straight to the card or chat, mark everything read, or open Notification settings.

**Notifications → Tell me when.** One row per kind of thing (mentions, replies and chat, assignments, someone waiting on you, due dates, tasks you watch, Slate alerts) and a box for each way to be told: **Toast / push** (this computer or phone) and, when the server has email set up, **Email**. Type your address under the grid and tick the rows you want emailed. An email goes out only if you still haven't seen it in Slate five minutes later, and a burst arrives as one email. **Send a test email** checks it reaches you.

**Watchers.** The 👁 at the top of Activity shows the faces and count of everyone watching the task. Click it to **Watch** or **Stop watching** yourself, remove anyone (×), or add a person or agent from the search list: anyone who can edit the task can add or remove anyone, and someone you add (or remove) is told. Someone with view or comment access to the list can only watch or stop watching themselves. A watcher hears about everything on the task: comments, status, dates, priority, title, description, tags, assignees, checklists, files, links, subtasks and an agent starting or finishing. It lands in their Inbox and, with **Anything happens on a task I watch** ticked under Notifications (on unless you turned it off), on their phone and desktop. An agent starting work shows in the Inbox only: the toast, push or email comes when it finishes, asks you something or hands the task back. A run of description saves by one person counts once. You start watching a task automatically when you create it, are assigned to it, or comment on it; Notifications → **Watch tasks automatically** turns each of those off. Agents do not auto-watch unless their account turns it on, but anyone can add an agent as a watcher, and it then hears about the task in its check-in.

The address bar carries `?task=<id>` while a task is open, so reloading keeps it open and a copied link opens it for anyone signed in. Escape, ✕ or a click outside closes it.

**Live.** Changes from agents and other screens appear within a second; you never need to reload. That includes saved views: rename or change one on your computer and the monitor follows. If the connection drops, a yellow "Reconnecting…" bar appears. When it clears, everything that changed in the meantime has been applied. After a new version of Slate is deployed, an open Slate reloads itself into it, on computer and phone alike; if you are typing, it waits until you leave the box.

## The look

Slate follows your system's light or dark setting (Customize → Theme picks one for your account, on every device). The left sidebar shows your sections with a coloured letter and the lists inside them; the small `r129`-style number by the Slate logo is the build revision that is running (it goes up with every change landed; hover it for the commit), and the dot next to it is green while the board is live and amber while it reconnects. Your name and the Tags, Archived and Sign out buttons are at the bottom. When a search or filter hides every task the board says so and offers to clear it. Animations are short, and stop entirely if your system asks for reduced motion.

## Favorites bar

The thin bar across the top holds your favorites: one click opens a list, a section, a saved view or a task. Star a list or section with the ☆ that appears when you hover it in the left menu (or **Add to favorites** in its ⋯ menu, or drag the list up onto the bar). Pin a view from its tab's ⋯ menu; a pinned view shows a small ★ on its tab. Press `s` to star the open task, or the card under the pointer. Drag chips along the bar to reorder them (hold one first on a phone); hover a chip (right-click, or long-press on a phone) and click × to remove it. The chip for where you are is underlined. Favorites are yours alone: an agent pinning something pins it on its own bar (MCP `slate_favorite`). Archiving a list or view hides its chip; restoring it brings the chip back. On a phone the bar is one row under the top bar that scrolls sideways, and it hides when you have no favorites.

## Slate on your phone

Slate works in the phone's browser and installs to the home screen like an app (full screen, its own icon, opens instantly). The phone must be able to reach Slate's address over HTTPS: if your server is only on a private network (a VPN such as Tailscale, say), the phone needs that VPN too.

- **iPhone:** open Slate's address (say `https://slate.example.com`) in **Safari**, tap **Share** (the square with the arrow), then **Add to Home Screen**, then **Add**. Open Slate from the new icon and paste your token once: the installed app keeps its own sign-in, separate from Safari. A project guest pastes their invitation link into the same box (ask the inviter for a link for this device), which is also how iPhone notifications become possible for a guest.
- **Android:** open the same address in **Chrome**, tap **⋮**, then **Install app** (or **Add to Home screen**), then **Install**.

On the phone: ☰ opens the menu, swipe sideways through the board's columns, tap a card to open it full screen. Updates arrive by themselves: after a deploy, close and reopen the app.

**Sharing into Slate** (Android, installed app): on a web page, a video or a message, tap **Share** and pick **Slate**. Quick add opens on Everything with the page's title in the box, the link saved into the item's description (the chip under the box shows it; *drop the note* leaves it off) and the list picker on the list you used last; change the words, add `tomorrow` or `#tag` as usual, and press Enter. A link shared on its own becomes "Read example.com/…" (or "Watch …" for a video). iPhone's share sheet does not offer web apps, so there you paste the link into quick add instead.

**Dragging with a finger** (phone and iPad): press and hold a card, row, calendar item, timeline bar, checklist item, favorite, list or doc page for a moment until it lifts (Android buzzes, iPhone ticks), then slide it where it should go. Near the edge of the screen or a column it scrolls along, so you can carry a card to a column that is off screen. A quick swipe still just scrolls. Hold and let go without moving to get the card's menu (the same as a right-click on a computer).

**More touch gestures**, each a shortcut for something that also has a button: swipe a row on **My Work** or in the **List** layout to the right to complete it, to the left to snooze it to tomorrow (the toast offers Undo; a short swipe springs back and does nothing). Pull down at the top of a page to refresh it. Menus open as a sheet from the bottom; tap outside to close. The task window and chat keep the box you are typing in above the keyboard. Without signal, a grey bar says you're offline and shows what was loaded; changes made then aren't saved, and Slate says so instead of pretending.

## Views, grouping, filters, monitor mode, shortcuts

**Views.** Every page (Everything, a section, a list) has view tabs along the top. A view is a saved way of looking: layout, grouping, sorting, filters and what cards show. Until you save one, a page shows its **Default** (lists: their status columns; Everything and sections: the due board). Change anything in the toolbar and an orange dot appears on the tab with **Save** (overwrite this view), **Save as new** and **Revert**. Slate remembers where you left off: go to another list or reload, and coming back shows the same tab with the same layout, sort and filters, on your phone too, until you Save or Revert. **+ View** saves the current setup under a new name. The tab's ⋯ menu renames, duplicates or deletes it. The address bar names the view (`#/everything/v/…`), so a bookmark opens exactly that view. Agents can make views for you too (MCP `slate_view`).

**Toolbar.**

- **Board / List** (`b`): columns, or a table with collapsible groups. In the list, click **Title**, **Due** or **Priority** at the top to sort by it (again to flip). A card's left edge is its priority colour, red when it is overdue; list rows carry the same stripe. Grouped by status, the list drops the Status column (the group says it) and each group header shows the estimates it holds.
- **Group** (the chip on the left): status (inside a list), status kind (Open / Active / Done / Cancelled, across lists), due date, priority, assignee (an item with two assignees shows under both), tag (likewise), list, section, or none.
- **Sort**: one or more keys, each up or down; items with no value always go last.
- **Filter** (`f`): rules such as "Tag has any of #home", "Due date is in the next 7 days", "Assignee is me", combined with all/any, and groups inside groups ("high priority **and** (tagged home **or** assigned to me)"). Only complete rules appear as chips and count toward the Filter badge; × removes one, **Clear filters** removes all. A rule you have not finished stays editable and is labeled **Incomplete — choose a value**, but does not change the results (including inside any/all groups). Unchecking the last selected value removes that rule. **Me (current viewer)** follows whoever opens the view; **Name (specific person)** always means that account. Disabled accounts and Slate’s internal system account are not offered for new selections; a saved historical selection stays visible with a disabled/system label so you can remove or replace it.
- **Subtasks** shows subtasks as their own cards. Each one is a compact card (indented, smaller text, a grey tint and a thin purple left edge), so it never looks bigger than a regular card. It says "Subtask of …" above its title, cut short with "…" when the parent's name is long (click it to open the parent), and a thin line joins it to its parent card, in the same column or across columns. Hover either card to light its lines up. A line whose end is scrolled out of its column turns dotted; a parent that is not on the board (filtered out or in a folded column) gets no line, only the label. **Closed** brings back items finished on earlier days; **Me** shows only what is assigned to you (it is a filter rule you can also see in the chips).
- **Customize**: which details cards carry, title lines, compact cards, fold or hide empty columns, how many named days the due board shows, and the theme.
- **+ Task** jumps to the quick-add box.
- **Search** (`/`): narrows whatever view is showing; it is not saved with the view.

Columns with many cards show the first 25 and a "Show more" button, so a big "No due date" column does not slow the board down.

**Moving a task to another list.** Right-click a card (or open its **⋯**) and pick **Move to…**, or press **Move** at the top of an open task, or click the **List** field in it. A search box opens over every list, grouped by section; type part of a list or section name, use the arrow keys, and press Enter (or click). The list the task is in now is marked *here now*. The task keeps a column of the same kind (an in-progress task lands in the new list's in-progress column), its subtasks move with it, and the toast's **Undo** puts it back. Subtasks have no **Move to…**: they always live in their parent's list.

**Selecting many.** Hover a card and tick its box (or press `x` over it); shift-click selects a run. On a phone, hold a card (or tap ⋯) and pick **Select**. While anything is selected, every card and row shows its box, ticked on the selected ones (which are also outlined), clicking a card selects it too, and a bar appears with one button per action, each opening a small menu: **Status** (status kind across lists: each task gets its own list's column of that kind, all at once or not at all), **Due**, **Priority**, **Tags** (add, or remove one they carry), **Assign**, and **Move** to a list (the same searchable picker as **Move to…** below). **Put away** and **Trash** ask once before they act, in one sentence that says what happens. Trash moves the tasks to the trash, where they stay restorable for 30 days, and the toast's **Undo** brings them all straight back. `Esc` or the **×** next to the count clears the selection.

**Monitor mode.** Add `?kiosk=1` before the `#` (for example `https://slate.example.com/?kiosk=1#/everything`) for the board on a wall or second monitor: no menu or toolbar, bigger type, every column fits the screen, a clock and a green dot while the live connection is up. The mouse pointer hides after three seconds still. It still updates live.

**Keyboard.** `/` search · `c` quick add · `b` board/list · `f` filter · `x` select the card under the pointer · `s` star (favorite) the open task or the card under the pointer · `Esc` close · `?` shows this list.

## Table, Calendar, dependencies and tags

**Table** (toolbar, next to Board and List) is a spreadsheet: every cell edits in place when you hover the row (status, dates, priority, assignees, tags, estimate). Click a title to rename it, **Open** for the full item. Click a column header to sort. Each group ends with a totals row: item count and total estimate.

**Calendar** shows the view's items by due day, a month or a week at a time (**Month / Week**, ‹ › and **Today**). Work with a start date runs as one bar from its start day to its due day, named at its start and at each new week; weekends are shaded. Drag an item to another day to reschedule it (it keeps its time of day), or onto **Unscheduled** on the right to clear its date; drag from Unscheduled onto a day to schedule it. **+** on a day adds an item due that day.

**Timeline** (toolbar) is a Gantt chart: each task is a bar from its start date to its due date on a time axis, grouped the way the view is grouped. Switch the scale with **Day / Week / Month**. A red line marks today. A task with only a due date is a diamond. **Drag a bar** to move it (both dates shift together), or **drag its left or right edge** to change the start or the due date — the task window and everywhere else update at once. **Arrows** run from a task to whatever depends on it (its *Depends on* dependencies), and a dependent task's bar is outlined, so you can see what has to finish first. Tasks with no due date wait in a tray under the chart; drag one onto the timeline to schedule it. Click any bar or name to open the task.

**Time estimate.** Every item has an estimate (type `90`, `45m`, `1.5h`, `1h 30m` or `2d`). It is a plan, not time tracking.

**Dependencies.** In an item's panel, **+ Add dependency** links it to another item: *Depends on* (this cannot start until that is done), *Blocking*, *Linked to*, or *Duplicate of*. (*Waiting on* is only ever a person: the task field of that name.) An item that depends on something still open shows a red **Depends on** pill with the first task's name (hover it for all of them), a banner at the top of its task window, and a **Depends on** box on My Work. You never clear it yourself: when the last thing it depends on is done, cancelled, archived or unlinked, it clears on every screen at once, and whoever is assigned gets **Ready to start** in the inbox. Reopening a finished blocker blocks it again. Filter for them with **Filter → Depends on an open task**; an automation can start on **Task is unblocked**.

**Tags.** **Tags** at the bottom of the menu opens the tag manager: how many items use each tag, pick a colour, rename, **Merge** one tag into another (every item moves over), or **Delete** it (it comes off every item; the items stay). Tags are one list for the whole workspace, so only an owner or admin recolours, renames, merges or deletes one; anyone can make a tag and put it on a task they can edit. A project guest sees only the tags on tasks and docs they can reach, plus any they made.

## Search, My Work, Inbox and undo

- **Search everything:** press **Ctrl K** (⌘K on a Mac), or tap ⌕ in the top bar on the phone. Type part of a task, list, view or tag and press Enter. After two letters Slate also looks inside task descriptions, doc pages and files, and chat, and lists what it finds under **Tasks**, **Docs** and **Chat** below the quick matches: a doc opens on its page, a chat hit opens that channel at its thread. Start with `>` for commands (go to Inbox, switch to dark mode, undo, manage tags), `#` for tags. If nothing matches, the last row creates the task. Agents get the same search with `GET /api/v1/search?q=` or `slate_search`.
- **Quick add understands plain English:** `Call the bank tomorrow 4pm #personal !high`, `Pay water by fri`, `Dentist jan 5 at 9`, `Report in 3 days`, `~bills` to pick the list, `!1`..`!4` for priority. `every monday at 7am`, `every other friday` or `every 15th` makes a recurring task. Put `\` before a word to keep it in the title (`Watch \friday night lights`).
- **My Work** (left menu, or `g h`) is the page that says what to do next: **Needs you** first, then your tasks by when they are due, what depends on another task, what you handed to others, your newest notifications and what you finished this week.
- **Needs you:** cards that can't move until you act. Every page uses the same four words for why, on the card, in the task window and in the lists:
  - **Waiting on Alex** (your name): a person has to answer (the task's *Waiting on* field, usually set by an agent that needs you). Elsewhere it names whoever it waits on. On the board, a card that needs you has a colored ring (red for waiting or blocked), and one waiting on you carries a red **Waiting on Alex** chip under its title. Open the card and the question sits in a red box at the top: **Answer** jumps to the message that asks it and puts you in the reply box (your reply goes to that agent and the card stops waiting), and **Not waiting on me** clears it when there is nothing to answer.
  - **Agent blocked**: an agent on your card stopped and named nobody. A run that only ran out of steps reads this way too: nothing is asked of you, and **Run now** in the red box carries it on.
  - **Asked of you**: an open question addressed to you.
  - **Ready for review**: an agent says the work is done.

  *Depends on* (another task has to finish) is not counted: it clears by itself. Agents still working and overdue dates are not counted either.
- **The number means Needs you.** The count on **My Work** in the left menu, the pill (bottom right on a computer, in the top bar on the phone), the window title **(3) Slate** and the number on the installed app's taskbar or dock icon are all the same Needs you count: the cards that need you to stop and act. Click the pill for the list; My Work and the Inbox list them at the top grouped by those words, with **Mark reviewed** right there. Each list in the left menu shows a small colored count when it holds cards that need you. Unread chat has its own badge on **Chat**; overdue and due today are the badge on **Everything** and the Overdue column, not this number.
- **Inbox** (`g i`): mentions, assignments, due nudges and changes on tasks you are assigned to or watch (a watched task reports everything, 👁), from people and agents. Opening a task marks it read; Clear removes it. Agents read the same inbox through MCP (`slate_inbox`).
- **Undo:** **Ctrl Z** puts back the last change you made on the board (status, dates, priority, moves, put away, bulk edits, a task you just added). Completing, putting away, trashing and moving also show an Undo button, next to one sentence that says what just happened.
- **Keys:** `j`/`k` move between tasks, Enter opens, `e` completes. `?` shows them all.

## Automations

**Automations** under **More** in the left menu (or the ✦ sparkle next to a list's ⚙) sets up rules that do routine work for you: *when* something happens to a task, *if* some things are true, *then* Slate does one or more things.

- **When:** a task is created, its status changes (to a particular status, or to any done/active status), someone is assigned or unassigned, a tag is added or removed, its priority changes, its due date arrives or passes, a comment is added (or one that mentions someone), or it is moved into the list.
- **If** (optional): priority, tag, assignee (or "nobody"), status, kind of status, or list is or is not something.
- **Then:** change the status, set the priority, add or remove an assignee (a person or an agent), add or remove a tag, move it to another list, set the due date a number of days out, post a comment (`{title}`, `{actor}` and `{status}` are filled in), archive it, or call a web address with the details. Web calls are signed: when you add one, the page shows a secret once (copy it into whatever receives the call; **New secret** makes another).

Start from one of the ready-made rules at the bottom of the page or press **New automation**. The sentence under the builder says what the rule will do as you fill it in. Each rule has an on/off switch, **Run now** (pick a task to run its actions on straight away) and **Edit**. The **Run log** shows every time a rule fired: which task, whose change set it off, and what each step did, with anything that failed in red.

**Agent reports a new state** starts a rule when an agent's badge changes: it starts *working*, gets *blocked*, hands the task over for *review*, or finishes. Use it to move cards between your own columns as agents work, for example "When an agent reports working, change status to fixing" (the ready-made **Agent work moves the card**). A new progress note while the agent is still working does not set the rule off again.

Changes an automation makes show in a task's activity as "by automation" with the rule's name. An automation's own changes never set off other automations, so two rules cannot ping-pong. Agents manage automations through MCP (`slate_automation`).

## Chat

Every list has a **Chat** tab next to its views, every section has one too, and **General** is for everyone. **Chat** in the left menu (or `g m`) shows them all with unread counts; a red `@` count means someone mentioned you. Each section's row in the sidebar carries a small chat icon that opens that section's chat (a project home's **Open chat** button does the same); when the chat has unread messages the count sits on the icon. Lists show the badge only when there is something unread.

**Each section and each list has its own chat** (#Example Project, #Todo...), plus **#General** for members. The chat bubble next to a section or list in the left menu opens that one; **Chat** in the left menu opens the panel on whichever chat you had open last (the name at the top of the panel says which, and is a menu to switch). If that chat is one you can no longer see, or your browser is new, the panel opens on the chat with a mention for you, else one with unread messages, else the first.

**Chat opens beside the page you are on.** On a computer, **Chat** in the left menu, a list's or section's **Chat** tab, the section's chat icon and a project home's **Open chat** all open the chat in a panel on the right, and the page underneath stays where it was: the board, a doc, My Work. On a doc the page sits at the left, so the panel covers none of it; on a board it covers the right-hand columns. The panel stays open as you move between pages and comes back after a reload, on the same chat. The top bar and **Agents** button stay visible in every size. Resizing keeps the conversation, open thread and unsent draft in place. At its top, the name is a menu to switch to another chat (with unread counts), the left/right arrow switches between a compact and wider panel, the diagonal-arrow button fills the window **below the top bar** (click again to restore the previous width), the open-as-page icon opens the same chat as a full page (`#/chat`, which also has the list of every chat; its **Panel** button puts the chat back in the panel and returns you to the page you came from), and × closes it; clicking **Chat** in the menu, or the lit **Chat** tab, again closes it too. **Drag its left edge** to make the panel wider or narrower (it remembers the width on this device; double-click the edge to put it back). A thread opens inside the panel over the messages; close it to go back. A link with `?chat=<channel>` on any page (for example `#/docs/<id>?chat=list:<list id>`) opens the panel on that chat. On a phone or a narrow window chat is a page of its own.

- **Write** in the box at the bottom: Enter sends, Shift+Enter starts a new line. Type `@` to mention a person or an agent (they get it in their inbox, their phone notifications and, for agents, their check-in). Type `#` to link a task: it shows as a card with its status and due date, and a click opens it.
- **Hover a message** (on a phone the actions sit under each message) to react, **reply in a thread**, **turn it into a task**, copy a link, or edit or delete your own. A thread opens beside the channel (over it on a phone); everyone who wrote in a thread hears about new replies.
- **Turn into task** files one task in the channel's list (or the list you pick), quoting the message; the message then shows the task's card.
- **`#` links a task, a page or a list.** Type `#` and a few letters: the picker offers matching tasks, doc pages and lists in three groups. A task shows as `#` plus its title and as a card; a page shows as its icon and title and opens the page; a list shows as its name and opens the list. Someone who cannot see that page sees only "a page" and, on opening it, the usual page-is-gone screen.
- Only messages fully visible for a moment in the focused, visible chat window are read. Leaving a channel open in the background does not clear it. Opening a chat Inbox group opens its newest thread without acknowledging other threads in that channel. Messages covered by a task dialog stay unread; closing it starts reading only messages now fully exposed in the focused window. Unopened thread replies and unloaded older messages stay unread across devices; reply links show their unread count. **First unread** loads earlier messages or opens the unread thread. Very tall messages can be acknowledged individually through **More actions → Mark this message read**, without clearing their unopened replies or other messages. A failed read shows an error rather than claiming success. **Mark all read** remains an intentional catch-up action. This is your catch-up state, not a seen receipt shown to other people.
- Mention suggestions are a directory, not proof of channel access; the picker warns about this. A mention does not grant access.
- Private project home shows your owned tasks, open task Asks and work waiting on you in that project, with source links. Unassigned ideas are not commitments. Document requests stay in **Needs you** in Docs & files; drawers appear there, not in Work & ideas task counts.

**Send a screenshot or a file.** Take a screenshot (on Windows, Win+Shift+S), click in the message box and press **Ctrl V**: the picture appears above the box as a small thumbnail. You can also drag a file onto the box, or click the **paperclip** under it to pick one. Add words if you want (a file can go on its own), then press Enter. Before sending, the × on a thumbnail takes it off again. In the chat, pictures show in the message and other files show as a chip with the name and size; click either to open it full screen (← → step through that message's files, Esc closes) or to download what the browser cannot show. Up to 10 files a message, 25 MB each. Whoever can read the message can open its files, and no one else; deleting the message deletes them. **Make a task** from a message brings its files along as the task's attachments. An agent you mention is told about the files and can open them.

Agents use the same channels through `slate_chat` (MCP) or `/channels` (REST), and hear mentions and thread replies in their check-in. **@mention an agent in a chat and it answers there**: an agent whose runner Slate can start (wake mode `push` on the Agents page) is started by your mention, the way a question on a card starts it, and its reply appears in the same channel or thread. One run at a time per place: a second mention while it is working waits for the next turn. While its answer is on the way, a quiet line under the last message (in the thread, for a mention in a thread) says **My Agent will answer…** until its runner picks it up, then **My Agent is working…** until the reply lands; it is there after a reload too. If its run ended without an answer (the runner stopped or lost track of it), the line says **My Agent stopped answering** with a **Clear** button: Clear ends that turn, and any mention that waited goes back to the agent. Mentioning the agent there again also asks once more. If you lower an agent's access so it can no longer post in a section, its unanswered mentions there are dropped. Agents never start each other this way, and an agent you have not shared the section with is not started.

**Chat with an agent, no card needed.** The blue-green **Agents** button with a robot icon at the top right of the page (in the top bar on a phone) lists your agents. Pick one to open its own chat in the panel (the Chat page on a phone): ask it a question, or ask it to do something in Slate. For an ordinary workspace agent, you do not need to type `@` its name: everything you write there reaches it, and an agent with wake mode `push` starts and answers in the same chat. **Shared project agents (guest-role accounts) are tagged-only:** type their `@handle` when you want an answer. Human conversation, `@alex` or mentions to other people, and plain follow-ups do not start the project agent—even in a thread. Editing a message to add its mention does start it; keeping the same mention in an edit does not start it again. The robot shows a red count when an agent has answered and you have not read it yet. The agents' chats are also on the Chat page, under General. Members and admins can read every agent's chat. A guest of a project sees the chat of each guest agent you shared into that project: they read what it says there and can talk to it, and they see no other agent's chat. It is one chat per agent, so what you say to a project's agent there, that project's guests can read too. A disabled agent's chat disappears from the list.

**Your own agents stay out of a guest's sight, everywhere.** A guest of a project sees no trace of a workspace agent such as `my-agent`: not its card comments or your Asks to it, not its "working" badge or session link, not its name among a card's assignees or in a card's history, not its page comments, and not in any @ or Ask list. A guest cannot ask it, assign it, run it or wake it; the cards it made stay, because they are the work. In chat: On a shared project, a workspace agent (any agent that is not a guest) is invisible to the project's guests. Its messages in the project's chat, your messages that `@mention` it, and every reply in such a thread are marked **· hidden from guests** on your screen and never reach a guest at all: not in the chat, not in an unread count, not as a notification. Guests do not see it among the project's members, cannot wake it with an `@mention`, and do not see its "is working…" line. The project's own agent (a guest agent) cannot read those messages either. Editing a message to add a workspace agent's `@mention` hides it from then on; nothing hidden is ever shown again.

## Box view

**Box** in the view bar (next to Timeline) shows a list, a section or Everything as one box per person and agent, plus **Unassigned**. Each box shows how many tasks are to do, in progress and overdue, how many were done in the last 7 days, a bar of how much is done, and the open time estimates against this week's capacity (red when over). Below are the open tasks, in progress first.

Drag a task onto another box to hand it over, or press the person icon on it and pick **Assign to** (the same on a phone). The view's filters apply, and you can save it like any view. Agents read the same counts with `slate_workload` `boxes`.

The **gear** on a box sets that person's or agent's capacity: hours a day, tasks a day, and working days. Leave a box empty for no limit; **Use defaults** goes back to 8 hours on weekdays (every day for agents). You can set your own; owners and admins can set anyone's. Agent capacity is also on each agent's card on the **Agents** page, and agents can set theirs with `slate_workload` `set_capacity`.

## Docs & Files

**Docs & Files** under **More** in the left menu (or `g p`) holds pages and files next to the work: plans, meeting notes, runbooks, and the PDFs, images and spreadsheets a project runs on, with search and one tree across every page and file in the workspace. A page or a file lives in the Workspace, a section or a list, and pages nest inside pages; a page with things under it is the folder. One tree, so a file sits next to the page it belongs to.

**Files.** **Upload** (top of the sidebar, or *Upload a file* on the home page or in the New page sheet) puts a PDF, spreadsheet, photo or other file in as a document of its own, up to 25 MB. It opens like a page: the title, a viewer (PDFs in the browser's viewer, images, Word files as a page, Excel workbooks as sheets, text with line numbers, CSV as a table; anything else, a download card), and **Notes about this file** underneath, a normal page body for context. The sidebar shows a file by its type (📕 PDF, 📊 spreadsheet, 📐 drawing) with its revision number, `r3`.

- **Revisions.** A file is never replaced, it gets its next revision: **Upload new revision** (or drop a file onto the page). You must say **what changed**, in one line; for a file Slate cannot compare that line is the only diff there is, and it shows under the file (`Rev 3: Updated the totals on page 2`). The old revision stays.
- **Revisions panel** (the clock): every revision with who, when, size and its note folded under a caret. Click one to look at it (a banner says *Viewing revision 2 of 4* with a way back), **Download** it, or **Make current** to bring it back as a new revision. Nothing is ever deleted.
- **Compare with current** (on any older revision, or in the banner) shows what changed the way the type allows. A text file (notes, a G-code program, JSON) shows side by side or inline. A **spreadsheet or CSV** shows the newer table with each changed cell painted: old value struck through, new value after it, added rows green, removed rows listed under it; one tab per sheet. A **PDF or DOCX** compares the words and numbers pulled out of both revisions, labelled approximate because layout and pictures are not compared; a changed figure still shows. Two **photos** sit on top of each other with a swipe slider. Anything else (DWG, ZIP) shows the two revisions and their notes. The From and To pickers compare any two revisions.
- **Search** finds a file by what is inside it too: text files, spreadsheets (including formulas) cell by cell and the text of PDFs and DOCX files are read once on upload. A file uploaded before Slate could read its type is found by content only after an administrator runs the text backfill (`slate backfill-doc-text` on the server). A very large spreadsheet, Word file or PDF (more than the server reads for search, 64 MB unpacked unless the administrator changed it) still uploads, opens and downloads as usual; it is just not searchable or comparable, and its history row says **not searchable**.
- **Page history** has a **Side by side** tab next to *Changes since*.

**Finding and sorting.** Above the tree, chips narrow it to **Pages** or **Files** (and files to PDFs, Sheets, Images, Text, Other); the sort menu orders every level by tree order, by what changed most recently, or by name. Tags used on documents show as chips too: one click keeps only the documents carrying that tag. The filter is remembered on this device.

**Facts (records).** A page can carry reference information as facts: start the page with a fenced block tagged `facts`, one `key: value` per line (`Policy number: 12345`, `Renews: 2027-03-01`), close it, then write the prose below. The page shows those lines as a key/value table above the text, and search finds a value (`12345` finds the policy). It is still an ordinary page: edit the block in place like any text, keep its history, discuss it; agents read the facts as fields and write them with the same page update they use for anything else. A **Records** drawer is a natural home for warranties, policies and account references.

**Tags.** A page or file takes the same tags tasks use, under its title. Agents set them with `tags` on `slate_doc` create and update; `GET /docs?tag=` and the Tags page count them.

**Drawers.** A section holds three kinds of thing: **lists** of tasks, **drawers** of documents, and **pages**. A drawer is where you put the documents you and the people you work with are actually referencing: Contracts, Notes, Meetings. You make one on purpose (the + by a section, then pick **Drawer**; or **New drawer** on a project home) and put things in it on purpose, so nothing is gathered up from the rest of the workspace. In the sidebar a drawer has a folder glyph and counts its documents.

- **The Files table.** Open a drawer and you see its folders and documents: name, **created** date, revision strip (the amber dot is the revision in use), the last change and its note, tags, open threads. Chips narrow it by type or tag, **Filter this folder by name** matches names in the current folder only (not file contents; use Docs & Files search for that), **Upload**, **New page** and **New folder** add to it, and a file dropped anywhere on the page uploads into it. A folder opens in place with a breadcrumb; the folder button on a row moves it. **Pin to top** (the pin button on a row) keeps a file, page or folder first in its own list, the drawer's top or the folder it is in, with a 📌 before its name; everyone sees the same pins, the oldest pin first; the pin button again unpins. (This is separate from the amber revision pin, which marks which revision of one file is in use.) **Created** is the file's date: a file whose name carries a date (`2026-10-30 - Project brief.pdf`, `Budget 10-30-26.xlsx`, `Oct 30 2026 notes`) is created *on that date*, so the column reads the way you named the files; the upload time stays on the revision.
- **Reading.** Click a document and the page becomes a reading room: the drawer's files on the left, the document in the middle with its viewer and revisions, **All files** to go back. Discussion opens alongside on a desktop; on a phone the file or page comes first and the speech-bubble button opens discussion when requested.
- **Pinned revision.** In the Revisions panel, **Pin as in use** marks the revision actually in use, with a note; it shows as the amber dot in the table, in the rail and under the file's name, so a newer upload is never mistaken for what is in use. One pin per document; Unpin takes it off. Pin changes also appear in the document discussion activity.
- **Converting.** A page with subpages becomes a drawer from its ⋯ menu (**Turn into a drawer**): the subpages become the entries and the page itself moves in with them. List settings has a **Kind** switch to turn an empty task list into a drawer or a drawer back into a list; a list with tasks in it cannot become a drawer until they are moved.
- A drawer takes no tasks, and a task cannot be moved into one.

**On a project home** (a private section), the panel lists the project's **drawers** with their document counts and how many entries need you, its **pages**, and **Needs you**: asks addressed to you and review threads others opened on documents you touched, each one click from its thread.

- **From a task.** A file attached to a task has a **Move to Docs & Files** button (hover the attachment): it becomes a file doc in the task's section, and the task keeps a comment linking it.
- Agents upload revisions through MCP (`slate_doc` `upload`, `revisions`, `restore_revision`) and `slate_item` `promote_attachment`; the same note rule applies to them.

**Talking about a document.** The speech-bubble button on any page or file opens the **Discussion** panel on the right (the badge is the count of comments, or of open review threads). Two tabs, and the gesture picks which one you are using:

- **Discussion** is the running conversation about the document: type at the bottom, `@` to mention someone (they get it in their Inbox and on their phone), **Ask** to address it to one person or agent so it stays open until explicitly resolved. Between the comments, small grey lines say what happened to the document (a revision uploaded with its note, a rename, a version named, a thread resolved); the caret opens the details. Every comment is stamped with the revision or version it was written on (`on rev 3`, `on v2`), and the stamp turns amber when the document has moved on, so an old remark is never read against the wrong revision.
- **Review** is "edit and comment", pinned to a spot. On a page, select text and press **Comment**: the thread hangs on that sentence, and you can type a **suggested replacement**; whoever reads it presses **Accept** and the page changes (an ordinary save, so page history has it) and the thread resolves. On an image, click the spot: a numbered pin appears, and the thread sits in the panel. On a PDF, **comment on a page** and give the page number. Each thread has **Reply** and **Resolve** (and Reopen); resolved threads hide behind *Show resolved*. A thread from an earlier revision folds as **Outdated** with one click to open it, and its pin turns amber.
- **@mention an agent on a page and it answers there.** Type `@my-agent` in a page, in a review thread or in Discussion (or Ask it), and an agent whose runner Slate can start (wake mode `push` on the Agents page) reads the part of the page around your mention and the thread, then replies as a comment: in that thread, or as a new Discussion comment when you mentioned it in the page text. Nothing else starts (no task is filed, nothing on the board moves). A second mention while it is working waits for its next turn, and a mention after a run that ended without an answer asks again. Agents never start each other this way, and an agent that cannot see the page is not started.
- Replies notify the asker and earlier thread participants; replying does **not** mean completion. Use **Resolve** (or accept a suggestion) to close a thread. A push or Inbox link opens the document on that very thread.
- Suggestions apply only to their original unchanged page save and an exact, unambiguous quote. If the page changed—even within one history version—or the quote occurs twice without a precise span, re-anchor the suggestion or edit by hand. Comments made while viewing an older file revision keep that revision; replies keep the root thread’s revision.

- **New page** (or the + by a place in the sidebar, or a list's ⋯ menu) starts blank or from a template: meeting notes, project plan, decision record, runbook, agent log.
- **The page sits at the left** of the area, at a reading width (about 90 characters a line); the space on its right is where the chat panel opens, so the two never overlap on a desktop screen.
- **Click anywhere in the page and type.** The page stays formatted while you write, like a Word or Google doc; you never see Markdown. It saves itself as you type. Use the toolbar (or Ctrl+B, Ctrl+I, Ctrl+K for a link), or select text for a small bar with Bold, Italic, Link, Create task and Comment. Type `/` for headings, checklists, tables, quotes, code, dividers, images, a task card, a new task or a link to another page; or start a line with `# `, `- `, `1. `, `[] ` or `> ` for a heading, bullets, numbers, a checklist or a quote. In a table, Tab moves to the next cell. Paste or drop an image to upload it. Tick a checklist box with one click. Clicking a link while reading opens it; while typing, Ctrl+click (⌘-click) opens it. Agents still read and write the page as Markdown, and the parts you did not touch stay exactly as they wrote them.
- **Tasks on a page are live cards**: they show status, due date and assignees and change as the task changes; tick one to complete it, click it to open it. Select some text and press **Create task** to turn it into a task in a task list (never a drawer); the text becomes the saved task's card. A failed save keeps the form open and does not announce success. The task window shows the pages that mention it under **In docs**.
- **Page history** (the clock) keeps one version per sitting of edits, with what changed since and how it read. **Restore** brings an old version back as a new one, so nothing is lost. **Name this version** marks a checkpoint.
- **Organize**: drag pages in the sidebar to reorder or nest them, or use ⋯ → Move. Pages can be favorited, duplicated, copied as Markdown or downloaded. **Archive** hides a page and its subpages; restore or delete them from **Archived pages** at the bottom of the sidebar.
- If someone (or an agent) changes the page while you are typing, a banner offers **Take theirs**, **Keep mine**, or **Download my draft**. Unsaved page text is kept locally per account/document across navigation and reload, subject to browser storage availability. Recovered drafts ask before overwriting; Keep mine still checks for another intervening save. If browser storage is unavailable, download the draft before reloading.
- Signing out or an expired session clears the displayed documents without moving your unfinished page text to the next account. Returning to the saved text discards its abandoned recovery copy; an undo made during a pending save is kept until that undo is saved too.
- Closing **Discussion** on a phone keeps unfinished discussion comments, replies and review fields in this browser, separately for each account and document. Reopen the reply on its original thread to continue. After a reload, reselect the same reviewed text on the same version to recover that review and its suggested replacement. Sending clears the draft; explicitly cancelling a reply or review discards it. Recovery depends on browser storage and does not carry a review onto a different version or selection.
- **Mention someone** with `@` on a page: they get it in their Inbox (and on their phone), agents in their check-in. Opening the page reads it.
- **Search** (`Ctrl K`) finds pages by title and by their text, and files by title or filename.

On a phone the page list comes first and a page opens over it; everything above works the same. Agents read and write pages through MCP (`slate_doc`), including editing one section by its heading and filing tasks from a page.

## Reminders, the daily digest and notifications

**Remind me** in a task's window sets reminders. Tasks have none until you add one. For a task with a due date, **Before due** lets you switch on as many alerts as you like: at the due time, 5, 15 or 30 minutes, 1 or 2 hours, 1 or 2 days, or a week before, or any other amount ("Other", for example 3 days). Tap a lit one to switch it off. The menu stays open so you can pick several. Below that are one-off times: in 20 minutes, in an hour, this evening, tomorrow morning, next Monday, or any date and time. You can add a note and remind someone else (a person or an agent) if you can edit the task; with view or comment access you can only remind yourself. Reminders show as chips under the task's fields; × removes one, and hovering one shows when it goes off.

An alert before the due date follows the date if it moves. A task due on a day without a time counts as due at 5 PM (when it turns overdue), so "1 hour before" is 4 PM that day and "1 day before" is 5 PM the day before. On a repeating task, each new copy gets the same before-due alerts.

When a reminder goes off it lands in your **Inbox** (and an agent's check-in). Every morning Slate also sends you **your day**: what is overdue, due today and waiting on you. Nothing comes on a day with nothing in it.

**Notifications** (the bell at the bottom of the left menu, or search "notifications") sets up the rest:

- **Phone and desktop**: **Turn on for this device** makes the computer or phone buzz with Slate's news, even when Slate is closed. Tapping one opens the task. Do it once on each device you want it on; **Send a test** checks it works. On iPhone this works only in the installed app (see *Slate on your phone*), on iOS 16.4 or later. **On a Mac**, the browser (or Slate, installed as an app) also needs the Mac's own permission: macOS asks once, the first time the browser shows any notification, and if that prompt was dismissed every push arrives in silence. After a test the panel says where to look (System Settings → Notifications → Google Chrome → Allow notifications; turn off Focus). If the browser is missing from that list, quit and reopen it, then send another test so macOS asks.
- **Taskbar count and flashing title**: the window's title reads **(n) Slate** with your **Needs you** count, the same number as the pill (see Needs you above). Install Slate as an app (the install icon in Chrome's or Edge's address bar) and the same number sits on its taskbar or dock icon. Unread chat counts on **Chat** in the left menu instead. While Slate is not the window in front and chat is unread, the title flips between that and **New in #Example Project** every second or so, so an installed app's taskbar label changes too. A web page cannot make a taskbar button flash on its own; the toast, the count and the changing title are what you get.
- **Nudge me again about chat I still have not read**: if a chat message is still unread ten minutes after it arrived (choose 5, 10, 30, 60 minutes or Never), you get one more push, **Still unread: #Example Project**, naming how many messages and from whom. One nudge per newest message; reading the channel ends it.
- **Push me when**: choose what buzzes your devices (mentions, chat replies, **new messages in chats you can see**, assignments, waiting on you, due dates, reminders and your daily digest are on to start). Everything but plain chat messages still lands in the Inbox; a chat message only notifies, the channel's unread badge is its record. On Windows these come as the usual notification toasts (Chrome and Edge hand them to Windows), even when Slate's tab is closed, as long as the browser is running. A busy channel replaces its own toast rather than stacking them.
- **Daily digest**: on or off, and the hour it comes.
- **Upcoming reminders**: everything you are going to be reminded about.

These choices follow your account, so the phone and the computer agree. Agents set reminders through MCP (`slate_reminder`).

## Duplicates

When the same thing was filed twice, open one of them and add **Duplicate of** under Dependencies: its card shows a *Duplicate* badge and the original lists it under *Duplicates*. Put the duplicate away when you are done with it. Agents can mark duplicates the same way (`slate_item` `link` with kind `duplicate_of`).

## Archive, trash and taking your data with you

**Archive** in the sidebar (under More) is where work goes when it leaves the board: **Done** (finished and cancelled tasks, newest first, with **Put away** to tidy them), **Put away** (**Bring back** returns one to the board), **Lists and sections** you archived (Restore), and the **Trash**. On a task the verb is **Put away**, so it never shares a name with this page; agents and the API still call it `archive`.

**Move to trash** is in a task's ⋯ menu, a card's ⋯ menu and the search box. The task (and its subtasks) disappears from every board straight away; the toast's **Undo** brings it back. In the trash, **Restore** puts it back where it was (if it was archived before, it comes back archived), and **Delete forever** removes it for good after you confirm. Anything left in the trash is deleted for good after 30 days; each row says how long it has left. Admins can **Empty trash**. A trashed task can still be opened; it shows a red banner with Restore. Its history stays even after it is deleted for good.

**Export JSON** and **Export CSV** at the top of the Archive page download everything; a list's or section's ⋯ menu exports just that. JSON keeps everything (comments, checklists, links, docs, and the chat of what you export, with threads and reactions) for a program to read back; CSV is one row per task for a spreadsheet. Admins also get **Backup**, a copy of the whole database you can keep somewhere safe. Agents can export too (`slate_export`, or `GET /api/v1/export`).

## Giving agents access

**Start here:** [Connect your own agent](docs/connect-your-agent.md) explains the guided
**Agents → Add my agent** screen. Choose local execution, MCP/API access, or manual
handoff; they are different connections. Creating a token alone does not make an agent
available to run. The connection panel distinguishes an unused code, connected credentials,
a passed agent test, an offline computer and a setup problem. Runtime approvals stay in the
agent's own application; typing “yes” on a card is not command authorization.


Each agent gets its own account and token. The very first account (yours) is created once, on a fresh install, by `POST /api/v1/bootstrap`; it returns your admin token a single time, so store it straight away. After that, an admin token creates accounts (`POST /api/v1/accounts`) and tokens (`POST /api/v1/accounts/{id}/tokens`). A token is shown only when it is created; Slate keeps only a fingerprint of it, so a lost token is revoked (`DELETE /api/v1/tokens/{id}`) and replaced, never recovered. Agents talk to Slate over its API or through MCP (`/mcp`). An agent that can receive web calls can also register a webhook to be told when its items come due.

The quick way: **Agents → Add my agent**. Give the agent a name (its handle is filled in for you), choose what it may do (Read, Write or Admin; Write is the usual choice) and label the token with the machine that will hold it. Slate shows the token once, with setup ready to paste into Claude Code, a `.mcp.json`, a Python or TypeScript agent, or a quick `curl` check, all pointing at this board. **Manage** on an agent's card shows its tokens (when each was last used), how many changes it made each day for two weeks and through what (API, MCP, the board), and lets you make a new token, revoke one, or disable the agent; disabling stops all its tokens at once and keeps its history. Everything an agent developer needs is in `docs/agents/`.

How agents pick up work: an agent asks Slate for its next task and gets the most important one it can do now (assigned to it, not waiting on an unfinished task, not taken by another agent, by priority, then due date). Its Working badge appears on the card straight away. If the agent stops checking in, Slate marks the attempt as needing a check rather than starting it again blindly. Its last runtime session must be checked before retrying, so a remote job that is still running cannot be duplicated. When it finishes, its comment, the time it spent and the new status arrive together; it may also pass the task to another agent (you see a *Handed to* comment) or leave it *ready for review* or *blocked* on you.

## Members, sharing and private lists

**Members** in the sidebar (owners and admins) lists every person and agent with their role, what they can reach and when they were last active. There are four roles: the **owner** (you) and **admins** see and change everything; **members** see every section and list that is not private; **guests** see only what is shared with them. Change a role from the dropdown; only the owner makes someone an admin, and choosing **Owner** for someone hands ownership over (you become an admin). **Invite** makes a person or guest and shows their sign-in token once; you can share a list with them in the same step. **Access** on a row shows, section by section and list by list, what that account can do and lets you share more.

**Share** sits at the top of every list and section (and in its ⋯ menu): it shows who reaches it and why, lets you add a person or agent at **Can view**, **Can comment** or **Can edit**, and has the **Private** switch. A private list or section disappears for members unless you share it with them, and shows a lock in the sidebar. Someone with view or comment access sees a note at the top of the list saying what they can do. Changes reach open boards at once: unshare a list and it vanishes from that person's screen.

Docs follow the place they live in: a doc in a list or section is seen and edited by whoever can see and edit that list or section (subpages too), and docs at the workspace level are for members, not guests.

Agents follow exactly the same rules. When you add an agent (**Agents → Add my agent**) you can make it a guest, and narrow its token to **only some lists** at most view, comment or edit; that token cannot see or touch anything else, even if the agent's account could (nor subscribe a webhook, which would hear the whole account's reach; give it an unlimited token for that). Only the owner can issue or revoke the owner's own tokens or disable the owner account; admins manage everyone else.

**Agents may not…** at the top of Members is one rule for every agent, whatever its token or role: tick what no agent may ever do here, and it is refused with "agents may not … here (workspace rule)". Out of the box agents may not delete things for good (purge, empty the trash, delete a doc or a file), archive sections or lists, change sharing (shares, invitations, roles, the Private switch), or issue tokens and manage accounts. You can also stop them moving tasks to the trash, changing automations and webhooks, or exporting. Each box saves as you click it and the change is in the audit log. People, you and your admins included, are never limited by it, and Slate's own housekeeping (emptying the trash after its days, automations) carries on.

## The audit log

**Audit log** in the sidebar is every change ever made, newest first: who did it (person or agent), when, through which door (the board, the API, MCP, or Slate itself) and what it touched, with the before and after under **Details**. Filter by person, People or Agents, kind of change, door, section or list, dates, or a word in the task title; **Export CSV** downloads exactly what the filters show. Nothing in it can be edited or deleted. Owners and admins see everything; anyone else sees the history of the lists they can read and their own actions. Agents read it with `slate_audit` or `GET /api/v1/audit`.

**Revert from the log.** A change someone else made to a task (a rename, a date, a priority, the description, tags, the status, who is on it, a move to another list) has a **Revert** button on its row, both here and on the task's History tab. It asks first, then puts the field back as a new change made by you: the log keeps the agent's change and yours, nothing is erased. If the field has changed again since (someone edited it after the row you are looking at), Slate refuses and the button reads **Changed since** with the reason. Creations, comments and reports have no Revert. Agents do the same with `POST /api/v1/events/{id}/revert` or `slate_item revert`.

## Health

**Health** in the sidebar (owners and admins; also Ctrl K → "health") says whether Slate is working. The top line is the verdict; below it every check, with problems first. **Background jobs** shows each job Slate runs on its own (reminders, the digest, due nudges, recurring tasks, trash purge) with when it last worked and, if it is failing, the error. **Agents and tokens** shows each token's calls in the last hour, how many failed and which were slow, with the failing calls listed below (route, status, who). Then response times, failed automation runs in the last day, queues, and how big the database is and how much disk is left.

If a job fails three runs in a row you get a notification (inbox and phone), and another when it works again. Agents can read the same report with `GET /api/v1/health` or `slate_health`, and anyone who can reach the server can read the short verdict at `/health`. The numbers start over when Slate restarts.

## Agents at work

When an agent picks up a task it reports on it: working (with how far along), blocked, ready for review, or done. The report shows as a small badge on the card, "🤖 My Agent · Working 40%", and in full at the top of the task window with the agent's note. A working badge pulses; blocked is red and says **Waiting on Alex** when the agent named you (or whoever it named) and **Agent blocked** when it named nobody; ready for review is amber; a working report nobody has refreshed for six hours fades. Done clears the badge and completes the task. When the agent has done what it can and the rest has to wait for the task's due time (you already gave it the go, and it set or kept the due time), it says so in its comment and leaves no badge: the card is not waiting on you, it stays assigned to the agent, and the due time runs the agent again. Each report is also in the task's history, and the note is posted as a comment from the agent. An agent only marks a card ready for review when it says the work is finished. If it stops without saying where things stand (for example while it is still waiting on background work), the card pauses as *Waiting on* you with a "Paused" note; reply on the card to let it carry on.

**Agents** under **More** in the left menu opens the agents page: every agent with a green dot when it is around right now, what it is on (click a task to open it), how many open tasks it has, how busy it has been today, and a live feed of everything agents did, newest first. Click "Feed" on an agent to see only its changes ("All" goes back). Nothing there needs a refresh.

**Next up**, above the feed, says what each agent is about to do in the next 24 hours, soonest first: a task's run time (6:00 AM on the day for a dated task, the due time if it has one, "now" for a task with no date), a **Run now** you pressed, a question asked of it, and the tasks it is holding right now with how long it has had them. It uses the same rule the agents use to pick work, so if a task is not there, the agent will not pick it up either (it is blocked, waiting on someone, or not due yet). Click a task to open it. Your daily digest carries the same list as **Agents today**, so the morning note says what the agents will do before you look. Agents read it with `GET /api/v1/agents/plan` or `slate_work plan`.

To give a task to an agent, open it and press **🤖 Hand to an agent** by the comment box: pick the agent and say what it should do. Slate assigns the agent and posts your words as a comment that mentions it, in one step; the agent finds both in its inbox and its badge appears when it starts.

**When agent tasks run by themselves.** A task assigned to an agent that has a runner (see `docs/agents/runner.md`) starts on its own: at once if it has no due date; at 6:00 in the morning on the due day if it has a due date without a time, so it is done by the time you look; or at the due time if it has one. To start it sooner, press **Run now**: the robot button on the card (hover), in the card's ⋯ menu, or by the comment box in the task window, next to **Ask** and **Hand to an agent**: the three ways the next turn starts sit together. It is only offered when an agent is assigned and the task is open. The card shows **Waiting for pickup…** until it has a working report. An agent working on another card is no reason to wait: each agent runs several cards at once. Run now pressed by the agent itself (another session of the agent filing a card for its runner) starts it too. An unexplained wait says **Not picked up yet. Check agent status.** A recent roster check with no connection says **No live connection seen…** Both link to Agents. These hints also appear when opening an already-due card, not just after pressing Run now. Independent card sessions can run concurrently up to the runner's configured limit (for example four per agent); capacity above that waits and drains automatically. A task requested while the runner is off is not lost: the runner checks outstanding work when it reconnects. When an agent says where it is working (its chat, session or pull request), the badge has an **Open ↗** link to it. For an agent on Hermes Agent whose adapter is set to send session links, that link opens the card's own session in Hermes Desktop, on that agent's profile, so you can watch or join the conversation without hunting for it. The badge goes away when the run finishes done, but the task window keeps an **Open My Agent's session ↗** line under the status for every agent that worked the card, so you can still get to the conversation afterwards (and a reply you post on the card continues in that same session).

**Show an agent (or anyone) a screenshot on the card.** Click in the comment box and press **Ctrl V** with a screenshot on the clipboard, or drag a file onto the box. The picture appears above the box as a small thumbnail (the × takes it off again); type what you want to say, or nothing, and press Enter. The comment shows the picture itself; click it to open it full screen. The file is also in the task's Attachments. The agent is told which files are on the card and can open them.

**Talking with an agent on the card.** An agent with a runner answers on the card itself: its reply for each run is a comment by the agent, and the task window's Activity reads as a chat (older exchanges fold to one line; click to open). While it works, a bubble at the top of Activity shows what it is doing and when it last reported ("updated 12 s ago"). When it needs you (a sign-in, a decision, an approval), the badge says **Waiting on Alex** (your first name), the card is under **Needs you** on My Work (and counts in the pill, the title and the app icon), and your phone gets "My Agent needs you on: …". Answer ordinary questions in the card's comments; your answer becomes its next turn and the badge says **Answered** until pickup. Command approval is different: use the agent application's native approval control. The live connection stays open while it waits; typing "approve" here does not authorize a command. Until the agent answers, three bouncing dots at the foot of the thread say "My Agent is typing…". You can keep talking after it answers: a follow-up question wakes it again even when the card is Ready for review or done. The **Ask** picker under the comment box starts on the card's agent; on a shared project's card it starts on that project's own agent, else the agent you asked last, so Enter sends your words to the right agent. Typing an agent's `@handle` ("@other-agent can you…") switches the picker to that agent unless you picked someone by hand, and a comment that `@mentions` only people ("@alex did you…") is a plain comment, so the agent is not woken; pick **No one (comment)** for a plain note. You are not in the list: you never need to ask yourself. When the agent marks its answer **Ready for review**, press **Mark reviewed** on the badge in the task window once you've read it. A blocked badge ("Waiting on Alex", "Agent blocked") has a **✕ Clear** button in the task window that takes it off the card; it leaves the card's Waiting on field alone. An agent you only asked something on a card that is not its own never marks that card blocked on someone else: saying it is another person's job is just its answer. Only two things clear Ready for review: **Mark reviewed**, or changing the card's status yourself (dragging it to another column, or completing it). Opening the task window and closing it again never does. **Run now** while the agent is already working says so ("My Agent already has it, working 4 min"); on a card waiting on you, Run now tells the agent to go on without an answer.

**Agents in other apps.** Some agents live in another company's app (ChatGPT, Grok…) and Slate can't start them. On the Agents page an admin sets how each agent is woken: **a runner** (Run now), **its own app checks Slate** (the card says **Nudge**: you get a notification to open that app and say "work Slate"; the Agents page has the prompt to save as a scheduled task there), or **by hand** (the card says **Copy brief**: the task as a prompt to paste into the agent's app). Paste the agent's answer back as a comment. If its last line is `SLATE: done`, the task is completed; `SLATE: blocked waiting_on alex` marks it waiting on Alex. Details: `docs/agents/pull-agents.md`.

**Ask someone.** To put a question or request to someone on a task without handing them the task, pick them in the **Ask…** menu beside the comment button (people and agents), then send. Every question shows an "Asked … · Open" pill, including questions to agents, lands in their check-in and in My Work under **Asked of you**, and an agent with a runner is woken to answer. It stays open when they reply; **Resolve** (for them or for you) closes it. A card with a question waiting for you shows a small orange "? 1".

### Requests, replies and notification delivery

**Asks and agent runs are separate.** Replies and task completion leave Asks open until
Resolve. An agent that finishes a taken turn will not repeatedly run the same open Ask;
new or reassigned questions can start another turn. If an agent was only asked a question,
not assigned the task, its explicit Done answer resolves just the questions it took, never
questions arriving mid-run. Human replies always require explicit Resolve. Open Ask pills
remain visible even after an agent's typing indicator stops.

An Ask on a task reaches its recipient's Inbox and enabled push devices without also
@mentioning them or making them watch the task. It uses the **Mentions** notification choice.
Replies notify the requester and people already participating; a reply such as “I need more
time” does not settle the request. Reading or clearing its notification does not resolve it.
Temporary push-provider failures are retried for up to a day, including after a server
restart, without resending to devices already recorded as successful. Disabled accounts and
people who lose access receive neither new pushes nor retries. A private task reference in
shared chat cannot reveal its title through the notification.

**Cards filed by other systems.** A system that files cards for things it watches (a broken service, an email) can tag each card with its own reference. A reference belongs to one card only, closed, archived and trashed cards included: filing a second card with the same one is refused with "external_ref already used by <id>", so the system finds its existing card instead of making a duplicate. If that card is closed, the refusal says so and tells the system to reopen it: a problem that comes back reopens its old card, with its history, rather than starting a new one. A task's **acceptance criteria** reach the agent as a "Done means:" section of its brief.

Agents report with `POST /api/v1/items/{id}/report` or the MCP `slate_item` `report` action; `docs/agents/quickstart.md` has the details. Each agent token is limited to 1,200 reads and 300 changes a minute so a runaway agent cannot swamp Slate; the agents page shows when one hits the limit.

## Watching changes as they happen

Every change anyone makes is an event, and agents can follow them two ways with any token that can read:

- **Live:** `GET /api/v1/events/stream` is a Server-Sent Events stream (the board uses it too). Each change arrives within a second, named by its type (`account.created`, `item.updated`…). A client that drops and reconnects picks up exactly where it left off, nothing missed and nothing twice.
- **Polling:** `GET /api/v1/events?since=<last id you saw>` returns the next page of changes, oldest first, for agents that check in on a timer instead of staying connected.

Both take `type=` (one or more types, comma-separated) and `item_id=` to narrow what you see.

## Being told: webhooks

An agent that can receive web calls can ask Slate to call it when something happens, instead of watching the stream. It needs a token with `write`.

1. **Subscribe:** `POST /api/v1/webhooks` with `{"url": "...", "events": ["item.due", "item.assigned", "item.commented"], "filter": {"assignee_me": true}}`. The reply includes a `secret` (`whsec_...`). **Store it now: Slate never shows it again.** Lost it? Disable the subscription and create a new one.
2. **What it can ask for:** event types such as `item.created`, `item.updated`, `item.status_changed`, `item.assigned`, `item.commented`, `item.archived`, `item.due_soon`, `item.due`, `item.overdue`, or `["*"]` for everything. The filter narrows it: `assignee_me` (only items you are assigned to, including the moment you are taken off one, plus items you watch and comments that @mention you; a mention added by editing a comment arrives as `item.mentioned`), `list_ids`, `section_ids`, `tags`, and `due_lead_hours` (how early "due soon" arrives; 24 by default).
3. **What arrives:** a JSON POST with the change (who did it, what field, before and after) and a short summary of the item. Header `X-Slate-Event` names the type.
4. **Check it is really Slate:** every call carries `X-Slate-Signature: sha256=<code>`. Compute HMAC-SHA256 of the raw body with your secret and compare; reject the call if they differ. Copy-paste versions for Python and Node are in `docs/webhooks.md`.
5. **Reply quickly with any 2xx.** If your endpoint is down or answers 5xx or 429, Slate tries again after 10 s, 1 min, 5 min, 30 min and 2 h, then gives up. Other 4xx answers are not retried. You may occasionally get the same event twice; the body's `id` is the same both times.
6. **Manage it** (or ask the agent to use its `slate_webhook` MCP tool, which does the same): `GET /api/v1/webhooks` lists yours; `PATCH /api/v1/webhooks/{id}` changes the url, events or filter, or pauses it with `{"disabled": true}`; `GET /api/v1/webhooks/{id}/deliveries` shows every attempt and its result; `POST /api/v1/webhooks/{id}/test` sends a test call right now and tells you what happened.

Addresses must be `https://`, or plain `http://` only inside the private network (a VPN or LAN address, `localhost`, a machine name like `nas`).

**Due reminders:** Slate itself raises `item.due_soon` (a day ahead, or your chosen lead), `item.due` at the due time and `item.overdue` an hour later if the item is not done. Each comes once per due date; moving the due date later starts them over. Done, cancelled and archived items stay quiet. These appear as changes by the `slate` account.

## Connect your agent (MCP)

Any agent that speaks MCP can use Slate directly. It gets twenty-eight tools (the `TOOLS` table in `src/slate/mcp_tools.py`, in this order): `slate_item`, `slate_comment`, `slate_chat`, `slate_checklist`, `slate_view`, `slate_favorite`, `slate_automation`, `slate_workload`, `slate_reminder`, `slate_inbox`, `slate_list`, `slate_section`, `slate_tag`, `slate_recurrence`, `slate_webhook`, `slate_account`, `slate_today`, `slate_checkin`, `slate_work`, `slate_doc`, `slate_agents`, `slate_health`, `slate_batch`, `slate_events`, `slate_share`, `slate_audit`, `slate_export` and `slate_search`; `docs/agents/reference/mcp.md` lists every action. An agent should start each run with `slate_checkin` (its open work and what changed for it) and say what it is doing with `slate_item` `report`. `slate_item` `activity` reads a task's comments and history together (what a person sees beside the task); `slate_comment` comments, edits, reacts and mentions (`@alex`); `slate_checklist` ticks steps inside a task; `slate_item` `attach` adds a file (base64) and `watch` follows a task. Each takes an `action` (for example `create`, `update`, `split`) and a `params` object; the tool descriptions tell the agent what each action needs. Filing an item through MCP still makes exactly one item; subtasks come only from `split`. Everything an agent does over `/mcp` shows in the history as "via MCP"; a stdio setup (`slate-mcp`) is a client of the REST API, so its changes read "via API".

Each agent needs its own Slate token (an admin creates one on the Agents page, or with an admin token's `slate_account create_token`). In the examples below, replace `slate_...` with that token and `https://slate.example.com` with your Slate's address.

There are two ways to connect:

- **Over the network (streamable HTTP):** the agent talks to `<Slate address>/mcp` and sends `Authorization: Bearer <token>` on every request. Nothing to install.
- **As a local program (stdio):** the agent's app starts `slate-mcp` on your machine, which forwards every call to Slate's API. It needs `SLATE_URL` and `SLATE_TOKEN` set, and a checkout of the Slate repo with `uv` installed (for example `/path/to/slate`).

**Claude Desktop** (stdio). Open Settings → Developer → Edit Config, add this to `claude_desktop_config.json`, save, and restart Claude Desktop:

```json
{
  "mcpServers": {
    "slate": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/slate", "slate-mcp"],
      "env": {
        "SLATE_URL": "https://slate.example.com",
        "SLATE_TOKEN": "slate_..."
      }
    }
  }
}
```

**Claude Code** (streamable HTTP, nothing local to run):

```
claude mcp add --transport http slate https://slate.example.com/mcp --header "Authorization: Bearer slate_..."
```

Or the same over stdio:

```
claude mcp add slate -e SLATE_URL=https://slate.example.com -e SLATE_TOKEN=slate_... -- uv run --directory /path/to/slate slate-mcp
```

**Any other MCP client** that supports streamable HTTP: server URL `https://slate.example.com/mcp`, header `Authorization: Bearer slate_...`. A missing or wrong token gets HTTP 401.

Chat apps can also attach two read-only documents: `slate://today` (your day) and `slate://item/<id>` (one item with its subtasks, comments and recent history), both as plain markdown.

An agent can label its work with a run or session name by passing `session_ref` with any tool call; it is stored on every change that call makes.

## Recurring tasks

A recurring rule makes a fresh item on a schedule: the bulk trash every other Wednesday, the AC filter on the 1st. Tell an agent in plain words and it writes the rule, or set it yourself from the board. Each item it makes is a normal item (tick it done, comment, reassign); finishing one never changes the rule.

**From the board.** Open an item and press **↻ Make recurring** (next to Repeats). Pick how it repeats:

| Choice | For example |
|---|---|
| Daily, every N days | water the plants |
| Every weekday (Mon–Fri) | stand-up notes |
| Weekly, on chosen days, every N weeks | bulk trash every 2 weeks on Wednesday |
| Monthly on a date (several allowed, or **Last day**), every N months | rent on the 1st; invoices on the last day |
| Monthly on a weekday: 1st/2nd/3rd/4th/last, several allowed | the 2nd and 4th Wednesday |
| Yearly on a date | a renewal on March 15 |
| Yearly on a weekday | the 2nd Sunday of May |
| Custom (RRULE) | anything else, written as an RFC 5545 rule |

Then choose when it **ends** (never, after a number of times, on a date), what each new one is **called** (`{date}` adds its date), who each new one is **assigned to** (the rule keeps its own list: it starts as this item's assignees, and reassigning one item later never changes the next ones), what happens **if one is missed** (*still create the next one*, *move it forward*, or *skip while one is open*), and how many days **ahead** each one appears. While you choose, the box shows the rule in plain words and the next five dates, worked out by Slate, so what you see is what will happen. A rule Slate cannot use says why before you can save it.

**On a schedule or when done.** The switch at the top of the box decides when the next one comes. *On a schedule* (the default) makes one for every date, done or not. *When done* waits: marking this one done makes the next, counted from the day you did it, so "every 3 months" means three months after you last changed the filter. With *When done* you can also tick **Reopen this task**: instead of a new item, the same one goes back to open with the next date and its checklist unticked (its history stays in one place).

**Skip weekends** moves a date that falls on Saturday or Sunday to the Monday after. **Carry checklists over** (on by default) gives each new one the last one's checklists, unticked. Before-due alerts always carry over to the next one.

Cards show what repeats and how (**↻ Weekly**, **↻ Every 2 weeks**, **↻ Monthly · when done**; hover for the whole rule). The **Calendar** draws the dates a schedule will fill that are not items yet as dashed chips, and the **Timeline** marks them on the row of the newest one; click one to open the task that carries the rule.

The item you started from stays the current one, with its own due date; the rule makes the next ones. (An item with no due date becomes the first one and takes the first date.) On an item that already repeats, the Repeats line has **Edit**, **Pause** / **Resume** and **End**. Editing changes future items only.

- Items appear **two weeks ahead** by default (`lead_days`), so you see what is coming.
- A rule with no time of day makes items **due at 5 pm workspace time** on their date. Give a time ("at 3 pm") and that is the due time. Daylight saving never moves it.
- Titles can include the date: "Bulk trash {date}" becomes "Bulk trash Wed Nov 11".
- **If you miss one**, the rule's policy decides: *still create the next one* (the default: the missed one stays overdue and the next still appears), *move it forward* (the missed item's due date jumps to the next date, so there is never more than one), or *skip while open* (no new one appears until the open one is done or archived).
- Pausing a rule stops it; resuming starts again from today without catching up. Ending a rule is final; items already made stay.
- "Run now" makes any items due within the rule's window straight away instead of within the minute.

What you say, and the rule an agent writes (the start date anchors "every other" and "every 3"):

| You say | `rrule` | `dtstart` |
|---|---|---|
| "Bulk trash every other Wednesday, next pickup Nov 11" | `FREQ=WEEKLY;INTERVAL=2;BYDAY=WE` | `2026-11-11` |
| "Change the AC filter on the 1st of every month" | `FREQ=MONTHLY;BYMONTHDAY=1` | `2026-11-01` |
| "Weekly review every Friday at 3 pm" | `FREQ=WEEKLY;BYDAY=FR;BYHOUR=15` | today |
| "Monthly audit on the last weekday of the month" | `FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1` | today |

## Importing from ClickUp

To move from ClickUp, you (or an agent) copy your ClickUp workspace into Slate with a command on the server: `slate import clickup --team <workspace id>` with the ClickUp API token in `CLICKUP_TOKEN` prints a dry-run report and writes nothing; add `--apply` to write it. Running it again later is safe: tasks that came over before are updated, not doubled, and comments and checklists already there are not added twice. A mapping file (`--mapping FILE`) says which ClickUp list goes where; the options are in `docs/importer.md`.

### The dry-run report

The dry run is the plan: it reads ClickUp and writes nothing. Read it before you run the command again with `--apply`.

- **Destinations:** how many tasks land in each Slate list, and each list's columns. Without a mapping file every ClickUp list keeps its name, in a section named after its ClickUp space. A mapping file can send lists elsewhere, and its `mirror` option gathers every task whose name starts with a prefix you choose into one shared list, tagged. Closed ClickUp tasks come across archived, so their history is kept but they stay off the board.
- **Recurrence rules:** a chore kept in ClickUp as a run of dated copies (three or more tasks with the same name on a regular schedule) becomes one rule, written in plain words, for example "every 2 weeks on Wednesday, starting Wed Nov 11". Only the copies due in the next two weeks are imported; the rule makes the rest as they come up. Check each sentence: a wrong one is easier to fix now.
- **Not collapsed:** same-named tasks whose dates follow no clean schedule. They come across as separate dated tasks.
- **Lapsed:** a regular schedule whose copies are all closed. No rule is made; the copies come across as history.
- **Twins:** closed exact copies of a task in another list. They are skipped.
- **Dropped:** the later dated copies the rules replace.
- **Flattened:** subtasks deeper than four levels. Slate nests four deep, so anything below that moves up under an ancestor, with a note. A subtask whose parent is not imported becomes a top-level task, also with a note.
- **Unassigned:** tasks assigned to someone without a Slate account. They arrive with no assignee.
- **Unmapped:** ClickUp lists that are not imported at all.

Every imported item keeps a footer line with its ClickUp link, and ClickUp comments arrive as comments with the original author and time at the top.
