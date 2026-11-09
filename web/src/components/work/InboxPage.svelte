<script lang="ts">
  import { describe, type HistoryContext } from '../../lib/history';
  import { groupEntries, inbox, REASON_LABEL, type InboxEntry, type Reason } from '../../lib/inbox.svelte';
  import { ago } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import Icon, { type IconName } from '../Icon.svelte';
  import NeedsPanel from './NeedsPanel.svelte';
  import { waitingOn } from '../../lib/words';

  // The inbox: one row per item, newest first, with what happened in plain English. Opening a
  // row opens the task and marks its entries read; Clear takes it out of the inbox.
  type Tab = 'all' | 'mentions' | 'assigned' | 'agents' | 'unread';
  const TABS: { key: Tab; label: string }[] = [
    { key: 'all', label: 'All' },
    { key: 'unread', label: 'Unread' },
    { key: 'mentions', label: 'Mentions' },
    { key: 'assigned', label: 'Assigned to me' },
    { key: 'agents', label: 'Agent activity' },
  ];
  let tab = $state<Tab>('all');

  const ctx: HistoryContext = {
    name: (id) => board.accountName(id),
    title: (id) => board.items[id]?.title,
    status: (id) => {
      for (const l of Object.values(board.lists)) {
        const s = l.statuses.find((x) => x.id === id);
        if (s) return s.name;
      }
      return undefined;
    },
  };

  const isAgent = (e: InboxEntry) => board.accounts[e.event.actor_id]?.kind === 'agent';
  const want: Record<Tab, (e: InboxEntry) => boolean> = {
    all: () => true,
    unread: (e) => !e.read,
    mentions: (e) => e.reason === 'mention' || e.reason === 'reply',
    assigned: (e) => ['assigned', 'waiting', 'due', 'unblocked', 'nudge', 'reminder', 'digest', 'access'].includes(e.reason),
    agents: isAgent,
  };
  const groups = $derived(groupEntries(inbox.entries.filter(want[tab])));
  const counts = $derived(
    Object.fromEntries(TABS.map((t) => [t.key, inbox.entries.filter(want[t.key]).filter((e) => !e.read).length])),
  );

  const ICON: Record<Reason, IconName> = {
    mention: 'at',
    reply: 'reply',
    assigned: 'user',
    waiting: 'hourglass',
    due: 'clock',
    unblocked: 'play',
    nudge: 'robot',
    reminder: 'bell',
    digest: 'sun',
    health: 'pulse',
    access: 'lock',
    comment: 'comment',
    status: 'status',
    update: 'edit',
    archived: 'archive',
    moved: 'list',
    watching: 'eye',
  };
</script>

<section class="work-page inbox-page" aria-label="Inbox">
  <header class="work-head">
    <div>
      <h1>Inbox</h1>
      <p class="muted">What happened on tasks you follow. What you have to act on is under Needs you, the same list as My Work.</p>
    </div>
    <div class="inline">
      <button class="secondary" disabled={!inbox.unread} onclick={() => void inbox.readAll()}>Mark all read</button>
      <button class="secondary" disabled={!inbox.entries.length} onclick={() => void inbox.clearAll()}>Clear all</button>
    </div>
  </header>

  <NeedsPanel layout="groups" />

  <div class="inbox-tabs" role="tablist">
    {#each TABS as t (t.key)}
      <button role="tab" aria-selected={tab === t.key} class:active={tab === t.key} onclick={() => (tab = t.key)}>
        {t.label}
        {#if counts[t.key]}<span class="count hot">{counts[t.key]}</span>{/if}
      </button>
    {/each}
  </div>

  {#if !inbox.loaded}
    <p class="muted pad-row">Loading…</p>
  {:else if !groups.length}
    <div class="inbox-empty">
      <div class="big">✓</div>
      <p>{tab === 'all' ? (board.needsMe.length ? 'Inbox zero. Needs you above is what is left to act on.' : 'Inbox zero, and nothing needs you.') : 'Nothing here.'}</p>
    </div>
  {:else}
    <div class="inbox-list" role="list">
      {#each groups as g (g.itemId ?? g.entries[0].event.id)}
        <div class="inbox-row" class:unread={g.unread > 0} role="listitem" data-item={g.itemId}>
          <span class="inbox-dot" aria-label={g.unread ? `${g.unread} unread` : 'read'}></span>
          <button class="inbox-main" onclick={() => inbox.open(g)}>
            <span class="inbox-title">
              <span class="t">{g.title}</span>
              {#if g.listId && board.lists[g.listId]}<span class="work-list">{board.lists[g.listId].name}</span>{/if}
            </span>
            {#each g.entries.slice(0, 3) as e (e.event.id)}
              <span class="inbox-line" class:agent={isAgent(e)}>
                <span class="inbox-icon" title={e.reason === 'waiting' ? waitingOn(board.me?.account.display_name) : REASON_LABEL[e.reason]}><Icon name={ICON[e.reason]} size={13} /></span>
                {#if isAgent(e)}<Icon name="robot" size={13} />{/if}
                <span class="what">{describe(e.event, { ...ctx, now: board.now })}</span>
              </span>
            {/each}
            {#if g.entries.length > 3}<span class="muted small">and {g.entries.length - 3} more</span>{/if}
          </button>
          <span class="inbox-side">
            <span class="muted small">{ago(g.latest, board.now)}</span>
            <button class="link small" onclick={() => void inbox.clearGroup(g)}>Clear</button>
          </span>
        </div>
      {/each}
    </div>
  {/if}
</section>
