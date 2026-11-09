<script lang="ts">
  // A tag as every view shows it: its own colour, else a stable pick from the palette, so the
  // same tag looks the same on the board, list, table, task window, My Work and the tag manager.
  import type { Snippet } from 'svelte';
  import { tagColor } from '../lib/cards';
  import { board } from '../lib/store.svelte';

  let { name, children }: { name: string; children?: Snippet } = $props();
  const c = $derived(tagColor(name, board.tagMap));
</script>

<span class="tag-pill" style:background={c.bg} style:color={c.fg}>{name}{@render children?.()}</span>
