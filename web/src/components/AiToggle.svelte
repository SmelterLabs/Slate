<script lang="ts">
  // The sparkle beside a new-task box: on, the configured model fills in the task from the
  // words (POST /items/ai); off, the box is plain quick add. One switch for every box here.
  import { board } from '../lib/store.svelte';
  import Icon from './Icon.svelte';

</script>

{#if board.aiReady}
  <button
    type="button"
    class="ai-toggle"
    class:on={board.aiAddOn}
    aria-pressed={board.aiAddOn}
    title={board.aiAddOn
      ? 'AI add is on: describe the task and AI fills in the details. Click for plain quick add.'
      : 'AI add is off. Click to let AI fill in new tasks.'}
    onclick={() => board.setAiAdd(!board.aiAddOn)}><Icon name="sparkle" /><span class="ai-label">AI</span></button
  >
{/if}

<style>
  .ai-toggle {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    padding: 2px 7px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font-size: 11px;
    cursor: pointer;
    flex: none;
  }
  .ai-toggle.on {
    color: var(--accent-ink);
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .ai-label {
    font-weight: 600;
  }
</style>
