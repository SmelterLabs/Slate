<script lang="ts">
  import type { Assignee } from '../lib/types';
  import Icon from './Icon.svelte';

  // `named` writes an agent's name beside its robot, where there is room for it (a board
  // card): several agents on one board are told apart without hovering.
  let { people, named = false }: { people: Assignee[]; named?: boolean } = $props();

  function initials(a: Assignee): string {
    return a.display_name
      .split(/\s+/)
      .map((w) => w[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }
</script>

{#if people.length}
  <span class="avatars">
    {#each people as a (a.id)}
      <span
        class="avatar"
        class:agent={a.kind === 'agent'}
        class:named={named && a.kind === 'agent'}
        title="{a.display_name} (@{a.handle})"
      >
        {#if a.kind === 'agent'}<Icon name="robot" size={13} />{#if named}<span class="avatar-name">{a.display_name}</span
            >{/if}{:else}{initials(a)}{/if}
      </span>
    {/each}
  </span>
{/if}
