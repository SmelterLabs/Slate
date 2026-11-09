<script lang="ts">
  // Reminders in the task window: chips for this task's reminders (×  removes) and a "Remind
  // me" menu. "Before due" toggles (at due, 5 min … 1 week, or a custom amount) set any number
  // of alerts at once and stay open; below them one-click times, a custom date and time, an
  // optional note, and who to remind.
  // The server fires them into the inbox, check-in and phone/desktop notifications. Live: any
  // reminder event on this task reloads the chips, so every open window agrees.
  import { onDestroy } from 'svelte';
  import { api, get, post } from '../../lib/api';
  import { dueLabel } from '../../lib/format';
  import {
    BEFORE_DUE,
    customLead,
    fromLocalInput,
    leadText,
    presets,
    whenText,
    type LeadUnit,
    type Reminder,
    type ReminderBody,
  } from '../../lib/reminders';
  import { board } from '../../lib/store.svelte';
  import type { Item } from '../../lib/types';
  import Dropdown from '../Dropdown.svelte';
  import Icon from '../Icon.svelte';

  let { item }: { item: Item } = $props();

  let list = $state<Reminder[]>([]);
  let open = $state(false);
  let custom = $state('');
  let note = $state('');
  let who = $state('');
  let busy = $state(false);

  const me = $derived(board.me?.account.id ?? '');
  const people = $derived(Object.values(board.accounts).filter((a) => a.handle !== 'slate' && !a.disabled_at));
  const choices = $derived(presets(board.now));
  let leadAmount = $state('');
  let leadUnit = $state<LeadUnit>('hour');
  const target = $derived(who || me);
  // The before-due alert this panel's target already has at a lead, if any.
  const alertAt = (minutes: number) =>
    list.find((r) => r.before_minutes === minutes && r.remind_at === null && r.account_id === target);
  const firesText = (r: Reminder) => (r.at ? `Goes off ${dueLabel(r.at, board.now, false)}` : (r.note ?? ''));
  const pending = $derived(list.filter((r) => !r.fired));
  const nameOf = (r: Reminder) => (r.account_id === me ? '' : `${board.accountName(r.account_id) ?? r.account} · `);

  async function load(id: string) {
    try {
      const rows = await get<Reminder[]>(`/items/${id}/reminders`);
      if (id === item.id) list = rows;
    } catch {
      // the chips stay as they were
    }
  }

  $effect(() => {
    void load(item.id);
  });
  const off = board.onEvent((ev) => {
    if (ev.item_id === item.id && ev.type.startsWith('item.reminder')) void load(item.id);
  });
  onDestroy(off);

  async function add(body: ReminderBody, keepOpen = false) {
    busy = true;
    try {
      const target = who && who !== me ? board.accounts[who]?.handle : undefined;
      const r = await post<Reminder>(`/items/${item.id}/reminders`, {
        ...body,
        ...(note.trim() ? { note: note.trim() } : {}),
        ...(target ? { account: target } : {}),
      });
      list = [...list.filter((x) => x.id !== r.id), r];
      board.flash(`Reminder set: ${whenText(r, board.now)}`);
      if (!keepOpen) {
        open = false;
        note = '';
        custom = '';
      }
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }

  async function remove(r: Reminder) {
    list = list.filter((x) => x.id !== r.id);
    try {
      await api('DELETE', `/reminders/${r.id}`);
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      void load(item.id);
    }
  }

  function toggle(minutes: number) {
    const have = alertAt(minutes);
    if (have) void remove(have);
    else void add({ before_minutes: minutes }, true);
  }

  function addLead(e: SubmitEvent) {
    e.preventDefault();
    const minutes = customLead(leadAmount, leadUnit);
    if (minutes === null) {
      board.flash('Pick a whole number, up to 60 days before due');
      return;
    }
    if (!alertAt(minutes)) void add({ before_minutes: minutes }, true);
    leadAmount = '';
  }

  function addCustom(e: SubmitEvent) {
    e.preventDefault();
    const at = fromLocalInput(custom);
    if (at) void add({ at });
  }
</script>

<div class="rem">
  {#each pending as r (r.id)}
    <span class="rem-chip" title={firesText(r)}>
      <Icon name="bell" size={12} />
      <span>{nameOf(r)}{whenText(r, board.now)}{r.note ? ` · ${r.note}` : ''}</span>
      <button class="rem-x" aria-label="Remove reminder {whenText(r, board.now)}" onclick={() => void remove(r)}
        ><Icon name="x" size={11} /></button
      >
    </span>
  {/each}
  <Dropdown float title="Remind me" cls="rem-add" bind:open>
    {#snippet label()}<Icon name="bell" size={13} /> {pending.length ? 'Add' : 'Remind me'}{/snippet}
    <div class="rem-panel">
      <div class="rem-before" role="group" aria-label="Before due">
        <span class="rem-head muted">Before due</span>
        {#if item.due_at}
          <div class="rem-toggles">
            {#each BEFORE_DUE as b (b.minutes)}
              <button
                type="button"
                class="rem-toggle"
                aria-pressed={!!alertAt(b.minutes)}
                aria-label={b.minutes ? leadText(b.minutes) : 'At the due time'}
                disabled={busy}
                onclick={() => toggle(b.minutes)}>{b.label}</button
              >
            {/each}
          </div>
          <form class="rem-lead" onsubmit={addLead}>
            <input type="number" min="1" step="1" bind:value={leadAmount} placeholder="Other" aria-label="Alert amount" />
            <select bind:value={leadUnit} aria-label="Alert unit">
              <option value="min">minutes</option>
              <option value="hour">hours</option>
              <option value="day">days</option>
              <option value="week">weeks</option>
            </select>
            <span class="muted">before</span>
            <button class="primary small" disabled={busy || !leadAmount}>Add</button>
          </form>
        {:else}
          <span class="muted rem-hint">Give the task a due date to set alerts before it.</span>
        {/if}
      </div>
      <span class="rem-head muted">At a time</span>
      <div class="rem-presets" role="group" aria-label="Remind me">
        {#each choices as p (p.key)}
          <button type="button" class="rem-preset" disabled={busy} onclick={() => void add(p.body)}>{p.label}</button>
        {/each}
      </div>
      <form class="rem-custom" onsubmit={addCustom}>
        <input type="datetime-local" bind:value={custom} aria-label="Remind at" />
        <button class="primary small" disabled={busy || !custom}>Set</button>
      </form>
      <input class="rem-note" bind:value={note} maxlength="280" placeholder="Note (optional)" aria-label="Reminder note" />
      <label class="rem-who">
        <span class="muted">Remind</span>
        <select bind:value={who} aria-label="Who to remind">
          <option value="">Me</option>
          {#each people.filter((a) => a.id !== me) as a (a.id)}<option value={a.id}>{a.display_name}</option>{/each}
        </select>
      </label>
    </div>
  </Dropdown>
</div>

<style>
  .rem {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }
  .rem-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 4px 2px 8px;
    border-radius: var(--radius-pill);
    background: var(--accent-soft);
    color: var(--accent-ink);
    font-size: var(--fs-sm);
    max-width: 100%;
  }
  .rem-chip > span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rem-x {
    display: grid;
    place-items: center;
    width: 18px;
    height: 18px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: transparent;
    color: inherit;
  }
  .rem-x:hover {
    background: color-mix(in srgb, currentColor 15%, transparent);
  }
  :global(.dd-btn.rem-add) {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    color: var(--muted);
    font-size: var(--fs-sm);
  }
  .rem-panel {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px;
    min-width: 240px;
  }
  .rem-presets {
    display: flex;
    flex-direction: column;
  }
  .rem-before {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--line);
  }
  .rem-head {
    font-size: var(--fs-sm);
  }
  .rem-hint {
    font-size: var(--fs-sm);
  }
  .rem-toggles {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    max-width: 300px;
  }
  .rem-toggle {
    padding: 3px 9px;
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    background: transparent;
    color: var(--text);
    font-size: var(--fs-sm);
  }
  .rem-toggle[aria-pressed='true'] {
    background: var(--accent-soft);
    border-color: var(--accent);
    color: var(--accent-ink);
  }
  .rem-lead {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: var(--fs-sm);
  }
  .rem-lead input {
    width: 64px;
  }
  .rem-preset {
    text-align: left;
    padding: 6px 8px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--text);
  }
  .rem-preset:hover {
    background: var(--card-hover);
  }
  .rem-custom {
    display: flex;
    gap: 6px;
    padding-top: 8px;
    border-top: 1px solid var(--line);
  }
  .rem-custom input {
    flex: 1;
    min-width: 0;
  }
  .rem-who {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: var(--fs-sm);
  }
  .rem-who select {
    flex: 1;
  }
</style>
