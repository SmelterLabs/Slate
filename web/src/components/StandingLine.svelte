<script lang="ts">
  // Where a card stands after an agent's report or finish, and whose move it is.
  import { board } from '../lib/store.svelte';
  import type { ItemStanding } from '../lib/types';

  let { standing, full = false }: { standing: ItemStanding; full?: boolean } = $props();

  const mine = $derived(!!standing.move_id && standing.move_id === board.me?.account.id);
  const moveLabel = $derived(
    !standing.move_id ? null : mine ? 'Your move' : (board.accountName(standing.move_id) ?? null),
  );
</script>

<span class="standing" class:full data-standing>
  <span class="standing-line" data-standing-line title={standing.line}>{standing.line}</span>
  {#if moveLabel}
    <span class="standing-move" class:mine data-standing-move>{moveLabel}</span>
  {/if}
</span>

<style>
  .standing {
    display: flex;
    align-items: baseline;
    gap: 6px;
    min-width: 0;
    max-width: 100%;
    font-size: 12px;
    line-height: 1.35;
    color: var(--muted);
  }
  .standing-line {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .standing-move {
    flex: none;
    font-size: 11px;
    font-weight: 600;
    padding: 0 6px;
    border-radius: 999px;
    border: 1px solid var(--line);
    background: color-mix(in srgb, var(--text) 5%, transparent);
    color: var(--muted);
    white-space: nowrap;
  }
  .standing-move.mine {
    border-color: color-mix(in srgb, var(--accent) 45%, transparent);
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    color: var(--accent);
  }
  .standing.full {
    align-items: center;
    justify-content: space-between;
    padding: 7px 10px;
    margin-bottom: 10px;
    border-radius: var(--radius);
    border: 1px solid var(--line);
    background: color-mix(in srgb, var(--text) 3%, transparent);
  }
  .standing.full .standing-line {
    white-space: normal;
    font-size: 13px;
    color: var(--text);
  }
</style>
