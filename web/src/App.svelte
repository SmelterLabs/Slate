<script lang="ts">
  import { workspaceFormatter } from './lib/timezone';
  import { untrack } from 'svelte';
  import { docOf, isKiosk, pageOf, parseRoute, type Page } from './lib/route';
  import { docs } from './lib/docs.svelte';
  import { inbox } from './lib/inbox.svelte';
  import { chat } from './lib/chat.svelte';
  import { chatChannelOf, chatHref, threadOf } from './lib/chat';
  import { chatPanel } from './lib/chatpanel.svelte';
  import { chatPanelOf, FLASH_MS, flashTitle, windowBadge } from './lib/chatpanel';
  import ChatPage from './components/chat/ChatPage.svelte';
  import ChatPanel from './components/chat/ChatPanel.svelte';
  import { board, scopeKey } from './lib/store.svelte';
  import { checkForNewBuild } from './lib/freshness';
  import { takeInvitation } from './lib/collaboration';
  import { takeShare } from './lib/share';
  import { share } from './lib/share.svelte';
  let invitation = $state(takeInvitation());
  import TokenScreen from './components/TokenScreen.svelte';
  import Nav from './components/Nav.svelte';
  import NeedsBadge from './components/NeedsBadge.svelte';
  import FavoritesBar from './components/FavoritesBar.svelte';
  import AgentChatButton from './components/AgentChatButton.svelte';
  import ScopePage from './components/ScopePage.svelte';
  import AgentsPage from './components/AgentsPage.svelte';
  import ArchivePage from './components/ArchivePage.svelte';
  import DocsPage from './components/docs/DocsPage.svelte';
  import MembersPage from './components/members/MembersPage.svelte';
  import AuditPage from './components/audit/AuditPage.svelte';
  import HealthPage from './components/health/HealthPage.svelte';
  import ShareDialog from './components/ShareDialog.svelte';
  import TaskModal from './components/task/TaskModal.svelte';
  import MediaViewer from './components/task/MediaViewer.svelte';
  import { task } from './lib/task.svelte';
  import ShortcutSheet from './components/ShortcutSheet.svelte';
  import TagManager from './components/TagManager.svelte';
  import NotificationSettings from './components/NotificationSettings.svelte';
  import { notifySettings } from './lib/notify.svelte';
  import { syncPush } from './lib/push';
  import HomePage from './components/work/HomePage.svelte';
  import InboxPage from './components/work/InboxPage.svelte';
  import CommandPalette from './components/work/CommandPalette.svelte';
  import PullToRefresh from './components/PullToRefresh.svelte';
  import Skeleton from './components/Skeleton.svelte';
  import EmptyState from './components/EmptyState.svelte';
  import Icon from './components/Icon.svelte';

  let navOpen = $state(false);
  let sheet = $state(false);
  let palette = $state(false);
  /** My Work or Inbox, when the hash names one; otherwise the scope page. */
  let page = $state<Page | null>(pageOf(location.hash));
  const kiosk = isKiosk(location.search, location.hash);
  let docId = $state<string | null>(docOf(location.hash));
  let chatChannel = $state<string | null>(chatChannelOf(location.hash));
  let chatThread = $state<string | null>(threadOf(location.hash));
  /** A list or section on its Channel tab (`#/list/<id>/chat`). */
  let scopeChat = $state(parseRoute(location.hash).chat ?? false);

  // The URL names the scope and view: it survives a reload, and the back button works.
  function readHash() {
    if (location.hash.startsWith('#/invite?')) invitation = takeInvitation();
    // The phone's share sheet (`/?title=…&url=…`, manifest share_target) or `#/share?…`: read
    // it into a quick-add draft, strip it from the address and land where quick add is.
    const shared = takeShare();
    if (shared) {
      share.line = shared.line;
      share.body = shared.body;
    }
    const r = parseRoute(location.hash);
    page = pageOf(location.hash);
    navOpen = false;
    docId = docOf(location.hash);
    chatChannel = chatChannelOf(location.hash);
    chatThread = threadOf(location.hash);
    scopeChat = r.chat ?? false;
    // `?chat=<key>` on any page opens the chat panel there (a link from an agent or a message);
    // a narrow window shows the Chat page instead, since the panel would cover everything.
    const wantPanel = chatPanelOf(location.hash);
    if (wantPanel && page !== 'chat') {
      if (chatPanel.fits) chatPanel.show(wantPanel.channel, wantPanel.thread);
      else {
        location.hash = chatHref(wantPanel.channel, wantPanel.thread);
        return;
      }
    }
    // These pages leave the scope alone. A doc or chat can still open a task (`?task=`).
    if (page === 'docs' || page === 'chat') {
      const taskId = new URLSearchParams(location.hash.split('?')[1] ?? '').get('task');
      if (taskId && board.openId !== taskId) void board.open(taskId);
      return;
    }
    if (
      page === 'agents' ||
      page === 'archive' ||
      page === 'members' ||
      page === 'audit' ||
      page === 'health'
    )
      return;
    if (scopeKey(r.scope) !== scopeKey(board.selection)) {
      board.clearSelection();
      board.q = '';
    }
    board.selection = r.scope;
    board.viewId = r.viewId;
    if (r.viewId) board.rememberView(r.scope, r.viewId);
    // `?task=<id>` in the hash opens that task's window (copy link, reload).
    const taskId = new URLSearchParams(location.hash.split('?')[1] ?? '').get('task');
    if (taskId && board.openId !== taskId) void board.open(taskId);
  }

  $effect(() => {
    untrack(readHash);
    window.addEventListener('hashchange', readHash);
    // A view change still waiting to be saved goes out as the page closes or reloads.
    const flush = () => board.flushDrafts();
    window.addEventListener('pagehide', flush);
    return () => {
      window.removeEventListener('hashchange', readHash);
      window.removeEventListener('pagehide', flush);
    };
  });

  // Start the board whenever a token appears (page load with a stored token, or sign-in).
  let started = false;
  $effect(() => {
    if (board.token && invitation === null && !started) {
      started = true;
      if (!board.ready) void board.start();
    }
    if (!board.token) started = false;
  });

  // The inbox follows the board's stream once it is live; opening a task reads its entries.
  $effect(() => {
    if (!board.ready) return;
    untrack(() => inbox.start());
    return () => inbox.stop();
  });

  // The docs index feeds the palette, the favorites bar and the Docs page.
  $effect(() => {
    if (!board.ready) return;
    untrack(() => docs.start());
    return () => docs.stop();
  });

  // Chat's channel list and unread badges follow the stream the same way.
  $effect(() => {
    if (!board.ready) return;
    untrack(() => chat.start());
    return () => chat.stop();
  });

  // A new deploy reaches an open board by itself: look when the live stream comes back (a
  // deploy restarts the server) and when the tab is shown again (lib/freshness.ts).
  $effect(() => {
    if (board.conn === 'live') untrack(() => void checkForNewBuild());
  });
  $effect(() => {
    const onShow = () => {
      if (!document.hidden) void checkForNewBuild();
    };
    document.addEventListener('visibilitychange', onShow);
    return () => document.removeEventListener('visibilitychange', onShow);
  });

  // Phone/desktop notifications: keep this browser's subscription current with the server, and
  // let a tapped notification route an already open window (public/sw.js posts the link).
  $effect(() => {
    if (board.ready) void syncPush().catch(() => {});
  });
  $effect(() => {
    if (!('serviceWorker' in navigator)) return;
    const onMessage = (e: MessageEvent) => {
      if (e.data?.type === 'slate-open' && typeof e.data.url === 'string') location.hash = new URL(e.data.url).hash;
    };
    navigator.serviceWorker.addEventListener('message', onMessage);
    return () => navigator.serviceWorker.removeEventListener('message', onMessage);
  });
  $effect(() => {
    if (board.openId) {
      const id = board.openId;
      untrack(() => inbox.readItem(id));
    }
  });

  // The title and, for Slate installed as an app, the count on its taskbar or dock icon: Needs
  // you (lib/attention.ts), the same number as the pill and My Work, because that is the count
  // that means "stop and act". Unread chat keeps its own badge on Chat. While the window is not
  // in front and chat is unread, the title flips between that and "New in #Todo" every
  // FLASH_MS: a browser cannot flash a taskbar button, so the title, the toast and the push
  // (service/push.py `chat`, `renudge`) are what chat gets.
  let inFront = $state(typeof document === 'undefined' || (!document.hidden && document.hasFocus()));
  $effect(() => {
    const update = () => (inFront = !document.hidden && document.hasFocus());
    window.addEventListener('focus', update);
    window.addEventListener('blur', update);
    document.addEventListener('visibilitychange', update);
    return () => {
      window.removeEventListener('focus', update);
      window.removeEventListener('blur', update);
      document.removeEventListener('visibilitychange', update);
    };
  });
  $effect(() => {
    const { title, count } = windowBadge(board.token ? board.needsMe.length : 0);
    const flash = board.token && !inFront ? flashTitle(chat.channels) : null;
    document.title = title;
    const nav = navigator as Navigator & { setAppBadge?: (n?: number) => Promise<void>; clearAppBadge?: () => Promise<void> };
    if (count) void nav.setAppBadge?.(count).catch(() => {});
    else void nav.clearAppBadge?.().catch(() => {});
    if (!flash) return;
    let other = true;
    const timer = setInterval(() => {
      document.title = other ? flash : title;
      other = !other;
    }, FLASH_MS);
    return () => {
      clearInterval(timer);
      document.title = title;
    };
  });

  const missing = $derived.by(() => {
    const s = board.selection;
    if (s.kind === 'list') return !board.lists[s.id];
    if (s.kind === 'section') return !board.sections.some((x) => x.id === s.id);
    return false;
  });
  const where = $derived.by(() => {
    const s = board.selection;
    if (s.kind === 'list') return board.lists[s.id]?.name ?? '';
    if (s.kind === 'section') return board.sections.find((x) => x.id === s.id)?.name ?? '';
    return 'Everything';
  });

  // Monitor mode: a clock, and a cursor that hides when the mouse rests.
  const clockFmt = workspaceFormatter('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
  let cursor = $state(true);
  let cursorTimer: ReturnType<typeof setTimeout> | undefined;
  function wake() {
    if (!kiosk) return;
    cursor = true;
    clearTimeout(cursorTimer);
    cursorTimer = setTimeout(() => (cursor = false), 3000);
  }

  function typing(t: EventTarget | null): boolean {
    const el = t as HTMLElement | null;
    return !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
  }

  /** j / k: the next or previous task on the page, in screen order; Enter opens it. */
  function step(dir: 1 | -1) {
    const rows = [...document.querySelectorAll<HTMLElement>('main [data-item]')].filter((el) => el.offsetParent);
    if (!rows.length) return;
    const at = rows.findIndex((el) => el.dataset.item === board.focusId);
    const next = rows[at < 0 ? (dir > 0 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, at + dir))];
    board.focusId = next.dataset.item ?? null;
    for (const el of rows) el.classList.toggle('kb-focus', el === next);
    next.scrollIntoView({ block: 'nearest' });
  }

  // `g` then a letter jumps: g h My Work, g i Inbox, g e Everything.
  let chord = 0;

  function onKey(e: KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k' && !kiosk) {
      e.preventDefault();
      palette = !palette;
      return;
    }
    if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key.toLowerCase() === 'z' && !typing(e.target) && board.ready && !kiosk) {
      e.preventDefault();
      void board.undoLast();
      return;
    }
    if (e.key === 'Escape') {
      if (task.viewing) task.closeViewer();
      else if (palette) palette = false;
      else if (sheet) sheet = false;
      else if (board.tagManagerOpen) board.closeTagManager();
      else if (board.openId) board.close();
      else if (board.selected.size) board.clearSelection();
      else navOpen = false;
      if (typing(e.target)) (e.target as HTMLElement).blur();
      return;
    }
    if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey || !board.ready || kiosk) return;
    const focus = (id: string) => {
      e.preventDefault();
      document.getElementById(id)?.focus();
    };
    if (chord && Date.now() - chord < 1200) {
      chord = 0;
      const to = { h: '#/home', i: '#/inbox', e: '#/everything', p: '#/docs', m: '#/chat' }[e.key];
      if (to) {
        e.preventDefault();
        location.hash = to;
        return;
      }
    }
    chord = 0;
    // With the task window open, only the keys about that task act; the board behind stays put.
    if (board.openId && !['e', 's', 't', 'g', '?'].includes(e.key)) return;
    switch (e.key) {
      case 'g':
        chord = Date.now();
        return;
      case 'j':
        e.preventDefault();
        return step(1);
      case 'k':
        e.preventDefault();
        return step(-1);
      case 'Enter':
        if (board.focusId && !board.openId && document.activeElement === document.body) {
          e.preventDefault();
          void board.open(board.focusId);
        }
        return;
      case 'e':
        // Done: complete the open task, or the one under the pointer or the j/k cursor.
        if (board.openId ?? board.focusId) void board.toggleComplete((board.openId ?? board.focusId)!);
        return;
      case '/':
        return focus('view-search');
      case 'c':
        return focus('quick-add');
      case 'b':
        board.setConfig({ layout: board.active.config.layout === 'board' ? 'list' : 'board' });
        return;
      case 'f':
        e.preventDefault();
        document.getElementById('filter-btn')?.click();
        return;
      case 'x':
        if (board.focusId) board.toggleSelect(board.focusId);
        return;
      case 's': {
        // Star: pin the open task, or the card under the pointer, to the favorites bar.
        const id = board.openId ?? board.focusId;
        if (id) void board.toggleFavorite('item', id);
        return;
      }
      case '?':
        sheet = !sheet;
        return;
    }
  }
