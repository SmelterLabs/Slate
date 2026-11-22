<script lang="ts">
  // Priority flags: Urgent, High, Normal, Low, or none.
  import type { Snippet } from 'svelte';
  import { priorityLong } from '../lib/priority';
  import type { Priority } from '../lib/types';
  import { PRIORITIES } from '../lib/views';
  import Dropdown from './Dropdown.svelte';
  import Icon from './Icon.svelte';

  let { value, onchange, children }: {
    value: Priority | null;
    onchange: (p: Priority | null) => void;
    children?: Snippet;
  } = $props();

  let open = $state(false);
  let root = $state<HTMLElement>();
  const current = $derived(value ? priorityLong(value) : null);

  function pick(p: Priority | null) {
    open = false;
    if (p !== value) onchange(p);
  }
</script>

<svelte:document onmousedown={(e) => open && root && !root.contains(e.target as Node) && (open = false)} />

{#if children}
  <!-- On a card: the board's own menu, filled flags in the flag colours. -->
  <Dropdown float title="Priority" cls="pill pill-btn" bind:open label={children}>
    <div class="menu">
      {#each PRIORITIES as p (p)}
        <button class="prio-flag-{p}" class:on={value === p} onclick={() => pick(p)}
          ><Icon name="flag" fill /> {priorityLong(p)}</button
        >
      {/each}
      {#if value}<button onclick={() => pick(null)}><Icon name="x" /> Clear</button>{/if}
    </div>
  </Dropdown>
{:else}
  <!-- In the task window: a field that opens its own list. -->
  <div class="prio" bind:this={root}>
    <button class="value" class:empty={!current} aria-label="Priority: {current ?? 'none'}" aria-expanded={open} onclick={() => (open = !open)}>
      <span class="flag" style="color: var(--prio-{value ?? 'low'})"><Icon name="flag" size={15} /></span>
      {current ?? 'Empty'}
    </button>
    {#if open}
      <ul class="menu prio-list" role="listbox" aria-label="Priorities">
        {#each PRIORITIES as p (p)}
          <li role="option" aria-selected={p === value}>
            <button onclick={() => pick(p)}>
              <span class="flag" style="color: var(--prio-{p})"><Icon name="flag" size={15} /></span>
              {priorityLong(p)}
            </button>
          </li>
        {/each}
        {#if value}
          <li role="option" aria-selected="false"><button class="muted" onclick={() => pick(null)}>Clear</button></li>
        {/if}
      </ul>
    {/if}
  </div>
{/if}

<style>
  .prio {
    position: relative;
  }
  .value {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 0;
    background: none;
    padding: 4px 6px;
    border-radius: 6px;
    font-size: 13.5px;
  }
  .value:hover {
    background: var(--card-hover);
  }
  .value.empty {
    color: var(--muted);
  }
  .flag {
    display: inline-flex;
  }
  .prio-list {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    z-index: 30;
    min-width: 160px;
    margin: 0;
    padding: 4px;
    list-style: none;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    box-shadow: 0 8px 24px rgb(0 0 0 / 0.25);
  }
  .prio-list button {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border: 0;
    border-radius: 6px;
    background: none;
    text-align: left;
    font-size: 13.5px;
  }
  .prio-list button:hover {
    background: var(--card-hover);
  }
</style>
