<script lang="ts">
  // A date with an optional time, workspace calendar. Quick picks apply at once; a bare date is
  // sent as YYYY-MM-DD (stored as 17:00 workspace time, flagged date-only), and its time is not
  // shown.
  import { board } from '../lib/store.svelte';
  import { longDay, joinWhen, quickPicks, splitWhen, timeLabel } from '../lib/when';

  let {
    label,
    value,
    dateOnly,
    onchange,
  }: {
    label: string;
    value: string | null;
    /** The item's `due_date_only` (a due date); start dates leave it out. */
    dateOnly?: boolean;
    onchange: (when: string | null) => void;
  } = $props();

  let open = $state(false);
  const parts = $derived(splitWhen(value, dateOnly));
  let withTime = $state(false);
  const picks = $derived(quickPicks(board.now, value));

  const shown = $derived(
    parts.date ? `${longDay(parts.date)}${parts.time ? ` · ${timeLabel(parts.time)}` : ''}` : '',
  );

  function set(date: string, time: string) {
    const next = joinWhen(date, time);
    if (next !== joinWhen(parts.date, parts.time)) onchange(next);
  }
</script>

<div class="datepick">
  <button
    class="datepick-value"
    class:empty={!shown}
    aria-label="{label}: {shown || 'none'}"
    aria-expanded={open}
    onclick={() => {
      open = !open;
      withTime = !!parts.time;
    }}>{shown || 'Set date'}</button
  >
  {#if open}
    <div class="datepick-pop" role="group" aria-label="{label} picker">
      <div class="inline wrap">
        {#each picks as p (p.label)}
          <button
            class="pick"
            class:on={p.date === parts.date}
            onclick={() => {
              set(p.date, parts.time);
              open = false;
            }}>{p.label}</button
          >
        {/each}
        {#if value}
          <button
            class="pick"
            onclick={() => {
              onchange(null);
              open = false;
            }}>Clear</button
          >
        {/if}
      </div>
      <div class="inline wrap">
        <input
          type="date"
          aria-label="{label} date"
          value={parts.date}
          onchange={(e) => set(e.currentTarget.value, parts.time)}
        />
        {#if withTime}
          <input
            type="time"
            aria-label="{label} time"
            value={parts.time}
            onchange={(e) => set(parts.date, e.currentTarget.value)}
          />
          <button
            class="link small"
            onclick={() => {
              withTime = false;
              set(parts.date, '');
            }}>no time</button
          >
        {:else if parts.date}
          <button class="link small" onclick={() => (withTime = true)}>+ time</button>
        {/if}
        <button class="link small" onclick={() => (open = false)}>Done</button>
      </div>
    </div>
  {/if}
</div>