</script>

<svelte:window onkeydown={onKey} onmousemove={wake} />

{#if invitation !== null || !board.token}
  <TokenScreen {invitation} />
{:else}
  <div class="shell" class:nav-open={navOpen} class:selecting={board.selected.size > 0} class:kiosk class:no-cursor={kiosk && !cursor}>
    {#if kiosk}
      <header class="kiosk-bar">
        <span class="brand">Slate</span>
        <span class="clock">{clockFmt.format(board.now)}</span>
        <span class="conn conn-{board.conn}" title={board.conn === 'live' ? 'Live' : 'Reconnecting'}></span>
      </header>
    {:else}
      <header class="topbar">
        <button class="icon menu" aria-label="Menu" onclick={() => (navOpen = !navOpen)}><Icon name="menu" size={18} /></button>
        <span class="brand">Slate</span>
        <span class="where">{page === 'home' ? 'My Work' : page === 'inbox' ? 'Inbox' : page === 'agents' ? 'Agents' : page === 'docs' ? 'Docs' : page === 'archive' ? 'Archive' : page === 'chat' ? 'Chat' : page === 'members' ? 'Members' : page === 'audit' ? 'Audit log' : page === 'health' ? 'Health' : where}</span>
        <button class="icon top-search" aria-label="Search" onclick={() => (palette = true)}><Icon name="search" size={18} /></button>
        <NeedsBadge mode="top" />
        <AgentChatButton cls="top-agent" />
        <a class="icon top-inbox" href="#/inbox" aria-label="Inbox{inbox.unread ? `, ${inbox.unread} unread` : ''}"
          ><Icon name="bell" size={18} />{#if inbox.unread}<span class="badge">{inbox.unread > 99 ? '99+' : inbox.unread}</span>{/if}</a
        >
      </header>
      <FavoritesBar />
      <Nav onpick={() => (navOpen = false)} onsearch={() => (palette = true)} {page} {docId} />
      <button class="scrim" aria-label="Close menu" tabindex="-1" onclick={() => (navOpen = false)}
      ></button>
    {/if}
    {#if !kiosk}<PullToRefresh />{/if}
    <!-- My Work and the Inbox show the full Needs you panel, so the pill stays off them. -->
    {#if board.ready && !kiosk && page !== 'home' && page !== 'inbox'}<NeedsBadge mode="float" />{/if}
    <main class="main">
      {#if !board.online}
        <div class="banner offline" role="status">
          <Icon name="wifiOff" />You're offline. This is what was loaded; changes can't be saved until you're back.
        </div>
      {:else if board.conn === 'reconnecting'}
        <div class="banner" role="status">
          <Icon name="wifiOff" />Reconnecting… other people's changes will catch up when the line is back.
        </div>
      {/if}
      {#if !board.ready}
        <Skeleton />
      {:else if page === 'agents'}
        <AgentsPage />
      {:else if page === 'docs' && !kiosk}
        <DocsPage id={docId} />
      {:else if page === 'archive' && !kiosk}
        <ArchivePage />
      {:else if page === 'members' && !kiosk}
        {#if board.canManage}
          <MembersPage />
        {:else}
          <EmptyState icon="lock" title="Owners and admins only" hint="Ask an owner or admin to change roles or sharing." />
        {/if}
      {:else if page === 'audit' && !kiosk}
        <AuditPage />
      {:else if page === 'health' && !kiosk}
        <HealthPage />
      {:else if page === 'chat' && !kiosk}
        <ChatPage channelKey={chatChannel} threadId={chatThread} />
      {:else if missing}
        <EmptyState
          icon="archive"
          title="That {board.selection.kind} is gone"
          hint="It was archived or deleted. Pick another on the left, or restore it from the Archive page."
        >
          <a class="primary" href="#/everything">Go to Everything</a>
        </EmptyState>
      {:else if page === 'home' && !kiosk}
        <HomePage />
      {:else if page === 'inbox' && !kiosk}
        <InboxPage />
      {:else}
        <ScopePage scope={board.selection} {kiosk} chat={scopeChat && !kiosk} threadId={chatThread} />
      {/if}
    </main>
    {#if board.ready && !kiosk && chatPanel.open && page !== 'chat' && !scopeChat}
      <ChatPanel />
    {/if}
    {#if board.openId}
      <TaskModal />
    {/if}
    <!-- Above the task window; also opened from a chat message's files, with no task open. -->
    <MediaViewer />
    {#if palette}
      <CommandPalette
        onclose={() => (palette = false)}
        onshortcuts={() => (sheet = true)}
      />
    {/if}
    {#if sheet}
      <ShortcutSheet onclose={() => (sheet = false)} />
    {/if}
    {#if board.tagManagerOpen}
      <TagManager />
    {/if}
    {#if board.shareTarget}
      <ShareDialog kind={board.shareTarget.kind} id={board.shareTarget.id} />
    {/if}
    {#if notifySettings.open}
      <NotificationSettings />
    {/if}
    {#if board.toast}
      <div class="toast" class:soft={!!board.toastAction} role="alert">
        <span class="toast-icon"><Icon name={board.toastAction ? 'info' : 'alert'} size={16} /></span>
        <span>{board.toast}</span>
        {#if board.toastAction}
          {@const act = board.toastAction}
          <button
            class="toast-action"
            onclick={() => {
              act.run();
              board.toast = null;
              board.toastAction = null;
            }}>{act.label}</button
          >
        {/if}
      </div>
    {/if}
  </div>
{/if}
