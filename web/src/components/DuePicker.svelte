<script lang="ts">
  // The due pill as a button: ClickUp's quick dates, a calendar pick, or clear.
  import type { Snippet } from 'svelte';
  import { dateInput } from '../lib/format';
  import { board } from '../lib/store.svelte';
  import type { Item } from '../lib/types';
  import { addDays, quickDays, weekday } from '../lib/views';
  import Dropdown from './Dropdown.svelte';

  let { item, children, cls = 'pill pill-btn' }: { item: Item; children: Snippet; cls?: string } = $props();

  let open = $state(false);
  const days = $derived(quickDays(board.now));
  // This weekend = the coming Saturday (today when it is Saturday).
  const weekend = $derived(addDays(days.today, (6 - weekday(days.today) + 7) % 7));

  function pick(day: string | null) {
    open = false;
    void board.reschedule(item.id, day);
  }
</script>

<Dropdown float title="Due date" {cls} bind:open label={children}>
  <div class="menu">
    <button onclick={() => pick(days.today)}>Today</button>
    <button onclick={() => pick(days.tomorrow)}>Tomorrow</button>
    <button onclick={() => pick(weekend)}>This weekend <span class="muted small">Sat</span></button>
    <button onclick={() => pick(days.nextWeek)}>Next week <span class="muted small">Mon</span></button>
    <button onclick={() => pick(addDays(days.today, 14))}>2 weeks</button>
    <label class="menu-date"
      >Pick <input
        type="date"
        aria-label="Pick a date"
        value={dateInput(item.due_at)}
        onchange={(e) => e.currentTarget.value && pick(e.currentTarget.value)}
      /></label
    >
    {#if item.due_at}<button class="danger" onclick={() => pick(null)}>Clear date</button>{/if}
  </div>
</Dropdown>
