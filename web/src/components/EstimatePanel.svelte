<script lang="ts">
  // An item's time estimate, editable in place. Self-contained so the task window can mount it
  // as is: props are just the item.
  import { formatEstimate, parseEstimate } from '../lib/format';
  import { board } from '../lib/store.svelte';
  import type { Item } from '../lib/types';
  import Icon from './Icon.svelte';

  let { item }: { item: Item } = $props();

  // The store's copy updates live (stream and optimistic writes); the prop may be a detail.
  const live = $derived(board.items[item.id] ?? item);

  let estimateText = $state('');
  let editingEstimate = $state(false);

  function saveEstimate() {
    if (!editingEstimate) return;
    editingEstimate = false;
    const t = estimateText.trim();
    const minutes = t ? parseEstimate(t) : null;
    if (t && minutes === null) return board.flash('Estimates look like 90, 45m, 1.5h, 1h 30m or 2d');
    if (minutes !== (live.estimate_minutes ?? null)) void board.setEstimate(live.id, minutes);
  }
</script>

<div class="estimate-panel" aria-label="Estimate">
  <div class="fp-row">
    <span class="fp-name muted" title="Time estimate"><Icon name="clock" size={16} /> Estimate</span>
    {#if editingEstimate}
      <!-- svelte-ignore a11y_autofocus -->
      <input
        class="field-text"
        aria-label="Time estimate"
        placeholder="e.g. 1h 30m"
        autofocus
        bind:value={estimateText}
        onblur={saveEstimate}
        onkeydown={(e) => {
          if (e.key === 'Enter') saveEstimate();
          if (e.key === 'Escape') {
            e.stopPropagation();
            editingEstimate = false;
          }
        }}
      />
    {:else}
      <button
        class="field-show"
        class:empty={!live.estimate_minutes}
        aria-label="Edit time estimate"
        onclick={() => {
          estimateText = formatEstimate(live.estimate_minutes);
          editingEstimate = true;
        }}>{formatEstimate(live.estimate_minutes) || '–'}</button
      >
    {/if}
  </div>
</div>
