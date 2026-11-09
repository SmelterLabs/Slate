<script lang="ts">
  // "Move to…": a search box over every live list, grouped by section. Arrow keys move the
  // highlight and Enter picks it; the card's current list is marked and cannot be picked.
  import { board } from '../lib/store.svelte';
  import { flatChoices, moveGroups } from '../lib/move';
  import Icon from './Icon.svelte';

  let {
    current = null,
    onpick,
    autofocus = true,
  }: { current?: string | null; onpick: (listId: string) => void; autofocus?: boolean } = $props();

  let q = $state('');
  let at = $state(0);
  let search: HTMLInputElement | undefined = $state();

  const groups = $derived(moveGroups(board.sections, Object.values(board.lists), q));
  const choices = $derived(flatChoices(groups).filter((l) => l.id !== current));
  const hot = $derived(choices[Math.min(at, choices.length - 1)]?.id);

  $effect(() => {
    // Straight to typing with a mouse; on a phone the keyboard would cover the list.
    if (autofocus && search && matchMedia('(pointer: fine)').matches) search.focus();
  });

  function onkey(e: KeyboardEvent) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const n = choices.length;
      if (n) at = (Math.min(at, n - 1) + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
    } else if (e.key === 'Enter' && hot) {
      e.preventDefault();
      onpick(hot);
    }
  }
</script>

<div class="move-picker" role="group" aria-label="Move to list">
  <input
    class="move-search"
    aria-label="Find a list"
    placeholder="Find a list or section"
    bind:this={search}
    bind:value={q}
    oninput={() => (at = 0)}
    onkeydown={onkey}
  />
  <div class="move-list bb-scroll">
    {#each groups as g (g.section.id)}
      <div class="menu-label">{g.section.name}</div>
      {#each g.lists as l (l.id)}
        {#if l.id === current}
          <button role="menuitem" class="move-current" disabled aria-current="true"
            ><Icon name="list" /> {l.name} <span class="muted small">here now</span></button
          >
        {:else}
          <button role="menuitem" class:hot={l.id === hot} onclick={() => onpick(l.id)}
            ><Icon name="list" /> {l.name}</button
          >
        {/if}
      {/each}
    {:else}
      <div class="muted small move-none">No list matches.</div>
    {/each}
  </div>
</div>
