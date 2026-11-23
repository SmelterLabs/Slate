<script lang="ts">
  // A date with an optional time, workspace calendar. Quick picks apply at once; a bare date is
  // sent as YYYY-MM-DD (flagged date-only), and its time is not shown. Time can be typed
  // naturally ("930a", "2:30pm", "14:30") or picked with one click.
  import { workspaceDay } from '../lib/quickadd';
  import { board } from '../lib/store.svelte';
  import { longDay, joinWhen, quickPicks, splitWhen, timeLabel } from '../lib/when';
  import TimePicker from './TimePicker.svelte';

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
  let root = $state<HTMLDivElement | null>(null);
  const parts = $derived(splitWhen(value, dateOnly));
  const picks = $derived(quickPicks(board.now, value));

  const shown = $derived(
    parts.date ? `${longDay(parts.date)}${parts.time ? ` · ${timeLabel(parts.time)}` : ''}` : '',
  );

  function set(date: string, time: string) {
    const next = joinWhen(date, time);
    if (next !== joinWhen(parts.date, parts.time)) onchange(next);
  }

  function setTime(time: string) {
    const day = parts.date || workspaceDay(board.now);
    set(day, time);
  }

  $effect(() => {
    if (!open) return;
    // Close on bubble `click` (not `pointerdown`) so collapsing this inline panel never shifts
    // a control below it out from under the pointer mid-click.
    const onClick = (e: MouseEvent) => {
      if (root && !root.contains(e.target as Node)) open = false;
    };
    window.addEventListener('click', onClick);
    return () => window.removeEventListener('click', onClick);
  });
</script>

<div class="datepick" bind:this={root}>
  <button
    class="datepick-value"
    class:empty={!shown}
    aria-label="{label}: {shown || 'none'}"
    aria-expanded={open}
    onclick={() => (open = !open)}>{shown || 'Set date'}</button
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
      <div class="datepick-row">
        <input
          type="date"
          aria-label="{label} date"
          value={parts.date}
          onchange={(e) => set(e.currentTarget.value, parts.time)}
        />
        <button class="link small" onclick={() => (open = false)}>Done</button>
      </div>
      <TimePicker {label} value={parts.time} onchange={setTime} />
    </div>
  {/if}
</div>

<style>
  .datepick-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
</style>
