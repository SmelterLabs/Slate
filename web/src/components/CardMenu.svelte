<script lang="ts">
  // A card's ⋯ menu (also what right-click opens): the everyday moves without opening the task.
  import { board } from '../lib/store.svelte';
  import { priorityLong } from '../lib/priority';
  import type { Item } from '../lib/types';
  import { copyText } from '../lib/clipboard';
  import { dateInput, shortDate } from '../lib/format';
  import { PRIORITIES, isClosed, quickDays } from '../lib/views';
  import Dropdown from './Dropdown.svelte';
  import Icon from './Icon.svelte';
  import MovePicker from './MovePicker.svelte';
  import { LEAVES } from '../lib/words';

  let { item, open = $bindable(false) }: { item: Item; open?: boolean } = $props();

  let confirmArchive = $state(false);
  // The menu swaps its actions for the list picker; closing the menu swaps them back.
  let moving = $state(false);
  $effect(() => {
    if (!open) moving = false;
  });
  const days = $derived(quickDays(board.now));
  const closed = $derived(isClosed(item));
  // The due day as YYYY-MM-DD in workspace time, so the row can show which choice is current.
  const dueDay = $derived(dateInput(item.due_at));
  const picked = $derived(!!dueDay && dueDay !== days.today && dueDay !== days.tomorrow);
  let dateEl: HTMLInputElement | undefined = $state();

  // Open the browser's calendar under the button; a browser without showPicker gets focus.
  function pickDate() {
    if (!dateEl) return;
    try {
      dateEl.showPicker();
    } catch {
      dateEl.focus();
    }
  }

  function run(fn: () => unknown) {
    open = false;
    confirmArchive = false;
    void fn();
  }
</script>

<Dropdown float title="Task actions" cls="icon card-more" align="right" bind:open>
  {#snippet label()}<Icon name="more" size={16} />{/snippet}
  <div class="menu card-menu" role="menu">
    {#if moving}
    <button role="menuitem" class="move-back" onclick={(e) => { e.stopPropagation(); moving = false; }}
      ><Icon name="chevronLeft" /> Move “{item.title}” to…</button
    >
    <MovePicker current={item.list_id} onpick={(id) => run(() => board.moveItem(item.id, id))} />
    {:else}
    <button role="menuitem" onclick={() => run(() => board.open(item.id))}><Icon name="open" /> Open</button>
    <button role="menuitem" title={closed ? 'Back to an open status' : LEAVES.complete} onclick={() => run(() => board.toggleComplete(item.id))}
      ><Icon name="check" /> {closed ? 'Mark not done' : 'Complete'}</button
    >
    {#if board.canRun(item)}
      <button role="menuitem" onclick={() => run(() => board.runAction(item))}
        ><Icon name="robot" /> {board.runLabel(item)}</button
      >
    {/if}
    <div class="menu-label">Due</div>
    <div class="menu-row chips due-row">
      <button class:on={dueDay === days.today} onclick={() => run(() => board.reschedule(item.id, days.today))}
        >Today</button
      >
      <button class:on={dueDay === days.tomorrow} onclick={() => run(() => board.reschedule(item.id, days.tomorrow))}
        >Tomorrow</button
      >
      <span class="menu-pick">
        <button class:on={picked} title="Pick a date" aria-label="Pick a date" onclick={pickDate}
          ><Icon name="calendar" size={13} />{picked && item.due_at ? shortDate(item.due_at, board.now) : 'Date'}</button
        >
        <input
          bind:this={dateEl}
          type="date"
          tabindex="-1"
          aria-label="Due date"
          value={dueDay}
          onchange={(e) => {
            const v = e.currentTarget.value;
            if (v) run(() => board.reschedule(item.id, v));
          }}
        />
      </span>
      {#if item.due_at}<button
          class="chip-clear"
          title="Clear due date"
          aria-label="Clear due date"
          onclick={() => run(() => board.reschedule(item.id, null))}><Icon name="x" size={13} /></button
        >{/if}
    </div>
    <div class="menu-label">Priority</div>
    <div class="menu-row chips prio-row">
      {#each PRIORITIES as p (p)}
        <button
          class="prio-flag-{p}"
          class:on={item.priority === p}
          title={priorityLong(p)}
          aria-label="Priority {priorityLong(p)}"
          onclick={() => run(() => board.setPriority(item.id, item.priority === p ? null : p))}
          ><Icon name="flag" fill /></button
        >
      {/each}
    </div>
    {#if !item.parent_id}
      <button
        role="menuitem"
        onclick={(e) => {
          e.stopPropagation();
          moving = true;
        }}><Icon name="folder" /> Move to…</button
      >
    {/if}
    <button role="menuitem" onclick={() => run(() => board.toggleSelect(item.id))}
      ><Icon name="checklist" /> {board.selected.has(item.id) ? 'Deselect' : 'Select'}</button
    >
    <button role="menuitem" onclick={() => run(() => copyText(item.title))}
      ><Icon name="link" /> Copy title</button
    >
    <button role="menuitem" onclick={() => run(async () => (await copyText(item.id)) && board.flash('Copied'))}
      ><Icon name="link" /> Copy ID</button
    >
    <!-- Each way off the board says what it does (lib/words.ts LEAVES); the toast repeats it. -->
    {#if confirmArchive}
      <button role="menuitem" class="danger leave-confirm" onclick={() => run(() => board.archive(item.id))}
        ><Icon name="archive" /><span>Put away? <small>{LEAVES.putAway}</small></span></button
      >
    {:else}
      <button
        role="menuitem"
        class="danger"
        title={LEAVES.putAway}
        onclick={(e) => {
          e.stopPropagation();
          confirmArchive = true;
        }}><Icon name="archive" /> Put away</button
      >
    {/if}
    <button role="menuitem" class="danger" title={LEAVES.trash} onclick={() => run(() => board.trash(item.id))}
      ><Icon name="trash" /> Move to trash</button
    >
    {/if}
  </div>
</Dropdown>

<style>
  .leave-confirm {
    align-items: flex-start;
    white-space: normal;
    max-width: 260px;
  }
  .leave-confirm small {
    display: block;
    font-weight: 400;
    opacity: 0.85;
  }
</style>
