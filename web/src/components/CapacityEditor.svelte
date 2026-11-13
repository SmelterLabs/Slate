<script lang="ts">
  // How much one person or agent can take on: hours a day (for time estimates), tasks a day,
  // and the days they work. Empty = no limit in that unit.
  // Opened from the Agents page. Saves in one PATCH.
  import { board } from '../lib/store.svelte';
  import type { Account } from '../lib/types';
  import { capacityOf } from '../lib/workload';

  let { account, onclose = () => {} }: { account: Account; onclose?: () => void } = $props();

  const DAYS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
  const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

  // svelte-ignore state_referenced_locally
  const start = capacityOf(account, board.capacities);
  const text = (n: number | null) => (n === null ? '' : String(n));
  let hours = $state(text(start.hours_per_day));
  let tasks = $state(text(start.tasks_per_day));
  let days = $state<number[]>([...start.work_days]);
  let busy = $state(false);

  function num(s: string, max: number, whole = false): number | null | undefined {
    const t = s.trim();
    if (!t) return null;
    const n = Number(t);
    if (!Number.isFinite(n) || n < 0 || n > max || (whole && !Number.isInteger(n))) return undefined;
    return n;
  }

  async function save(e: SubmitEvent) {
    e.preventDefault();
    const h = num(hours, 24);
    const t = num(tasks, 1000, true);
    if (h === undefined) return board.flash('Hours a day is a number from 0 to 24');
    if (t === undefined) return board.flash('Tasks a day is a whole number');
    busy = true;
    const ok = await board.setCapacity(account.id, {
      hours_per_day: h,
      tasks_per_day: t,
      work_days: [...days].sort(),
    });
    busy = false;
    if (ok) onclose();
  }

  async function reset() {
    busy = true;
    if (await board.resetCapacity(account.id)) onclose();
    busy = false;
  }
</script>

<form class="capacity-editor" onsubmit={save} aria-label="Capacity for {account.display_name}">
  <div class="cap-fields">
    <label>Hours a day <input inputmode="decimal" aria-label="Hours a day" placeholder="No limit" bind:value={hours} /></label>
    <label>Tasks a day <input inputmode="numeric" aria-label="Tasks a day" placeholder="No limit" bind:value={tasks} /></label>
  </div>
  <div class="cap-days" role="group" aria-label="Work days">
    {#each DAYS as d, i (i)}
      <button
        type="button"
        class="cap-day"
        class:on={days.includes(i)}
        aria-pressed={days.includes(i)}
        aria-label={DAY_NAMES[i]}
        title={DAY_NAMES[i]}
        onclick={() => (days = days.includes(i) ? days.filter((x) => x !== i) : [...days, i])}>{d}</button
      >
    {/each}
  </div>
  <div class="cap-actions">
    <button class="primary small" type="submit" disabled={busy}>Save</button>
    <button class="link small" type="button" onclick={onclose}>Cancel</button>
    {#if board.capacities[account.id]?.custom}
      <button class="link small cap-reset" type="button" disabled={busy} onclick={reset}>Use defaults</button>
    {/if}
  </div>
</form>
