<script lang="ts">
  // Stands in for an on-demand page (lib/lazy.svelte.ts) while its chunk loads: nothing for the
  // first moment, so a quick load doesn't flash, then a quiet "Loading…". If the load fails
  // (offline, or a deploy replaced the file) it says so and offers to try again.
  import type { LazyPage } from '../lib/lazy.svelte';

  let { page, dialog = false }: { page: LazyPage; dialog?: boolean } = $props();
  let slow = $state(false);

  $effect(() => {
    void page.load();
    const t = setTimeout(() => (slow = true), 300);
    return () => clearTimeout(t);
  });
</script>

{#if page.failed}
  <div class="page-load" class:dialog role="alert">
    <p>This page didn't load. {navigator.onLine ? 'The board may have been updated.' : "You're offline."}</p>
    <div class="page-load-actions">
      <button class="primary" onclick={() => void page.load()}>Try again</button>
      <button class="secondary" onclick={() => location.reload()}>Reload Slate</button>
    </div>
  </div>
{:else if slow && !dialog}
  <div class="page-load" role="status" aria-live="polite"><span class="muted">Loading…</span></div>
{/if}

<style>
  .page-load {
    display: grid;
    justify-items: center;
    gap: 10px;
    padding: 48px 16px;
    text-align: center;
  }
  .page-load.dialog {
    position: fixed;
    left: 50%;
    top: 20%;
    transform: translateX(-50%);
    z-index: 60;
    padding: 16px 20px;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--shadow-pop);
  }
  .page-load p {
    margin: 0;
  }
  .page-load-actions {
    display: flex;
    gap: 8px;
  }
</style>
