<script lang="ts">
  import type { Assignee } from '../lib/types';
  import Icon from './Icon.svelte';

  let { people }: { people: Assignee[] } = $props();

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
      <span class="avatar" class:agent={a.kind === 'agent'} title="{a.display_name} (@{a.handle})">
        {#if a.kind === 'agent'}<Icon name="robot" size={13} />{:else}{initials(a)}{/if}
      </span>
    {/each}
  </span>
{/if}
