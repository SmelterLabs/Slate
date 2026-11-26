<script lang="ts">
  import { chat } from '../../lib/chat.svelte';
  import { priorityLabel } from '../../lib/priority';
  import { chatHref } from '../../lib/chat';
  import { inbox } from '../../lib/inbox.svelte';
  import { shortDate } from '../../lib/format';
  import { type Candidate, type Hit, pushRecent, rank, segments } from '../../lib/palette';
  import { parseQuickAdd } from '../../lib/quickadd';
  import { docs } from '../../lib/docs.svelte';
  import type { DocSummary } from '../../lib/docs';
  import {
    SEARCH_DEBOUNCE_MS,
    groupHits,
    hitHref,
    searchAll,
    shouldSearch,
    type SearchFilters,
    type SearchHit,
    type SearchKind,
  } from '../../lib/search';
  import { board } from '../../lib/store.svelte';
  import { trapFocus } from '../../lib/focustrap';
  import Avatars from '../Avatars.svelte';
  import Icon from '../Icon.svelte';

  // Universal Search & Command Palette (Ctrl/Cmd+K): search tasks (title, description, comments,
  // card IDs, closed & archived), docs & files, chat, lists, sections, views and commands.
  let { onclose, onshortcuts }: { onclose: () => void; onshortcuts: () => void } = $props();

  function readRecent(): string[] {
    return board.prefs.recent_tasks ?? [];
  }
  function saveRecent(ids: string[]) {
    void board.setPrefs({ recent_tasks: ids });
  }

  let query = $state('');
  let cursor = $state(0);
  let input = $state<HTMLInputElement | null>(null);
  let listEl = $state<HTMLDivElement | null>(null);
  const recent = readRecent();

  // Inline filter toggles inside Universal Search.
  let kindFilter = $state<SearchKind | null>(null);
  let assigneeFilter = $state<string>('');
  let placeFilter = $state<string>(''); // '' | 'list:<id>' | 'section:<id>'
  let includeClosed = $state(true);
  let includeArchived = $state(true);

  const meHandle = $derived(board.me?.account.handle ?? '');
  const agents = $derived(
    board.pickable.filter((a) => a.kind === 'agent').sort((a, b) => a.display_name.localeCompare(b.display_name)),
  );
  const currentListId = $derived(board.selection.kind === 'list' ? board.selection.id : null);
  const isCommandMode = $derived(query.trim().startsWith('>'));

  const activeFilters = $derived.by((): SearchFilters => {
    const f: SearchFilters = {
      include_closed: includeClosed,
      include_archived: includeArchived,
    };
    if (kindFilter) f.kind = kindFilter;
    if (assigneeFilter) f.assignee = assigneeFilter;
    if (placeFilter.startsWith('list:')) f.list_id = placeFilter.slice(5);
    else if (placeFilter.startsWith('section:')) f.section_id = placeFilter.slice(8);
    return f;
  });

  const hasCustomFilters = $derived(
    kindFilter !== null || assigneeFilter !== '' || placeFilter !== '' || !includeClosed || !includeArchived,
  );

  function resetFilters() {
    kindFilter = null;
    assigneeFilter = '';
    placeFilter = '';
    includeClosed = true;
    includeArchived = true;
  }

  function go(hash: string) {
    location.hash = hash;
  }
  const focusLater = (id: string) => setTimeout(() => document.getElementById(id)?.focus(), 30);
  const openId = board.openId;

  interface TaskMeta {
    statusName: string | null;
    statusKind: string | null;
    statusColor: string | null;
    archived: boolean;
    section: string | null;
    list: string | null;
    assignees: string[];
    at: string;
  }

  interface Command extends Candidate {
    run: () => void;
    taskMeta?: TaskMeta;
  }

  const commands = $derived.by((): Command[] => {
    const out: Command[] = [
      { kind: 'action', id: 'go-home', label: 'Go to My Work', hint: 'g h', keywords: 'home today mine', run: () => go('#/home') },
      { kind: 'action', id: 'go-inbox', label: 'Go to Inbox', hint: 'g i', keywords: 'notifications mentions', run: () => go('#/inbox') },
      { kind: 'action', id: 'go-chat', label: 'Go to Chat', hint: 'g m', keywords: 'messages channels talk general', run: () => go('#/chat') },
      { kind: 'action', id: 'go-docs', label: 'Go to Docs', hint: 'g p', keywords: 'pages notes wiki plans documents', run: () => go('#/docs') },
      { kind: 'action', id: 'new-doc', label: 'New doc page', keywords: 'page note document write plan meeting', run: () => go('#/docs?new=1') },
      { kind: 'action', id: 'go-archive', label: 'Go to Archive', keywords: 'done finished archived trash deleted restore export backup download csv json', run: () => go('#/archive') },
      { kind: 'action', id: 'go-trash', label: 'Open the trash', keywords: 'deleted restore bin', run: () => go('#/archive?tab=trash') },
      { kind: 'action', id: 'go-everything', label: 'Go to Everything', hint: 'g e', keywords: 'all board', run: () => go('#/everything') },
      { kind: 'action', id: 'new-task', label: 'New task', hint: 'c', keywords: 'add create quick', run: () => focusLater('quick-add') },
      {
        kind: 'action',
        id: 'search-view',
        label: 'Filter this view',
        hint: '/',
        keywords: 'find search filter',
        run: () => {
          setTimeout(() => {
            document.getElementById('filter-btn')?.click();
            setTimeout(() => document.getElementById('view-search')?.focus(), 20);
          }, 30);
        },
      },
      { kind: 'action', id: 'filter', label: 'Filter rules', hint: 'f', run: () => setTimeout(() => document.getElementById('filter-btn')?.click(), 30) },
      {
        kind: 'action',
        id: 'layout',
        label: board.active.config.layout === 'board' ? 'Show as list' : 'Show as board',
        hint: 'b',
        keywords: 'layout kanban toggle',
        run: () => board.setConfig({ layout: board.active.config.layout === 'board' ? 'list' : 'board' }),
      },
      { kind: 'action', id: 'go-agents', label: 'Go to Agents', keywords: 'bots ai connect token presence activity', run: () => go('#/agents') },
      { kind: 'action', id: 'go-members', label: 'Go to Members', keywords: 'people roles guests admins invite share permissions', run: () => go('#/members') },
      { kind: 'action', id: 'go-audit', label: 'Go to Audit log', keywords: 'history who changed log events', run: () => go('#/audit') },
      { kind: 'action', id: 'go-health', label: 'Go to Health', keywords: 'status errors slow broken pollers latency uptime disk monitor', run: () => go('#/health') },
      { kind: 'action', id: 'go-settings', label: 'Go to Settings', keywords: 'settings general ai model provider claude openai chatgpt gemini ollama inbox default list', run: () => go('#/settings') },
      { kind: 'action', id: 'undo', label: 'Undo last change', hint: 'Ctrl Z', keywords: 'revert back', run: () => void board.undoLast() },
      { kind: 'action', id: 'tags', label: 'Manage tags', keywords: 'rename merge colour color', run: () => board.openTagManager() },
      {
        kind: 'action',
        id: 'notifications',
        label: 'Notifications and reminders',
        keywords: 'push phone desktop digest remind alerts',
        run: () => (inbox.settingsOpen = true),
      },
      { kind: 'action', id: 'inbox-read', label: 'Mark all notifications read', keywords: 'inbox clear', run: () => void inbox.readAll() },
      { kind: 'action', id: 'shortcuts', label: 'Keyboard shortcuts', hint: '?', keywords: 'help keys', run: () => onshortcuts() },
      {
        kind: 'action',
        id: 'theme',
        label: board.theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode',
        keywords: 'theme appearance colors',
        run: () => board.setTheme(board.theme === 'dark' ? 'light' : 'dark'),
      },
      { kind: 'action', id: 'monitor', label: 'Open monitor mode', keywords: 'kiosk tv wall', run: () => window.open(`${location.pathname}?kiosk=1${location.hash}`, '_blank') },
    ];
    const hidden = new Set(board.canManage ? [] : ['go-members', 'go-health', 'go-settings']);
    for (let i = out.length - 1; i >= 0; i--) if (hidden.has(out[i].id)) out.splice(i, 1);
    if (openId && board.items[openId]) {
      const it = board.items[openId];
      out.unshift(
        { kind: 'action', id: 'task-done', label: `Complete “${it.title}”`, keywords: 'done finish close', run: () => void board.toggleComplete(openId) },
        { kind: 'action', id: 'task-archive', label: `Put away “${it.title}”`, keywords: 'archive hide remove', run: () => void board.archive(openId) },
        { kind: 'action', id: 'task-trash', label: `Move “${it.title}” to the trash`, keywords: 'delete remove bin', run: () => void board.trash(openId) },
        {
          kind: 'action',
          id: 'task-link',
          label: 'Copy link to this task',
          keywords: 'share url',
          run: () => void navigator.clipboard?.writeText(`${location.origin}${location.pathname}#/list/${it.list_id}?task=${it.id}`).then(() => board.flash('Link copied')),
        },
      );
    }
    return out;
  });

  function targetList(): string | null {
    const s = board.selection;
    if (s.kind === 'list') return s.id;
    return board.quickAddList(board.active.key, s);
  }

  const places = $derived.by((): Command[] => {
    const out: Command[] = [];
    for (const sec of board.sections) {
      out.push({ kind: 'section', id: `s-${sec.id}`, label: sec.name, hint: 'Section', run: () => go(`#/section/${sec.id}`) });
      for (const l of board.listsOf(sec.id)) {
        out.push({ kind: 'list', id: `l-${l.id}`, label: l.name, hint: sec.name, run: () => go(`#/list/${l.id}`) });
      }
    }
    for (const v of Object.values(board.views)) {
      const where =
        v.scope_kind === 'everything'
          ? 'Everything'
          : v.scope_kind === 'list'
            ? (board.lists[v.scope_id ?? '']?.name ?? '')
            : (board.sections.find((x) => x.id === v.scope_id)?.name ?? '');
      out.push({
        kind: 'view',
        id: `v-${v.id}`,
        label: v.name,
        hint: `View · ${where}`,
        run: () => go(board.href(v.scope_kind === 'everything' ? { kind: 'everything' } : { kind: v.scope_kind, id: v.scope_id ?? '' }, v.id)),
      });
    }
    for (const c of chat.channels) {
      out.push({
        kind: 'channel',
        id: `c-${c.key}`,
        label: `# ${c.name}`,
        hint: c.kind === 'general' ? 'Chat' : `Chat · ${c.kind === 'list' ? 'List' : 'Section'}`,
        keywords: `${c.name} chat channel`,
        run: () => go(chatHref(c.key)),
      });
    }
    for (const t of board.tags) {
      out.push({
        kind: 'tag',
        id: `t-${t.name}`,
        label: `#${t.name}`,
        hint: 'Tag · search Everything',
        run: () => {
          go('#/everything');
          setTimeout(() => (board.q = t.name), 30);
        },
      });
    }
    return out;
  });

  const tasks = $derived.by((): Command[] => {
    const rec = new Map(recent.map((id, i) => [id, i]));
    const secNames = new Map(board.sections.map((s) => [s.id, s.name]));
    const out: Command[] = [];
    for (const it of board.plainItems) {
      const archived = !!it.archived_at;
      if (archived && !includeArchived) continue;
      const closed = it.status?.kind === 'done' || it.status?.kind === 'cancelled';
      if (closed && !includeClosed) continue;
      const lst = board.lists[it.list_id];
      if (activeFilters.list_id && it.list_id !== activeFilters.list_id) continue;
      if (activeFilters.section_id && lst?.section_id !== activeFilters.section_id) continue;
      if (activeFilters.assignee && !it.assignees.some((a) => a.handle === activeFilters.assignee || a.id === activeFilters.assignee)) {
        continue;
      }
      const r = rec.get(it.id);
      const section = lst ? (secNames.get(lst.section_id) ?? null) : null;
      out.push({
        kind: 'task',
        id: it.id,
        label: it.title,
        hint: section && lst ? `${section} · ${lst.name}` : (lst?.name ?? ''),
        extra: `${it.id} #${it.id} ${it.tags.map((t) => `#${t}`).join(' ')} ${it.assignees.map((a) => `@${a.handle}`).join(' ')}`,
        boost: (r !== undefined ? 30 - r : 0) + (archived ? -16 : closed ? -8 : 0),
        taskMeta: {
          statusName: it.status?.name ?? null,
          statusKind: it.status?.kind ?? null,
          statusColor: it.status?.color ?? null,
          archived,
          section,
          list: lst?.name ?? null,
          assignees: it.assignees.map((a) => a.id),
          at: it.updated_at,
        },
        run: () => {
          saveRecent(pushRecent(readRecent(), it.id));
          void board.open(it.id);
        },
      });
    }
    return out;
  });

  const docHint = (d: DocSummary) => {
    const top = (() => {
      let cur: DocSummary | undefined = d;
      for (let n = 0; cur?.parent_id && n < 10; n++) cur = docs.byId[cur.parent_id];
      return cur ?? d;
    })();
    const where = top.list_id ? board.lists[top.list_id]?.name : top.section_id ? board.sections.find((x) => x.id === top.section_id)?.name : 'Workspace';
    return `Doc · ${where ?? ''}`;
  };
  const pages = $derived.by((): Command[] => {
    if (activeFilters.assignee) return [];
    return docs.list
      .filter((d) => (!activeFilters.list_id || d.list_id === activeFilters.list_id) && (!activeFilters.section_id || d.section_id === activeFilters.section_id))
      .map((d) => ({
        kind: 'doc',
        id: d.id,
        label: d.icon ? `${d.icon} ${d.title}` : d.title,
        hint: docHint(d),
        extra: `${d.id} #${d.id}`,
        run: () => go(`#/docs/${d.id}`),
      }));
  });

  // One search: once two or more plain characters (or a #ID) are typed, the server searches
  // task IDs, titles, descriptions, comments, closed/archived cards, docs & files, and chat.
  let remote = $state<{ key: string; hits: SearchHit[] }>({ key: '', hits: [] });
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  const remoteKey = $derived(`${query.trim()}|${JSON.stringify(activeFilters)}`);
  $effect(() => {
    const q = query.trim();
    const filters = activeFilters;
    const key = remoteKey;
    clearTimeout(searchTimer);
    if (!shouldSearch(q)) {
      remote = { key: '', hits: [] };
      return;
    }
    searchTimer = setTimeout(() => {
      void searchAll(q, filters, 30).then(
        (hits) => (remoteKey === key ? (remote = { key, hits }) : null),
        () => {},
      );
    }, SEARCH_DEBOUNCE_MS);
  });

  const byId = $derived(new Map([...commands, ...places, ...pages, ...tasks].map((c) => [`${c.kind}:${c.id}`, c])));

  const hits = $derived.by((): Hit[] => {
    const q = query.trim();
    if (!q) {
      if (kindFilter === 'task') {
        return tasks.slice(0, 12).map((c) => ({ ...c, score: 0, marks: [] }));
      }
      if (kindFilter === 'doc') {
        return pages.slice(0, 12).map((c) => ({ ...c, score: 0, marks: [] }));
      }
      if (kindFilter === 'message') {
        return places.filter((p) => p.kind === 'channel').slice(0, 12).map((c) => ({ ...c, score: 0, marks: [] }));
      }
      const rec = recent
        .map((id) => tasks.find((t) => t.id === id))
        .filter((t): t is Command => !!t)
        .slice(0, 6);
      return [...rec, ...commands.slice(0, 8)].map((c) => ({ ...c, score: 0, marks: [] }));
    }
    if (q.startsWith('>')) return rank(q, commands, 40);
    // A #-prefixed 12-char ID skips the tag-only mode so it matches tasks & docs by ID.
    const effectiveQ = /^#[0-9A-Za-z]{12}$/.test(q) ? q.slice(1) : q;
    let pool: Candidate[];
    if (kindFilter === 'task') pool = tasks;
    else if (kindFilter === 'doc') pool = pages;
    else if (kindFilter === 'message') pool = places.filter((p) => p.kind === 'channel');
    else if (hasCustomFilters) pool = [...tasks, ...pages];
    else pool = [...commands, ...places, ...pages, ...tasks];
    return rank(effectiveQ, pool, 40);
  });

  const remoteGroups = $derived.by(() => {
    if (remote.key !== remoteKey || !remote.hits.length) return [];
    return groupHits(remote.hits, {
      task: hits.filter((h) => h.kind === 'task').map((h) => h.id),
      doc: hits.filter((h) => h.kind === 'doc').map((h) => h.id),
    });
  });
  const remoteRows = $derived(remoteGroups.flatMap((g) => g.hits));

  function openHit(h: SearchHit) {
    if (h.kind === 'task') {
      saveRecent(pushRecent(readRecent(), h.id));
      void board.open(h.id);
      return;
    }
    const href = hitHref(h);
    if (href) go(href);
  }

  function accountsOf(ids: string[] | undefined) {
    return (ids ?? []).map((id) => board.accounts[id]).filter((a): a is NonNullable<typeof a> => !!a);
  }

  const aiOn = $derived(board.aiAddOn && board.aiReady);
  const inboxName = $derived(board.lists[board.workspaceInboxList() ?? '']?.name ?? 'Todo');

  const create = $derived.by(() => {
    const q = query.trim();
    if (!q || q.startsWith('>') || /^#?[0-9A-Za-z]{12}$/.test(q)) return null;
    if (aiOn) return { title: q, tags: [] as string[], assignees: [] as string[], priority: null, due: null, start: null, list: null, rrule: null };
    const parsed = parseQuickAdd(q);
    return parsed.title ? parsed : null;
  });
  const createAt = $derived(hits.length + remoteRows.length);
  const total = $derived(createAt + (create ? 1 : 0));

  $effect(() => {
    void remoteKey;
    cursor = 0;
  });
  $effect(() => {
    input?.focus();
  });
  $effect(() => {
    listEl?.querySelector(`[data-index="${cursor}"]`)?.scrollIntoView({ block: 'nearest' });
  });

  function run(i: number) {
    if (i === createAt && create) {
      const q = query.trim();
      onclose();
      if (aiOn) {
        void board.aiAdd(null, q, {}, { route: true });
        return;
      }
      const listId = targetList();
      if (!listId) return board.flash('Pick a list to add to first');
      void board.quickAdd(listId, create);
      return;
    }
    if (i >= hits.length && i < createAt) {
      const r = remoteRows[i - hits.length];
      onclose();
      openHit(r);
      return;
    }
    const h = hits[i];
    const c = h && byId.get(`${h.kind}:${h.id}`);
    if (!c) return;
    onclose();
    c.run();
  }

  function onkey(e: KeyboardEvent) {
    if (e.key === 'ArrowDown' || (e.key === 'n' && e.ctrlKey)) {
      e.preventDefault();
      cursor = (cursor + 1) % Math.max(1, total);
    } else if (e.key === 'ArrowUp' || (e.key === 'p' && e.ctrlKey)) {
      e.preventDefault();
      cursor = (cursor - 1 + total) % Math.max(1, total);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      run(cursor);
    }
  }

  const KIND: Record<string, string> = {
    action: 'Command',
    task: 'Task',
    list: 'List',
    section: 'Section',
    view: 'View',
    channel: 'Chat',
    tag: 'Tag',
    person: 'Person',
    doc: 'Doc',
    message: 'Chat',
  };
</script>

<button class="palette-scrim" aria-label="Close search" tabindex="-1" onclick={onclose}></button>
<div class="palette" role="dialog" aria-modal="true" aria-label="Search and commands" use:trapFocus={{ onescape: onclose }}>
  <div class="palette-input">
    <span class="palette-icon" aria-hidden="true"><Icon name="search" size={17} /></span>
    <input
      bind:this={input}
      bind:value={query}
      onkeydown={onkey}
      role="combobox"
      aria-expanded="true"
      aria-controls="palette-results"
      aria-activedescendant="palette-{cursor}"
      aria-label="Search tasks, docs, chat, lists and commands"
      placeholder="Search tasks, comments, docs, chat, or #ID…   > commands"
      autocomplete="off"
      spellcheck="false"
    />
    {#if query}
      <button type="button" class="icon small" aria-label="Clear query" onclick={() => { query = ''; input?.focus(); }}>
        <Icon name="x" size={14} />
      </button>
    {/if}
    <kbd>Esc</kbd>
  </div>

  {#if !isCommandMode}
    <div class="palette-filters" role="toolbar" aria-label="Search filters">
      <div class="pf-group" role="group" aria-label="Type">
        <button type="button" class="pf-chip" class:on={kindFilter === null} onclick={() => (kindFilter = null)}>All</button>
        <button type="button" class="pf-chip" class:on={kindFilter === 'task'} onclick={() => (kindFilter = kindFilter === 'task' ? null : 'task')}>Tasks</button>
        <button type="button" class="pf-chip" class:on={kindFilter === 'doc'} onclick={() => (kindFilter = kindFilter === 'doc' ? null : 'doc')}>Docs</button>
        <button type="button" class="pf-chip" class:on={kindFilter === 'message'} onclick={() => (kindFilter = kindFilter === 'message' ? null : 'message')}>Chat</button>
      </div>
      <span class="pf-sep" aria-hidden="true"></span>
      {#if meHandle}
        <button
          type="button"
          class="pf-chip"
          class:on={assigneeFilter === meHandle}
          title="Only tasks assigned to you"
          onclick={() => (assigneeFilter = assigneeFilter === meHandle ? '' : meHandle)}
        >Mine</button>
      {/if}
      {#if agents.length}
        <select
          class="pf-select"
          class:on={!!assigneeFilter && assigneeFilter !== meHandle}
          aria-label="Filter by agent"
          value={assigneeFilter && assigneeFilter !== meHandle ? assigneeFilter : ''}
          onchange={(e) => (assigneeFilter = e.currentTarget.value)}
        >
          <option value="">Agent: Any</option>
          {#each agents as a (a.id)}
            <option value={a.handle}>Agent: {a.display_name}</option>
          {/each}
        </select>
      {/if}
      <select
        class="pf-select"
        class:on={!!placeFilter}
        aria-label="Filter by board or list"
        bind:value={placeFilter}
      >
        <option value="">Board: All</option>
        {#if currentListId && board.lists[currentListId]}
          <option value="list:{currentListId}">This list ({board.lists[currentListId].name})</option>
        {/if}
        {#each board.sections as sec (sec.id)}
          <option value="section:{sec.id}">{sec.name} (all lists)</option>
          {#each board.listsOf(sec.id) as lst (lst.id)}
            <option value="list:{lst.id}">&nbsp;&nbsp;{sec.name} · {lst.name}</option>
          {/each}
        {/each}
      </select>
      <button
        type="button"
        class="pf-chip pf-state"
        class:on={includeClosed}
        title="Include closed (done/cancelled) tasks"
        onclick={() => (includeClosed = !includeClosed)}
      ><Icon name="check" size={11} /> Closed</button>
      <button
        type="button"
        class="pf-chip pf-state"
        class:on={includeArchived}
        title="Include archived tasks"
        onclick={() => (includeArchived = !includeArchived)}
      ><Icon name="archive" size={11} /> Archived</button>
      {#if hasCustomFilters}
        <button type="button" class="link small pf-reset" onclick={resetFilters}>Reset</button>
      {/if}
    </div>
  {/if}

  <div class="palette-results" id="palette-results" role="listbox" bind:this={listEl}>
    {#if !query.trim() && hits.length}
      <div class="palette-section">{kindFilter ? `${KIND[kindFilter]}s` : recent.length ? 'Recent and commands' : 'Commands'}</div>
    {/if}
    {#each hits as h, i (`${h.kind}:${h.id}`)}
      {@const cmd = byId.get(`${h.kind}:${h.id}`)}
      {@const meta = cmd?.taskMeta}
      {@const isClosed = meta?.statusKind === 'done' || meta?.statusKind === 'cancelled'}
      <button
        class="palette-row"
        class:active={i === cursor}
        class:dimmed={meta?.archived || isClosed}
        id="palette-{i}"
        role="option"
        aria-selected={i === cursor}
        data-index={i}
        onmouseenter={() => (cursor = i)}
        onclick={() => run(i)}
      >
        <span class="palette-kind kind-{h.kind}">{KIND[h.kind]}</span>
        <span class="palette-main">
          <span class="palette-top">
            <span class="palette-label">
              {#each segments(h.label, h.marks) as seg, si (si)}{#if seg.mark}<mark>{seg.text}</mark>{:else}{seg.text}{/if}{/each}
            </span>
            {#if meta?.archived}
              <span class="hit-pill hit-archived" title="Archived task"><Icon name="archive" size={11} /> Archived</span>
            {:else if isClosed}
              <span class="hit-pill hit-closed" title="Closed task"><Icon name="check" size={11} /> {meta?.statusName ?? 'Done'}</span>
            {:else if meta?.statusName}
              <span class="hit-pill hit-status">
                <span class="hit-dot" style="background: {meta.statusColor ?? 'var(--muted)'}"></span>{meta.statusName}
              </span>
            {/if}
          </span>
          {#if meta}
            <span class="palette-meta">
              {#if h.hint}<span class="hit-where">{h.hint}</span>{/if}
              {#if meta.assignees.length}
                <span class="hit-assignees"><Avatars people={accountsOf(meta.assignees)} /></span>
              {/if}
              <span class="hit-date">{shortDate(meta.at)}</span>
            </span>
          {/if}
        </span>
        {#if !meta && h.hint}<span class="palette-hint">{h.hint}</span>{/if}
      </button>
    {/each}
    {#each remoteGroups as g (g.kind)}
      {@const base = hits.length + remoteRows.indexOf(g.hits[0])}
      <div class="palette-section" data-search-group={g.kind}>{g.label}</div>
      {#each g.hits as h, gi (`${h.kind}:${h.id}`)}
        {@const i = base + gi}
        {@const isClosed = h.status_kind === 'done' || h.status_kind === 'cancelled'}
        {@const whereLabel = h.kind === 'task' && h.section && h.where ? `${h.section} · ${h.where}` : (h.where ?? '')}
        <button
          class="palette-row remote"
          class:active={i === cursor}
          class:dimmed={h.archived || isClosed}
          id="palette-{i}"
          role="option"
          aria-selected={i === cursor}
          data-index={i}
          onmouseenter={() => (cursor = i)}
          onclick={() => run(i)}
        >
          <span class="palette-kind kind-{h.kind}">{KIND[h.kind]}</span>
          <span class="palette-main">
            <span class="palette-top">
              <span class="palette-label">{h.kind === 'message' ? (h.snippet ?? h.title) : h.title}</span>
              {#if h.matched_in === 'id'}
                <span class="hit-pill hit-id" title="Exact ID match">#{h.id}</span>
              {/if}
              {#if h.archived}
                <span class="hit-pill hit-archived" title="Archived task"><Icon name="archive" size={11} /> Archived</span>
              {:else if isClosed}
                <span class="hit-pill hit-closed" title="Closed task"><Icon name="check" size={11} /> {h.status_name ?? 'Done'}</span>
              {:else if h.kind === 'task' && h.status_name}
                <span class="hit-pill hit-status">
                  <span class="hit-dot" style="background: {h.status_color ?? 'var(--muted)'}"></span>{h.status_name}
                </span>
              {/if}
            </span>
            <span class="palette-meta">
              {#if whereLabel}<span class="hit-where">{whereLabel}</span>{/if}
              {#if h.assignees?.length}
                <span class="hit-assignees"><Avatars people={accountsOf(h.assignees)} /></span>
              {/if}
              {#if h.kind !== 'message' && h.snippet && h.snippet !== h.title}
                <span class="hit-snippet">
                  {#if h.matched_in === 'comment'}<span class="hit-match-tag">Comment</span>{/if}{h.snippet}
                </span>
              {/if}
              <span class="hit-date">{shortDate(h.at)}</span>
            </span>
          </span>
        </button>
      {/each}
    {/each}
    {#if create}
      <button
        class="palette-row create"
        class:active={cursor === createAt}
        id="palette-{createAt}"
        role="option"
        aria-selected={cursor === createAt}
        data-index={createAt}
        onmouseenter={() => (cursor = createAt)}
        onclick={() => run(createAt)}
      >
        <span class="palette-kind kind-create">{aiOn ? '✨ AI Create' : '+ Create'}</span>
        <span class="palette-label">{create.title}</span>
        <span class="palette-hint">
          {#if aiOn}
            Auto · {inboxName}
          {:else}
            {[create.due && `due ${create.due.replace('T', ' ')}`, create.priority && priorityLabel(create.priority), ...create.tags.map((t) => `#${t}`)]
              .filter(Boolean)
              .join(' · ') || (board.lists[targetList() ?? '']?.name ?? '')}
          {/if}
        </span>
      </button>
    {/if}
    {#if !total}
      <p class="muted small pad-row">No matches.</p>
    {/if}
  </div>
  <div class="palette-foot muted small">
    <span><kbd>↑</kbd><kbd>↓</kbd> move</span>
    <span><kbd>Enter</kbd> open</span>
    <span><kbd>&gt;</kbd> commands</span>
    <span><kbd>#</kbd> tag or card ID</span>
  </div>
</div>
