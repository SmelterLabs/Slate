<script lang="ts">
  // A centred icon, a line and a hint for places with nothing to show yet. Optional actions
  // go in the children snippet (buttons), so each caller keeps its own wiring.
  import type { Snippet } from 'svelte';
  import Icon, { type IconName } from './Icon.svelte';

  let {
    icon = 'inbox',
    title,
    hint = '',
    children,
  }: { icon?: IconName; title: string; hint?: string; children?: Snippet } = $props();
</script>

<div class="empty-state">
  <span class="empty-art" aria-hidden="true"><Icon name={icon} size={26} stroke={1.6} /></span>
  <h3>{title}</h3>
  {#if hint}<p>{hint}</p>{/if}
  {#if children}<div class="empty-actions">{@render children()}</div>{/if}
</div>
