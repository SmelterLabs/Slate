<script lang="ts">
  // Neutral "Stalled" mark with Run now when an agent stopped without finishing.
  import type { Item } from '../lib/types';
  import { STALLED } from '../lib/words';
  import RunNow from './RunNow.svelte';

  let { item, full = false }: { item: Item; full?: boolean } = $props();
</script>

<span class="stalled-badge" class:full data-stalled title="An agent stopped without finishing this task">
  <span class="stalled-row">
    <span class="dot" aria-hidden="true"></span>
    <span class="what">{STALLED}</span>
  </span>
  <span class="again" data-stalled-run><RunNow {item} /></span>
</span>

<style>
  .stalled-badge {
    --tone: var(--muted);
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 100%;
    min-width: 0;
    box-sizing: border-box;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--tone) 45%, transparent);
    background: color-mix(in srgb, var(--tone) 12%, transparent);
    color: var(--text);
    font-size: 12px;
    line-height: 1.3;
  }
  .stalled-badge.full {
    width: 100%;
    justify-content: space-between;
    border-radius: var(--radius);
    padding: 8px 12px;
    font-size: 13px;
  }
  .stalled-row {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    white-space: nowrap;
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--tone);
    flex: none;
  }
  .what {
    font-weight: 600;
  }
</style>
