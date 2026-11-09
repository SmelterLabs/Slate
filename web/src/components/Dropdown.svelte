<script lang="ts">
  // A button with a panel under it. Closes on a click outside or Escape. Clicks inside the
  // panel do not reach the card or row it sits in. `float` pins the panel to the window instead
  // of the button's box (moved under <body>), so no column, table cell or task window can clip
  // or cover it; it flips up near the bottom.
  import type { Snippet } from 'svelte';

  let {
    label,
    children,
    title = '',
    id = undefined,
    cls = '',
    align = 'left',
    float = false,
    open = $bindable(false),
  }: {
    label: Snippet;
    children: Snippet;
    title?: string;
    id?: string;
    cls?: string;
    align?: 'left' | 'right';
    float?: boolean;
    open?: boolean;
  } = $props();

  let root = $state<HTMLElement>();
  let panel = $state<HTMLElement>();
  let pos = $state<{ top: number; left: number } | null>(null);

  $effect(() => {
    // Phones show every panel as a bottom sheet (app.css), so only wider screens float.
    if (!float || !open || !root || !panel || window.matchMedia('(max-width: 760px)').matches) {
      pos = null;
      return;
    }
    const b = root.getBoundingClientRect();
    const p = panel.getBoundingClientRect();
    const below = b.bottom + 4;
    const top = below + p.height > window.innerHeight - 8 ? Math.max(8, b.top - 4 - p.height) : below;
    const want = align === 'right' ? b.right - p.width : b.left;
    pos = { top, left: Math.max(8, Math.min(want, window.innerWidth - p.width - 8)) };
  });

  function outside(e: MouseEvent) {
    const t = e.target as Node;
    if (open && root && !root.contains(t) && !panel?.contains(t)) open = false;
  }

  // A floating panel lives under <body>: a parent that is sticky (table cells), transformed or
  // translated (the task window), or clips its overflow would otherwise trap it, so it drew
  // behind later rows or off the window. Out there its clicks no longer pass through the card.
  function portal(node: HTMLElement, on: boolean) {
    if (!on) return;
    document.body.appendChild(node);
    return { destroy: () => node.remove() };
  }
  function key(e: KeyboardEvent) {
    if (open && e.key === 'Escape') {
      e.stopPropagation();
      open = false;
    }
  }
</script>

<svelte:document onmousedown={outside} />
<!-- Capture: Escape closes this panel only, before the board-wide Escape (App) clears a selection. -->
<svelte:window onkeydowncapture={key} />

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="dd" bind:this={root} onclick={(e) => e.stopPropagation()}>
  <button
    type="button"
    {id}
    class="dd-btn {cls}"
    class:on={open}
    aria-expanded={open}
    aria-label={title || undefined}
    {title}
    onclick={() => (open = !open)}>{@render label()}</button
  >
  {#if open}
    <div
      class="dd-panel"
      class:right={align === 'right'}
      class:floating={float}
      bind:this={panel}
      use:portal={float}
      style:top={pos ? `${pos.top}px` : undefined}
      style:left={pos ? `${pos.left}px` : undefined}
      style:visibility={float && open && !pos && !window.matchMedia('(max-width: 760px)').matches
        ? 'hidden'
        : undefined}
    >
      {@render children()}
    </div>
  {/if}
</div>
