<script lang="ts">
  // Weekly sweep ("Decide"): old overdue cards (>14 days late) from GET /today/sweep, with three
  // ordinary item edits (each with Undo): This week, New date, and Drop.
  import { get } from '../../lib/api';
  import { isOpen, isParked } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import { dropStatus, nextSweepDay } from '../../lib/sweep';
  import type { Item } from '../../lib/types';
  import { daysLate } from '../../lib/views';
  import DatePicker from '../DatePicker.svelte';

  let fetched = $state<Item[]>([]);
  let loading = $state(false);
  let loadGen = 0;

  async function load() {
    if (!board.me) return;
    const gen = ++loadGen;
    loading = true;
    try {
      const got = await get<Item[]>('/today/sweep');
      if (gen === loadGen) fetched = got; // an older answer never overwrites a newer one
    } catch {
      // keep previous list if offline
    } finally {
      if (gen === loadGen) loading = false;
    }
  }

  $effect(() => {
    void board.rev;
    if (board.me) void load();
  });

  const sweepWeekday = $derived(board.prefs.sweep_weekday ?? 4);
  const items = $derived(
    fetched
      .map((f) => board.items[f.id] ?? f)
      .filter((i) => !i.archived_at && isOpen(i) && !i.later && !isParked(i) && !board.lists[i.list_id]?.reference),
  );

  async function thisWeek(item: Item) {
    await board.update(item.id, { due_at: nextSweepDay(board.now, sweepWeekday) });
  }

  async function newDate(item: Item, when: string | null) {
    if (when) await board.update(item.id, { due_at: when });
  }

  async function drop(item: Item) {
    const status = dropStatus(board.lists[item.list_id]);
    if (status) await board.moveToStatus(item.id, status);
    else board.flash(`${board.lists[item.list_id]?.name ?? 'That list'} has no cancelled or done status to drop to`);
  }
</script>

{#if items.length}
  <section class="decide" aria-label="Decide" data-decide={items.length}>
    <header class="dc-head">
      <h2>Decide <span class="dc-n">{items.length}</span></h2>
      <span class="muted small">Old overdue cards. Pick a date or drop them.</span>
    </header>
    <div role="list">
      {#each items as item (item.id)}
        {@const late = daysLate(item, board.now)}
        {@const list = board.lists[item.list_id]}
        <div class="dc-row" role="listitem" data-decide-item={item.id}>
          <button class="dc-main" onclick={() => board.open(item.id)}>
            <span class="dc-title">{item.title}</span>
            <span class="dc-meta muted small">
              {#if late > 0}<span class="dc-late">{late}d overdue</span>{/if}
              {#if list}<span>· {list.name}</span>{/if}
            </span>
          </button>
          <span class="dc-actions">
            <button class="secondary" data-sweep-week onclick={() => void thisWeek(item)}>This week</button>
            <DatePicker
              label="New date"
              value={item.due_at}
              dateOnly={item.due_date_only}
              onchange={(when) => void newDate(item, when)}
            />
            <button class="secondary" data-sweep-drop onclick={() => void drop(item)}>Drop</button>
          </span>
        </div>
      {/each}
    </div>
  </section>
{/if}

<style>
  .decide {
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--panel);
    padding: 12px 12px 8px;
    margin-bottom: 16px;
  }
  .dc-head {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 8px;
  }
  h2 {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.95rem;
    margin: 0;
  }
  .dc-n {
    font-size: 12px;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--overdue) 20%, transparent);
    color: var(--text);
  }
  .dc-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    padding: 7px 6px;
    border-radius: 8px;
  }
  .dc-row:hover {
    background: var(--card-hover);
  }
  .dc-main {
    all: unset;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
    flex: 1;
  }
  .dc-title {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dc-meta {
    display: flex;
    gap: 6px;
  }
  .dc-late {
    color: var(--overdue);
    font-weight: 600;
  }
  .dc-actions {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
</style>
