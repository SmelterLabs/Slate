<script lang="ts">
  // Hover actions on a card or row: reschedule and priority, one click each.
  import { dateInput } from '../lib/format';
  import { priorityLong } from '../lib/priority';
  import { board } from '../lib/store.svelte';
  import type { Item, Priority } from '../lib/types';
  import { PRIORITIES, quickDays } from '../lib/views';
  import Dropdown from './Dropdown.svelte';
  import Icon from './Icon.svelte';

  let { item }: { item: Item } = $props();

  let dueOpen = $state(false);
  let prioOpen = $state(false);
  const days = $derived(quickDays(board.now));

  function due(day: string | null) {
    dueOpen = false;
    void board.reschedule(item.id, day);
  }
  function prio(p: Priority | null) {
    prioOpen = false;
    void board.setPriority(item.id, p);
  }
</script>

<span class="quick">
  <Dropdown title="Reschedule" cls="qa-btn" bind:open={dueOpen}>
    {#snippet label()}<Icon name="calendar" />{/snippet}
    <div class="menu">
      <button onclick={() => due(days.today)}>Today</button>
      <button onclick={() => due(days.tomorrow)}>Tomorrow</button>
      <button onclick={() => due(days.nextWeek)}>Next week <span class="muted small">Mon</span></button>
      <label class="menu-date"
        >Pick <input
          type="date"
          aria-label="Pick a date"
          value={dateInput(item.due_at)}
          onchange={(e) => e.currentTarget.value && due(e.currentTarget.value)}
        /></label
      >
      {#if item.due_at}<button class="danger" onclick={() => due(null)}>Clear date</button>{/if}
    </div>
  </Dropdown>
  <Dropdown title="Priority" cls="qa-btn" bind:open={prioOpen}>
    {#snippet label()}<Icon name="flag" />{/snippet}
    <div class="menu">
      {#each PRIORITIES as p (p)}
        <button class="prio-{p}" class:on={item.priority === p} onclick={() => prio(p)}>{priorityLong(p)}</button>
      {/each}
      {#if item.priority}<button onclick={() => prio(null)}>No priority</button>{/if}
    </div>
  </Dropdown>
</span>
