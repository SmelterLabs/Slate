<script lang="ts">
  import { workspaceFormatter } from '../../lib/timezone';
  import { inbox, groupEntries, type InboxEntry } from '../../lib/inbox.svelte';
  import { verb } from '../../lib/history';
  import { BUCKETS, myWork, type Bucket } from '../../lib/mywork';
  import { ago } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import Icon from '../Icon.svelte';
  import WorkRow from './WorkRow.svelte';
  import NeedsPanel from './NeedsPanel.svelte';

  // My Work: the canonical "what do I do next" page. Needs you first (lib/attention.ts: the same
  // list, words and count as the pill, the window title and the app icon), then everything
  // assigned to me by when it is due, what depends on another task, what I handed to others
  // (agents included), what is new in my inbox, and what I finished this week. Waiting on me
  // and Asked of you are groups of Needs you, so they have no card of their own here.
  const mod = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl';
  const meId = $derived(board.me?.account.id ?? '');
  const work = $derived(myWork(board.plainItems, meId, board.now));
  const fresh = $derived(groupEntries(inbox.entries.filter((e) => !e.read)).slice(0, 6));

  function line(e: InboxEntry): string {
    const who = board.accountName(e.event.actor_id) ?? 'Someone';
    return `${who} ${verb(e.event, { name: (id) => board.accountName(id), status: statusName, title: (id) => board.items[id]?.title, now: board.now })}`;
  }
  function statusName(id: string): string | undefined {
    for (const l of Object.values(board.lists)) {
      const s = l.statuses.find((x) => x.id === id);
      if (s) return s.name;
    }
    return undefined;
  }

  const hour = $derived(
    Number(
      workspaceFormatter('en-US', { hour: 'numeric', hourCycle: 'h23' }).format(
        board.now,
      ),
    ),
  );
  const greeting = $derived(hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening');
  const firstName = $derived((board.me?.account.display_name ?? '').split(/\s+/)[0]);
  const dateLine = $derived(
    workspaceFormatter('en-US', {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
    }).format(board.now),
  );

  // "Later" and "No due date" start folded so the page opens on what is close. Not saved
  // anywhere: every device opens My Work the same way.
  let folded = $state<Set<Bucket>>(new Set(['later', 'none']));
  function toggle(b: Bucket) {
    const next = new Set(folded);
    if (next.has(b)) next.delete(b);
    else next.add(b);
    folded = next;
  }

  const tiles = $derived([
    { label: 'Needs you', n: board.needsMe.length, hot: board.needsMe.length > 0, href: null },
    { label: 'Overdue', n: work.buckets.overdue.length, hot: work.buckets.overdue.length > 0, href: null },
    { label: 'Due today', n: work.buckets.today.length, hot: false, href: null },
    { label: 'Open', n: work.openCount, hot: false, href: null },
    { label: 'Unread', n: inbox.unread, hot: inbox.unread > 0, href: '#/inbox' },
    { label: 'Done this week', n: work.doneRecently.length, hot: false, href: null },
  ]);
</script>

<section class="work-page" aria-label="My Work">
  <header class="work-head">
    <div>
      <h1>{greeting}{firstName ? `, ${firstName}` : ''}</h1>
      <p class="muted">{dateLine}</p>
    </div>
    <button class="primary" onclick={() => document.getElementById('palette-open')?.click()}>
      Search or run a command <kbd>{mod} K</kbd>
    </button>
  </header>

  <div class="work-tiles">
    {#each tiles as t (t.label)}
      {#if t.href}
        <a class="work-tile" class:hot={t.hot} href={t.href}>
          <span class="n">{t.n}</span><span class="l">{t.label}</span>
        </a>
      {:else}
        <div class="work-tile" class:hot={t.hot}>
          <span class="n">{t.n}</span><span class="l">{t.label}</span>
        </div>
      {/if}
    {/each}
  </div>

  <NeedsPanel layout="groups" />

  <div class="work-cols">
    <div class="work-main">
      {#each BUCKETS as b (b.key)}
        {@const rows = work.buckets[b.key]}
        {#if rows.length || b.key === 'today'}
          <div class="work-group" data-bucket={b.key}>
            <button class="work-group-head" aria-expanded={!folded.has(b.key)} onclick={() => toggle(b.key)}>
              <span class="caret" class:open={!folded.has(b.key)}><Icon name="chevronRight" size={14} /></span>
              <span class="name" class:work-late={b.key === 'overdue'}>{b.label}</span>
              <span class="count">{rows.length}</span>
            </button>
            {#if !folded.has(b.key)}
              <div role="list">
                {#each rows as item (item.id)}<WorkRow {item} />{/each}
                {#if !rows.length}<p class="muted small pad-row">Nothing due today.</p>{/if}
              </div>
            {/if}
          </div>
        {/if}
      {/each}
      {#if !work.openCount}
        <p class="muted pad-row">Nothing is assigned to you. Assign yourself with <code>@{board.me?.account.handle}</code> in quick add.</p>
      {/if}
    </div>

    <aside class="work-side">
      <div class="work-card">
        <h2><a href="#/inbox">Inbox</a> {#if inbox.unread}<span class="count hot">{inbox.unread}</span>{/if}</h2>
        {#each fresh as g (g.itemId ?? g.entries[0].event.id)}
          <button class="work-note" onclick={() => inbox.open(g)}>
            <span class="t">{g.title}</span>
            <span class="muted small">{line(g.entries[0])} · {ago(g.latest, board.now)}</span>
          </button>
        {:else}
          <p class="muted small">You are all caught up.</p>
        {/each}
      </div>

      {#if work.blocked.length}
        <div class="work-card" data-card="blocked">
          <h2>Depends on <span class="count">{work.blocked.length}</span></h2>
          <p class="muted small">Your tasks that depend on another task. Each moves on by itself when that one is done.</p>
          <div role="list">{#each work.blocked as item (item.id)}<WorkRow {item} showBlockers />{/each}</div>
        </div>
      {/if}

      {#if work.delegated.length}
        <div class="work-card">
          <h2>Delegated <span class="count">{work.delegated.length}</span></h2>
          <div role="list">
            {#each work.delegated.slice(0, 12) as item (item.id)}<WorkRow {item} showPeople />{/each}
          </div>
        </div>
      {/if}

      {#if work.doneRecently.length}
        <div class="work-card">
          <h2>Done this week</h2>
          <div role="list">{#each work.doneRecently as item (item.id)}<WorkRow {item} />{/each}</div>
        </div>
      {/if}
    </aside>
  </div>
</section>
