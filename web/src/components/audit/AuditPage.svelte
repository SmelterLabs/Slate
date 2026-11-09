<script lang="ts">
  // The audit log (`#/audit`): every change, newest first, with who did it (person or agent),
  // through which door, and what it touched. Filters narrow it (who, people or agents, door,
  // kind of change, section/list, dates, text); Export downloads the same filter as CSV. New
  // events refresh the top of the log live. Everyone can open it: the server shows each
  // account only what its access can read (owners and admins see it all).
  import { onDestroy } from 'svelte';
  import { ApiError, get } from '../../lib/api';
  import { download } from '../../lib/archive';
  import { canRevert, revertEvent, revertQuestion } from '../../lib/revert';
  import { NO_FILTERS, activeFilters, auditQuery, auditTone, byDay, changeLine, type AuditFilters } from '../../lib/permissions';
  import { board } from '../../lib/store.svelte';
  import type { AuditActor, AuditEntry, AuditPage } from '../../lib/types';
  import EmptyState from '../EmptyState.svelte';
  import Icon from '../Icon.svelte';

  const PAGE = 60;
  const TYPE_GROUPS: { value: string; label: string }[] = [
    { value: '', label: 'Any change' },
    { value: 'item.', label: 'Tasks' },
    { value: 'item.created', label: 'Tasks created' },
    { value: 'item.status_changed', label: 'Status changes' },
    { value: 'item.assigned', label: 'Assignments' },
    { value: 'item.commented,comment.', label: 'Comments' },
    { value: 'item.archived,item.trashed,item.deleted,item.purged', label: 'Archived and deleted' },
    { value: 'share.,account.role_changed', label: 'Sharing and roles' },
    { value: 'account.,token.', label: 'Accounts and tokens' },
    { value: 'list.,section.', label: 'Lists and sections' },
    { value: 'message.', label: 'Chat' },
    { value: 'automation.', label: 'Automations' },
  ];
  const SOURCES = [
    { value: '', label: 'Any door' },
    { value: 'ui', label: 'Board' },
    { value: 'api', label: 'API' },
    { value: 'mcp', label: 'MCP' },
    { value: 'system', label: 'System' },
  ] as const;

  let filters = $state<AuditFilters>({ ...NO_FILTERS });
  let entries = $state<AuditEntry[] | null>(null);
  let next = $state<string | null>(null);
  let more = $state(false);
  let loadingMore = $state(false);
  let actors = $state<AuditActor[]>([]);
  let open = $state<string | null>(null);
  let showFilters = $state(false);
  let fresh = $state(0);

  const groups = $derived(byDay(entries ?? [], board.now));
  const nFilters = $derived(activeFilters(filters));
  const listsIn = $derived(filters.section ? board.listsOf(filters.section) : []);

  async function load() {
    const want = JSON.stringify(filters);
    try {
      const page = await get<AuditPage>(`/audit${auditQuery(filters, { limit: String(PAGE) })}`);
      if (want !== JSON.stringify(filters)) return;
      entries = page.entries;
      next = page.next_before;
      more = page.has_more;
      fresh = 0;
    } catch (e) {
      entries = [];
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async function older() {
    if (!next) return;
    loadingMore = true;
    try {
      const page = await get<AuditPage>(`/audit${auditQuery(filters, { limit: String(PAGE), before: next })}`);
      entries = [...(entries ?? []), ...page.entries];
      next = page.next_before;
      more = page.has_more;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      loadingMore = false;
    }
  }

  $effect(() => {
    JSON.stringify(filters);
    void load();
  });
  $effect(() => {
    void get<{ actors: AuditActor[] }>('/audit/facets')
      .then((f) => (actors = f.actors))
      .catch(() => {});
  });

  // Live: new events refresh the first page (debounced) unless the reader has paged back.
  let timer: ReturnType<typeof setTimeout> | undefined;
  const off = board.onEvent(() => {
    if ((entries?.length ?? 0) > PAGE) {
      fresh += 1;
      return;
    }
    clearTimeout(timer);
    timer = setTimeout(() => void load(), 400);
  });
  onDestroy(() => {
    off();
    clearTimeout(timer);
  });

  function reset() {
    filters = { ...NO_FILTERS };
  }

  async function exportCsv() {
    try {
      const name = await download(`/audit.csv${auditQuery(filters)}`, board.token, 'slate-audit.csv');
      board.flash(`Downloaded ${name}`);
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });
  const full = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
  const DOOR: Record<string, string> = { ui: 'Board', api: 'API', mcp: 'MCP', system: 'System' };

  function initials(n: string | null): string {
    return (n ?? '?')
      .split(/\s+/)
      .map((w) => w[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }

  function pretty(v: unknown): string {
    return v === null || v === undefined ? '—' : JSON.stringify(v, null, 1);
  }

  function byActor(a: AuditActor) {
    filters.actor = a.handle ?? a.id;
  }

  // Revert from the log: a change someone else made goes back through POST /events/{id}/revert
  // (a new change as you; the log keeps both). A 409 leaves the row disabled with the reason.
  const me = $derived(board.me?.account.id);
  const revertable = (e: AuditEntry) => ({ type: e.type, field: e.field, actor_id: e.actor.id, session_ref: e.session_ref });
  let reverting = $state<string | null>(null);
  let revertBlocked = $state<Record<string, string>>({});
  async function revert(e: AuditEntry) {
    if (!window.confirm(revertQuestion(revertable(e), e.actor.display_name ?? 'someone'))) return;
    reverting = e.id;
    try {
      await revertEvent(e.id);
      board.flash('Put back. The log keeps both changes.');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (err instanceof ApiError && err.status === 409) revertBlocked[e.id] = msg;
      board.flash(msg);
    } finally {
      reverting = null;
    }
  }
</script>

<section class="work-page audit-page" aria-label="Audit log">
  <header class="work-head">
    <div>
      <h1>Audit log</h1>
      <p class="muted">Who did what, when, and through which door. People and agents alike; nothing is ever edited or removed.</p>
    </div>
    <div class="inline">
      <button class="secondary" onclick={exportCsv}><Icon name="download" size={14} /> Export CSV</button>
    </div>
  </header>

  <div class="audit-bar">
    <input class="audit-search" type="search" bind:value={filters.q} placeholder="Search task titles and session refs" aria-label="Search the log" />
    <div class="seg" role="radiogroup" aria-label="Who">
      {#each [['', 'Everyone'], ['person', 'People'], ['agent', 'Agents']] as [v, l] (v)}
        <button role="radio" aria-checked={filters.kind === v} class:on={filters.kind === v} onclick={() => (filters.kind = v as AuditFilters['kind'])}>
          {#if v === 'agent'}<Icon name="robot" size={13} />{:else if v === 'person'}<Icon name="user" size={13} />{/if}{l}
        </button>
      {/each}
    </div>
    <button class="secondary filter-toggle" aria-expanded={showFilters} onclick={() => (showFilters = !showFilters)}
      ><Icon name="filter" size={14} /> Filters{#if nFilters}<span class="tab-count">{nFilters}</span>{/if}</button
    >
  </div>

  {#if showFilters}
    <div class="audit-filters" aria-label="Filters">
      <label class="field">
        <span class="muted small">Account</span>
        <select aria-label="Account" bind:value={filters.actor}>
          <option value="">Anyone</option>
          {#each actors as a (a.id)}<option value={a.handle ?? a.id}>{a.display_name} (@{a.handle}){a.kind === 'agent' ? ' · agent' : ''}</option>{/each}
        </select>
      </label>
      <label class="field">
        <span class="muted small">Kind of change</span>
        <select aria-label="Kind of change" bind:value={filters.type}>
          {#each TYPE_GROUPS as t (t.value)}<option value={t.value}>{t.label}</option>{/each}
        </select>
      </label>
      <label class="field">
        <span class="muted small">Door</span>
        <select aria-label="Door" bind:value={filters.source}>
          {#each SOURCES as s (s.value)}<option value={s.value}>{s.label}</option>{/each}
        </select>
      </label>
      <label class="field">
        <span class="muted small">Section</span>
        <select aria-label="Section" bind:value={filters.section} onchange={() => (filters.list = '')}>
          <option value="">Anywhere</option>
          {#each board.sections as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
        </select>
      </label>
      <label class="field">
        <span class="muted small">List</span>
        <select aria-label="List" bind:value={filters.list} disabled={!filters.section}>
          <option value="">All lists</option>
          {#each listsIn as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
        </select>
      </label>
      <label class="field">
        <span class="muted small">From</span>
        <input type="date" aria-label="From" bind:value={filters.since} />
      </label>
      <label class="field">
        <span class="muted small">To</span>
        <input type="date" aria-label="To" bind:value={filters.until} />
      </label>
      <button class="link" onclick={reset} disabled={!nFilters}>Clear filters</button>
    </div>
  {/if}

  {#if fresh}
    <button class="audit-fresh" onclick={load}><Icon name="up" size={13} /> {fresh} new {fresh === 1 ? 'change' : 'changes'}: back to the top</button>
  {/if}

  {#if entries === null}
    <p class="muted pad-row">Loading…</p>
  {:else if !entries.length}
    <EmptyState icon="history" title={nFilters ? 'Nothing matches' : 'No history yet'} hint={nFilters ? 'Loosen a filter or clear them.' : 'Every change anyone makes shows up here.'} />
  {:else}
    <div class="audit-log">
      {#each groups as g (g.day)}
        <h2 class="audit-day">{g.label}</h2>
        <ol class="inbox-list audit-list" aria-label={g.label}>
          {#each g.entries as e (e.id)}
            {@const line = changeLine(e)}
            <li class="audit-row tone-{auditTone(e.type)}" data-type={e.type}>
              <time class="audit-time muted small" datetime={e.at} title={full.format(new Date(e.at))}>{time.format(new Date(e.at))}</time>
              <span class="audit-dot" aria-hidden="true"></span>
              <button class="avatar" class:agent={e.actor.kind === 'agent'} title="Only {e.actor.display_name}" onclick={() => byActor(e.actor)}
                >{#if e.actor.kind === 'agent'}<Icon name="robot" size={13} />{:else}{initials(e.actor.display_name)}{/if}</button
              >
              <div class="audit-what">
                <p>
                  <button class="audit-who" onclick={() => byActor(e.actor)}>{e.actor.display_name ?? e.actor.id}</button>
                  {#if e.item}
                    {e.summary.replace(/ “.*”$/, '')}
                    <button class="audit-item" onclick={() => board.open(e.item!.id)}>{e.item.title}</button>
                  {:else}
                    {e.summary}
                  {/if}
                </p>
                <p class="audit-meta muted small">
                  <span class="door door-{e.source}">{DOOR[e.source] ?? e.source}</span>
                  {#if e.item?.list_name}<span><Icon name="list" size={11} /> {e.item.list_name}</span>{/if}
                  {#if line}<span class="audit-change">{line}</span>{/if}
                  {#if e.session_ref}<span title="Session reference">↳ {e.session_ref}</span>{/if}
                  <button class="link small" aria-expanded={open === e.id} onclick={() => (open = open === e.id ? null : e.id)}>{open === e.id ? 'Hide' : 'Details'}</button>
                  {#if canRevert(revertable(e), me)}
                    <button
                      class="link small"
                      data-revert={e.id}
                      disabled={reverting === e.id || !!revertBlocked[e.id]}
                      title={revertBlocked[e.id] ?? 'Put this change back (a new change, as you)'}
                      onclick={() => revert(e)}>{revertBlocked[e.id] ? 'Changed since' : 'Revert'}</button
                    >
                  {/if}
                </p>
                {#if open === e.id}
                  <dl class="audit-detail">
                    <dt>Event</dt><dd><code>{e.type}</code> · <code>{e.id}</code></dd>
                    <dt>When</dt><dd>{full.format(new Date(e.at))}</dd>
                    <dt>Who</dt><dd>{e.actor.display_name} (@{e.actor.handle}){e.actor.kind === 'agent' ? ', an agent' : ''}</dd>
                    {#if e.field}<dt>Field</dt><dd><code>{e.field}</code></dd>{/if}
                    <dt>Before</dt><dd><pre>{pretty(e.before)}</pre></dd>
                    <dt>After</dt><dd><pre>{pretty(e.after)}</pre></dd>
                  </dl>
                {/if}
              </div>
            </li>
          {/each}
        </ol>
      {/each}
      {#if more}
        <button class="secondary audit-more" disabled={loadingMore} onclick={older}>{loadingMore ? 'Loading…' : 'Show older'}</button>
      {/if}
    </div>
  {/if}
</section>
