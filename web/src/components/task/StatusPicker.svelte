<script lang="ts">
  import { LEAVES } from '../../lib/words';
  // ClickUp's status button: a colored pill naming the status, a menu of the list's statuses,
  // an arrow to step to the next one, and a check to complete.
  import { board } from '../../lib/store.svelte';
  import type { ItemDetail, Status } from '../../lib/types';
  import { statusColor as colorOf } from '../../lib/activity';
  import Icon from './Icon.svelte';

  let { item }: { item: ItemDetail } = $props();

  let open = $state(false);
  let root = $state<HTMLElement>();
  const list = $derived(board.lists[item.list_id]);
  const statuses = $derived([...(list?.statuses ?? [])].sort((a, b) => (a.position ?? 0) - (b.position ?? 0)));
  const current = $derived(statuses.find((s) => s.id === item.status?.id) ?? item.status);
  const next = $derived.by(() => {
    const i = statuses.findIndex((s) => s.id === current?.id);
    return i >= 0 && i < statuses.length - 1 ? statuses[i + 1] : undefined;
  });
  const done = $derived(current?.kind === 'done');
  const canComplete = $derived(statuses.some((s) => s.kind === 'done'));

  function pick(s: Status) {
    open = false;
    if (s.id !== current?.id) void board.moveToStatus(item.id, s).then(() => board.refreshDetail());
  }

  function outside(e: MouseEvent) {
    if (open && root && !root.contains(e.target as Node)) open = false;
  }
</script>

<svelte:document onmousedown={outside} />

<div class="status" bind:this={root}>
  <div class="pill" style="--c: {colorOf(current)}">
    <button
      class="name"
      aria-label="Status: {current?.name ?? 'none'}"
      aria-haspopup="listbox"
      aria-expanded={open}
      onclick={() => (open = !open)}>{current?.name ?? 'No status'}</button
    >
    {#if next}
      <button class="step" title="Move to {next.name}" aria-label="Move to {next.name}" onclick={() => pick(next)}
        ><Icon name="chevron" size={14} /></button
      >
    {/if}
  </div>
  {#if canComplete && !item.archived_at}
    <button
      class="complete"
      class:on={done}
      aria-pressed={done}
      title={done ? 'Reopen' : `Mark complete. ${LEAVES.complete}`}
      aria-label={done ? 'Reopen' : 'Mark complete'}
      onclick={() => board.toggleComplete(item.id).then(() => board.refreshDetail())}
      ><Icon name="check" size={15} /></button
    >
  {/if}
  {#if open}
    <ul class="menu" role="listbox" aria-label="Statuses">
      {#each statuses as s (s.id)}
        <li role="option" aria-selected={s.id === current?.id}>
          <button onclick={() => pick(s)}>
            <span class="dot" style="background: {colorOf(s)}"></span>
            {s.name}
            {#if s.id === current?.id}<span class="tick"><Icon name="check" size={14} /></span>{/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .status {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .pill {
    display: inline-flex;
    border-radius: 6px;
    background: var(--c);
    color: #fff;
    overflow: hidden;
  }
  .pill button {
    background: none;
    border: 0;
    color: inherit;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    padding: 5px 10px;
  }
  .pill .step {
    padding: 5px 6px;
    border-left: 1px solid rgb(255 255 255 / 0.35);
    display: inline-flex;
    align-items: center;
  }
  .pill button:hover {
    background: rgb(0 0 0 / 0.12);
  }
  .complete {
    width: 28px;
    height: 28px;
    border-radius: 6px;
    border: 1px solid var(--line);
    background: transparent;
    color: var(--muted);
    display: inline-grid;
    place-items: center;
    padding: 0;
  }
  .complete:hover {
    color: var(--kind-done);
    border-color: var(--kind-done);
  }
  .complete.on {
    background: var(--kind-done);
    border-color: var(--kind-done);
    color: #fff;
  }
  .menu {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    z-index: 30;
    min-width: 200px;
    margin: 0;
    padding: 4px;
    list-style: none;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgb(0 0 0 / 0.25);
  }
  .menu button {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border: 0;
    border-radius: 6px;
    background: none;
    text-align: left;
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }
  .menu button:hover {
    background: var(--card-hover);
  }
  .dot {
    width: 10px;
    height: 10px;
    border-radius: 3px;
    flex: none;
  }
  .tick {
    margin-left: auto;
    color: var(--accent);
  }
</style>
