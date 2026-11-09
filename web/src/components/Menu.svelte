<script lang="ts" module>
  export interface MenuEntry {
    label: string;
    onselect: () => void;
    danger?: boolean;
    disabled?: boolean;
    /** Tooltip: what the action does (lib/words.ts LEAVES for the ways off the board). */
    title?: string;
  }
</script>

<script lang="ts">
  // A small ⋯ menu: a button and a popover of actions. Closes on pick, outside click or Esc.
  let {
    label,
    entries,
    open = $bindable(false),
  }: { label: string; entries: MenuEntry[]; open?: boolean } = $props();
  let root: HTMLElement | undefined = $state();

  function onWindowClick(e: MouseEvent) {
    if (open && root && !root.contains(e.target as Node)) open = false;
  }

  function onKey(e: KeyboardEvent) {
    if (open && e.key === 'Escape') {
      e.stopPropagation();
      open = false;
    }
  }
</script>

<svelte:window onclick={onWindowClick} onkeydowncapture={onKey} />

<span class="menu" bind:this={root}>
  <button
    class="menu-button"
    aria-label={label}
    aria-haspopup="menu"
    aria-expanded={open}
    onclick={(e) => {
      e.preventDefault();
      e.stopPropagation();
      open = !open;
    }}>⋯</button
  >
  {#if open}
    <div class="menu-pop" role="menu">
      {#each entries as entry (entry.label)}
        <button
          role="menuitem"
          class:danger={entry.danger}
          disabled={entry.disabled}
          title={entry.title}
          onclick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            open = false;
            entry.onselect();
          }}>{entry.label}</button
        >
      {/each}
    </div>
  {/if}
</span>
