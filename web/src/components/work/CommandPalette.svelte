<script lang="ts">
  import { chat } from '../../lib/chat.svelte';
  import { priorityLabel } from '../../lib/priority';
  import { chatHref } from '../../lib/chat';
  import { notifySettings } from '../../lib/notify.svelte';
  import { inbox } from '../../lib/inbox.svelte';
  import { type Candidate, type Hit, pushRecent, rank, segments } from '../../lib/palette';
  import { parseQuickAdd } from '../../lib/quickadd';
  import { docs } from '../../lib/docs.svelte';
  import type { DocSummary } from '../../lib/docs';
  import { SEARCH_DEBOUNCE_MS, groupHits, hitHref, searchAll, shouldSearch, type SearchHit } from '../../lib/search';
  import { board } from '../../lib/store.svelte';

  // Ctrl/Cmd+K: find any task, list, section, view, chat channel or tag and jump
  // there, or run a command.
  // ">" narrows to commands, "#" to tags. With nothing typed it shows recent tasks first.
  let { onclose, onshortcuts }: { onclose: () => void; onshortcuts: () => void } = $props();

  // Recents follow the account (prefs), so every device offers the same ones.
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

  function go(hash: string) {
    location.hash = hash;
  }
  const focusLater = (id: string) => setTimeout(() => document.getElementById(id)?.focus(), 30);
  const openId = board.openId;

  interface Command extends Candidate {
    run: () => void;
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
      { kind: 'action', id: 'search-view', label: 'Search this view', hint: '/', keywords: 'find filter', run: () => focusLater('view-search') },
      { kind: 'action', id: 'filter', label: 'Filter this view', hint: 'f', run: () => setTimeout(() => document.getElementById('filter-btn')?.click(), 30) },
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
      { kind: 'action', id: 'undo', label: 'Undo last change', hint: 'Ctrl Z', keywords: 'revert back', run: () => void board.undoLast() },
      { kind: 'action', id: 'tags', label: 'Manage tags', keywords: 'rename merge colour color', run: () => board.openTagManager() },
      {
        kind: 'action',
        id: 'notifications',
        label: 'Notifications and reminders',
        keywords: 'push phone desktop digest remind alerts',
        run: () => (notifySettings.open = true),
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
    // The same pages as the side nav: members and health need an owner or admin.
    const hidden = new Set(board.canManage ? [] : ['go-members', 'go-health']);
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

  /** Where "create" files: the list on screen, else the view's quick-add list. */
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
    const out: Command[] = [];
    for (const it of board.plainItems) {
      if (it.archived_at) continue;
      const r = rec.get(it.id);
      const closed = it.status?.kind === 'done' || it.status?.kind === 'cancelled';
      out.push({
        kind: 'task',
        id: it.id,
        label: it.title,
        hint: board.lists[it.list_id]?.name,
        extra: `${it.tags.map((t) => `#${t}`).join(' ')} ${it.assignees.map((a) => `@${a.handle}`).join(' ')}`,
        boost: (r !== undefined ? 30 - r : 0) + (closed ? -8 : 0),
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
  const pages = $derived.by((): Command[] =>
    docs.list.map((d) => ({
      kind: 'doc',
      id: d.id,
      label: d.icon ? `${d.icon} ${d.title}` : d.title,
      hint: docHint(d),
      run: () => go(`#/docs/${d.id}`),
    })),
  );
  // One search: once two or more plain characters are typed, the server looks through task
  // descriptions, page and file text and chat (debounced; a stale answer is dropped). Its hits
  // show grouped Tasks / Docs / Chat under the local matches, without what those already list.
  let remote = $state<{ q: string; hits: SearchHit[] }>({ q: '', hits: [] });
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const q = query.trim();
    clearTimeout(searchTimer);
    if (!shouldSearch(q)) {
      remote = { q: '', hits: [] };
      return;
    }
    searchTimer = setTimeout(() => {
      void searchAll(q).then(
        (hits) => (query.trim() === q ? (remote = { q, hits }) : null),
        () => {},
      );
    }, SEARCH_DEBOUNCE_MS);
  });

  const byId = $derived(new Map([...commands, ...places, ...pages, ...tasks].map((c) => [`${c.kind}:${c.id}`, c])));

  const hits = $derived.by((): Hit[] => {
    const q = query.trim();
    if (!q) {
      const rec = recent
        .map((id) => tasks.find((t) => t.id === id))
        .filter((t): t is Command => !!t)
        .slice(0, 6);
      return [...rec, ...commands.slice(0, 8)].map((c) => ({ ...c, score: 0, marks: [] }));
    }
    const pool: Candidate[] = q.startsWith('>') ? commands : [...commands, ...places, ...pages, ...tasks];
    return rank(q, pool, 40);
  });

  // The server's groups, as rows that follow the local hits (same keyboard cursor).
  const remoteGroups = $derived.by(() => {
    if (remote.q !== query.trim() || !remote.hits.length) return [];
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

  // "Create task" from what was typed: always offered last, parsed like quick add.
  const create = $derived.by(() => {
    const q = query.trim();
    if (!q || q.startsWith('>')) return null;
    const parsed = parseQuickAdd(q);
    return parsed.title ? parsed : null;
  });
  const createAt = $derived(hits.length + remoteRows.length);
  const total = $derived(createAt + (create ? 1 : 0));

  $effect(() => {
    void query;
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
      const listId = targetList();
      onclose();
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
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      onclose();
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
<div class="palette" role="dialog" aria-modal="true" aria-label="Search and commands">
  <div class="palette-input">
    <span class="palette-icon" aria-hidden="true">⌕</span>
    <input
      bind:this={input}
      bind:value={query}
      onkeydown={onkey}
      role="combobox"
      aria-expanded="true"
      aria-controls="palette-results"
      aria-activedescendant="palette-{cursor}"
      aria-label="Search tasks, docs, chat, lists and commands"
      placeholder="Search tasks, docs, chat, lists, views…   > commands   # tags"
      autocomplete="off"
      spellcheck="false"
    />
    <kbd>Esc</kbd>
  </div>
  <div class="palette-results" id="palette-results" role="listbox" bind:this={listEl}>
    {#if !query.trim() && hits.length}
      <div class="palette-section">{recent.length ? 'Recent and commands' : 'Commands'}</div>
    {/if}
    {#each hits as h, i (`${h.kind}:${h.id}`)}
      <button
        class="palette-row"
        class:active={i === cursor}
        id="palette-{i}"
        role="option"
        aria-selected={i === cursor}
        data-index={i}
        onmouseenter={() => (cursor = i)}
        onclick={() => run(i)}
      >
        <span class="palette-kind kind-{h.kind}">{KIND[h.kind]}</span>
        <span class="palette-label">
          {#each segments(h.label, h.marks) as seg, si (si)}{#if seg.mark}<mark>{seg.text}</mark>{:else}{seg.text}{/if}{/each}
        </span>
        {#if h.hint}<span class="palette-hint">{h.hint}</span>{/if}
      </button>
    {/each}
    {#each remoteGroups as g (g.kind)}
      {@const base = hits.length + remoteRows.indexOf(g.hits[0])}
      <div class="palette-section" data-search-group={g.kind}>{g.label}</div>
      {#each g.hits as h, gi (`${h.kind}:${h.id}`)}
        {@const i = base + gi}
        <button
          class="palette-row remote"
          class:active={i === cursor}
          id="palette-{i}"
          role="option"
          aria-selected={i === cursor}
          data-index={i}
          onmouseenter={() => (cursor = i)}
          onclick={() => run(i)}
        >
          <span class="palette-kind kind-{h.kind}">{KIND[h.kind]}</span>
          <span class="palette-label">{h.kind === 'message' ? (h.snippet ?? h.title) : h.title}</span>
          <span class="palette-hint">{h.kind === 'message' ? h.where : (h.snippet ?? h.where ?? '')}</span>
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
        <span class="palette-kind kind-create">+ Create</span>
        <span class="palette-label">{create.title}</span>
        <span class="palette-hint">
          {[create.due && `due ${create.due.replace('T', ' ')}`, create.priority && priorityLabel(create.priority), ...create.tags.map((t) => `#${t}`)]
            .filter(Boolean)
            .join(' · ') || (board.lists[targetList() ?? '']?.name ?? '')}
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
    <span><kbd>#</kbd> tags</span>
  </div>
</div>
