<script lang="ts">
  // Assignees are accounts (AGENTS.md rule 2): people first, then agents (🤖). A searchable
  // popover; ticking toggles and saves at once (one PATCH of the whole set, by handle). The
  // popover stays open for several ticks in a row and closes on a click outside or Escape.
  import { board } from '../lib/store.svelte';
  import type { Assignee } from '../lib/types';
  import Avatars from './Avatars.svelte';

  let {
    assignees,
    onchange,
  }: { assignees: Assignee[]; onchange: (handles: string[]) => void } = $props();

  let open = $state(false);
  let q = $state('');
  let root: HTMLDivElement | undefined = $state();
  let trigger: HTMLButtonElement | undefined = $state();
  let search: HTMLInputElement | undefined = $state();

  function show() {
    open = true;
    // Straight to typing with a mouse; on a phone the keyboard would cover the list.
    if (matchMedia('(pointer: fine)').matches) queueMicrotask(() => search?.focus());
  }

  function hide() {
    open = false;
    q = '';
  }

  function outside(e: PointerEvent) {
    if (open && root && !root.contains(e.target as Node)) hide();
  }

  function onkey(e: KeyboardEvent) {
    if (!open || e.key !== 'Escape') return;
    // Close only the picker, not the task window behind it.
    e.stopPropagation();
    hide();
    trigger?.focus();
  }

  const candidates = $derived(
    board.pickable
      .filter((a) => {
        const s = q.trim().toLowerCase();
        return !s || a.display_name.toLowerCase().includes(s) || a.handle.includes(s);
      })
      .sort(
        (a, b) =>
          Number(a.kind === 'agent') - Number(b.kind === 'agent') ||
          a.display_name.localeCompare(b.display_name),
      ),
  );

  function toggle(handle: string) {
    const have = assignees.map((a) => a.handle);
    onchange(have.includes(handle) ? have.filter((h) => h !== handle) : [...have, handle]);
  }
</script>

<svelte:document onpointerdown={outside} onkeydown={onkey} />

<div class="assignee-picker" bind:this={root}>
  <div class="inline wrap">
    {#each assignees as a (a.id)}
      <span class="chip">
        <Avatars people={[a]} />
        {a.display_name}
        <button class="x" aria-label="Unassign {a.handle}" onclick={() => toggle(a.handle)}>×</button>
      </span>
    {/each}
    <button class="link small" bind:this={trigger} aria-expanded={open} onclick={() => (open ? hide() : show())}
      >+ assign</button
    >
  </div>
  {#if open}
    <div class="picker-pop" role="group" aria-label="Assign">
      <input aria-label="Find someone" placeholder="Find a person or agent" bind:this={search} bind:value={q} />
      <ul class="picker-list">
        {#each candidates as a (a.id)}
          {@const on = assignees.some((x) => x.id === a.id)}
          <li>
            <label>
              <input type="checkbox" checked={on} onchange={() => toggle(a.handle)} />
              <Avatars people={[a]} />
              <span class="ap-name">{a.display_name}</span>
              <span class="ap-meta muted small">@{a.handle}{a.kind === 'agent' ? ' · agent' : ''}</span>
            </label>
          </li>
        {:else}
          <li class="muted small">Nobody matches.</li>
        {/each}
      </ul>
    </div>
  {/if}
</div>
