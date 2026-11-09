<script lang="ts">
  // The Archive page (`#/archive`): finished tasks, archived tasks, archived lists and sections,
  // and the trash (restore, delete for good, empty). The header exports everything as JSON or
  // CSV, and admins can download a backup of the whole database. Item tabs ask the server
  // (closed / archived / trashed filters, newest first) and reload when an item event arrives.
  import { onDestroy } from 'svelte';
  import { get, post } from '../lib/api';
  import { download, exportPath, purgeLabel } from '../lib/archive';
  import { ago } from '../lib/format';
  import { board } from '../lib/store.svelte';
  import type { Item } from '../lib/types';
  import EmptyState from './EmptyState.svelte';
  import Icon from './Icon.svelte';
  import { LEAVES } from '../lib/words';

  type Tab = 'done' | 'archived' | 'lists' | 'trash';
  const TABS: { key: Tab; label: string }[] = [
    { key: 'done', label: 'Done' },
    { key: 'archived', label: 'Put away' },
    { key: 'lists', label: 'Lists and sections' },
    { key: 'trash', label: 'Trash' },
  ];
  const QUERY: Record<Exclude<Tab, 'lists'>, string> = {
    done: '/items?closed=true&archived=false&order=completed',
    archived: '/items?archived=true&order=archived',
    trash: '/items?trashed=true&order=trashed',
  };

  function tabOfHash(): Tab {
    const t = new URLSearchParams(location.hash.split('?')[1] ?? '').get('tab') as Tab | null;
    return t && TABS.some((x) => x.key === t) ? t : 'done';
  }
  let tab = $state<Tab>(tabOfHash());
  let rows = $state<Item[] | null>(null);
  let q = $state('');
  let confirm = $state<string | null>(null); // item id, or 'empty'
  let busy = $state(false);
  const admin = $derived(!!board.me?.scopes.includes('admin'));

  async function load(which: Tab) {
    if (which === 'lists') {
      await board.loadArchived();
      return;
    }
    try {
      const got = await get<Item[]>(QUERY[which]);
      if (which === tab) rows = got;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  $effect(() => {
    rows = null;
    confirm = null;
    void load(tab);
  });

  let timer: ReturnType<typeof setTimeout> | undefined;
  const off = board.onEvent((ev) => {
    const hit =
      tab === 'lists' ? ev.type.startsWith('list.') || ev.type.startsWith('section.') : ev.type.startsWith('item.');
    if (!hit) return;
    clearTimeout(timer);
    timer = setTimeout(() => void load(tab), 250);
  });
  // The palette's "Open the trash" (or any link) can change the tab while the page is open.
  const onHash = () => {
    if (/^#\/archive\/?(\?|$)/.test(location.hash) && tabOfHash() !== tab) tab = tabOfHash();
  };
  window.addEventListener('hashchange', onHash);
  onDestroy(() => {
    off();
    clearTimeout(timer);
    window.removeEventListener('hashchange', onHash);
  });

  const shown = $derived.by(() => {
    const needle = q.trim().toLowerCase();
    const all = rows ?? [];
    return needle ? all.filter((i) => i.title.toLowerCase().includes(needle)) : all;
  });
  const listName = (i: Item) => board.lists[i.list_id]?.name ?? '';
  const parentTitle = (i: Item) => (i.parent_id ? (rows?.find((r) => r.id === i.parent_id)?.title ?? null) : null);

  function when(i: Item): string {
    if (tab === 'done' && i.completed_at) return `Completed ${ago(i.completed_at, board.now)}`;
    if (tab === 'archived' && i.archived_at) return `Put away ${ago(i.archived_at, board.now)}`;
    if (tab === 'trash' && i.trashed_at) return `Trashed ${ago(i.trashed_at, board.now)}`;
    return '';
  }

  function drop(id: string) {
    if (rows) rows = rows.filter((r) => r.id !== id && r.parent_id !== id);
  }

  async function act(id: string, run: () => Promise<unknown>) {
    busy = true;
    try {
      await run();
      drop(id);
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
      confirm = null;
    }
  }

  const restore = (i: Item) =>
    act(i.id, async () => {
      if (!(await board.restore(i.id))) throw new Error('Not restored');
      board.flash(`Restored “${i.title}”`);
    });
  const unarchive = (i: Item) => act(i.id, () => board.unarchive(i.id));
  const archive = (i: Item) => act(i.id, () => post(`/items/${i.id}/archive`));
  const toTrash = (i: Item) => act(i.id, () => board.trash(i.id));
  const purge = (i: Item) =>
    act(i.id, async () => {
      await post(`/items/${i.id}/purge`);
      board.flash(`Deleted “${i.title}” for good`);
    });

  async function emptyTrash() {
    busy = true;
    try {
      const { purged } = await post<{ purged: number }>('/trash/empty');
      rows = [];
      board.flash(`Emptied the trash (${purged} task${purged === 1 ? '' : 's'})`);
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
      confirm = null;
    }
  }

  async function save(path: string, fallback: string) {
    try {
      const name = await download(path, board.token, fallback);
      board.flash(`Downloaded ${name}`);
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  function pick(t: Tab) {
    tab = t;
    history.replaceState(null, '', t === 'done' ? '#/archive' : `#/archive?tab=${t}`);
  }
</script>

<section class="work-page archive-page" aria-label="Archive">
  <header class="work-head">
    <div>
      <h1>Archive</h1>
      <p class="muted">Where work goes when it leaves the board: done, put away, archived lists, and the trash. Plus your data to take with you.</p>
    </div>
    <div class="inline">
      <button class="secondary" onclick={() => save(exportPath('json'), 'slate-everything.json')}
        ><Icon name="download" size={14} /> Export JSON</button
      >
      <button class="secondary" onclick={() => save(exportPath('csv'), 'slate-everything.csv')}
        ><Icon name="download" size={14} /> Export CSV</button
      >
      {#if admin}
        <button class="secondary" title="A copy of the whole database (admins)" onclick={() => save('/admin/backup', 'slate-backup.db')}
          ><Icon name="download" size={14} /> Backup</button
        >
      {/if}
    </div>
  </header>

  <div class="inbox-tabs" role="tablist">
    {#each TABS as t (t.key)}
      <button role="tab" aria-selected={tab === t.key} class:active={tab === t.key} onclick={() => pick(t.key)}>
        <Icon name={t.key === 'trash' ? 'trash' : t.key === 'done' ? 'check' : t.key === 'lists' ? 'list' : 'archive'} size={14} />
        {t.label}
      </button>
    {/each}
  </div>

  {#if tab === 'lists'}
    {#if !board.archivedSections.length && !board.archivedLists.length}
      <EmptyState icon="archive" title="No archived lists" hint="Archive a list or section from its ⋯ menu; it waits here until you restore it." />
    {:else}
      <div class="inbox-list arch-list" role="list">
        {#each board.archivedSections as s (s.id)}
          <div class="arch-row" role="listitem">
            <Icon name="folder" size={15} />
            <span class="arch-title"><span class="t">{s.name}</span><span class="arch-where">Section</span></span>
            <span class="arch-when muted small">{s.archived_at ? `Archived ${ago(s.archived_at, board.now)}` : ''}</span>
            <span class="arch-actions">
              <button class="secondary small" onclick={() => board.setSectionArchived(s.id, false)}
                ><Icon name="restore" size={13} /> Restore</button
              >
            </span>
          </div>
        {/each}
        {#each board.archivedLists as l (l.id)}
          <div class="arch-row" role="listitem">
            <Icon name="list" size={15} />
            <span class="arch-title"
              ><span class="t">{l.name}</span><span class="arch-where">{board.sections.find((s) => s.id === l.section_id)?.name ?? 'List'}</span></span
            >
            <span class="arch-when muted small">{l.archived_at ? `Archived ${ago(l.archived_at, board.now)}` : ''}</span>
            <span class="arch-actions">
              <button class="secondary small" onclick={() => board.setListArchived(l.id, false)}
                ><Icon name="restore" size={13} /> Restore</button
              >
            </span>
          </div>
        {/each}
      </div>
    {/if}
  {:else}
    <div class="arch-bar">
      <input class="arch-search" type="search" bind:value={q} placeholder="Filter by title" aria-label="Filter by title" />
      {#if tab === 'trash'}
        <span class="muted small">Trashed tasks are deleted for good after the trash period.</span>
        {#if admin && rows?.length}
          {#if confirm === 'empty'}
            <button class="danger-btn" disabled={busy} onclick={emptyTrash}>Delete {rows.length} for good?</button>
            <button class="link small" onclick={() => (confirm = null)}>Cancel</button>
          {:else}
            <button class="secondary" onclick={() => (confirm = 'empty')}><Icon name="trash" size={14} /> Empty trash</button>
          {/if}
        {/if}
      {/if}
    </div>
    {#if rows === null}
      <p class="muted pad-row">Loading…</p>
    {:else if !shown.length}
      <EmptyState
        icon={tab === 'trash' ? 'trash' : tab === 'done' ? 'check' : 'archive'}
        title={q ? 'Nothing matches' : tab === 'trash' ? 'The trash is empty' : tab === 'done' ? 'Nothing finished yet' : 'Nothing put away'}
        hint={q
          ? 'Try another word.'
          : tab === 'trash'
            ? 'Tasks you move to the trash wait here before they go for good.'
            : tab === 'done'
              ? 'Tasks in a done or cancelled status show here, newest first.'
              : 'Tasks you put away leave the board with their history kept and wait here.'}
      />
    {:else}
      <div class="inbox-list arch-list" role="list" aria-label={TABS.find((t) => t.key === tab)?.label}>
        {#each shown as i (i.id)}
          <div class="arch-row" role="listitem" data-item={i.id}>
            <Icon name={tab === 'trash' ? 'trash' : tab === 'done' ? (i.status?.kind === 'cancelled' ? 'x' : 'check') : 'archive'} size={15} />
            <button class="arch-title" onclick={() => board.open(i.id)}>
              <span class="t" class:struck={tab === 'done'}>{i.title}</span>
              {#if parentTitle(i)}<span class="muted small">in {parentTitle(i)}</span>{/if}
              {#if listName(i)}<span class="arch-where">{listName(i)}</span>{/if}
            </button>
            <span class="arch-when muted small" title={tab === 'trash' ? purgeLabel(i.purge_at, board.now) : ''}>
              {when(i)}{#if tab === 'trash'}<span class="arch-purge">{purgeLabel(i.purge_at, board.now)}</span>{/if}
            </span>
            <span class="arch-actions">
              {#if tab === 'done'}
                <button class="secondary small" disabled={busy} title={LEAVES.putAway} onclick={() => archive(i)}><Icon name="archive" size={13} /> Put away</button>
              {:else if tab === 'archived'}
                <button class="secondary small" disabled={busy} onclick={() => unarchive(i)}><Icon name="restore" size={13} /> Bring back</button>
                <button class="icon-btn" disabled={busy} aria-label="Move “{i.title}” to the trash" title="Move to trash. {LEAVES.trash}" onclick={() => toTrash(i)}
                  ><Icon name="trash" size={14} /></button
                >
              {:else if confirm === i.id}
                <button class="danger-btn small" disabled={busy} onclick={() => purge(i)}>Delete for good?</button>
                <button class="link small" onclick={() => (confirm = null)}>Cancel</button>
              {:else}
                <button class="secondary small" disabled={busy || !!(i.parent_id && rows?.some((r) => r.id === i.parent_id))} onclick={() => restore(i)}
                  ><Icon name="restore" size={13} /> Restore</button
                >
                <button class="link small danger" disabled={busy} onclick={() => (confirm = i.id)}>Delete forever</button>
              {/if}
            </span>
          </div>
        {/each}
      </div>
    {/if}
  {/if}
</section>

<style>
  .arch-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .arch-search {
    flex: 0 1 260px;
    min-width: 0;
  }
  .arch-row {
    display: grid;
    grid-template-columns: 18px minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 10px;
    padding: 9px 12px;
    border-top: 1px solid var(--line);
    color: var(--muted);
  }
  .arch-row:first-child {
    border-top: 0;
  }
  .arch-row:hover {
    background: var(--card-hover);
  }
  .arch-title {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
    border: 0;
    padding: 0;
    background: transparent;
    text-align: left;
    color: var(--text);
  }
  .arch-title .t {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .arch-title .t.struck {
    text-decoration: line-through;
    text-decoration-color: var(--muted);
  }
  button.arch-title:hover .t {
    color: var(--accent);
  }
  .arch-where {
    flex: none;
    color: var(--muted);
    font-size: 0.78rem;
  }
  .arch-when {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    white-space: nowrap;
  }
  .arch-purge {
    font-size: 11px;
    color: var(--overdue);
  }
  .arch-actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .arch-actions .small,
  .arch-bar .small {
    font-size: var(--fs-sm);
  }
  .arch-actions .secondary.small {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 9px;
  }
  .work-head .secondary {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .icon-btn {
    display: grid;
    place-items: center;
    width: 26px;
    height: 26px;
    padding: 0;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--muted);
  }
  .icon-btn:hover {
    background: var(--chip);
    color: var(--overdue);
  }
  .danger-btn {
    border: 1px solid var(--danger);
    background: var(--danger);
    color: #fff;
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: 600;
  }
  /* Narrow screens: same pieces, stacked under the title. */
  @media (max-width: 700px) {
    .arch-row {
      grid-template-columns: 18px minmax(0, 1fr);
    }
    .arch-when,
    .arch-actions {
      grid-column: 2;
      align-items: flex-start;
    }
    .arch-title {
      flex-wrap: wrap;
    }
  }
</style>
