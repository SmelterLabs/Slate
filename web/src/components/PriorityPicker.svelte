<script lang="ts">
  // The priority pill as a button: four flags and clear.
  import type { Snippet } from 'svelte';
  import { priorityLong } from '../lib/priority';
  import { board } from '../lib/store.svelte';
  import type { Item, Priority } from '../lib/types';
  import { PRIORITIES } from '../lib/views';
  import Dropdown from './Dropdown.svelte';
  import Icon from './Icon.svelte';

  let { item, children, cls = 'pill pill-btn' }: { item: Item; children: Snippet; cls?: string } = $props();

  let open = $state(false);

  function pick(p: Priority | null) {
    open = false;
    void board.setPriority(item.id, p);
  }
</script>

<Dropdown float title="Priority" {cls} bind:open label={children}>
  <div class="menu">
    {#each PRIORITIES as p (p)}
      <button class="prio-flag-{p}" class:on={item.priority === p} onclick={() => pick(p)}
        ><Icon name="flag" fill /> {priorityLong(p)}</button
      >
    {/each}
    {#if item.priority}<button onclick={() => pick(null)}><Icon name="x" /> Clear</button>{/if}
  </div>
</Dropdown>
