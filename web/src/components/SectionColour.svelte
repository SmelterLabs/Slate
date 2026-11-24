<script lang="ts">
  // "Colour…" from a section's ⋯ menu: twelve swatches and No colour, under the section's
  // heading in the nav (inline, so no floating panel to clip). One click saves and closes;
  // Escape closes. Tab moves between swatches, Enter picks.
  import { safeColor } from '../lib/mdcore';
  import { board } from '../lib/store.svelte';
  import type { Section } from '../lib/types';

  let { section, onclose }: { section: Section; onclose: () => void } = $props();

  // Mid-tone hues that read as a dot and as a soft wash on the dark and the light theme.
  const SWATCHES = [
    ['Red', '#e5484d'],
    ['Orange', '#f76b15'],
    ['Gold', '#d69e2e'],
    ['Lime', '#65a30d'],
    ['Green', '#30a46c'],
    ['Teal', '#12a594'],
    ['Cyan', '#00a2c7'],
    ['Blue', '#0090ff'],
    ['Indigo', '#3e63dd'],
    ['Violet', '#8e4ec6'],
    ['Pink', '#d6409f'],
    ['Brown', '#ad7f58'],
  ];
  const current = $derived(section.color?.toLowerCase() || null);

  function pick(color: string | null) {
    onclose();
    void board.updateSection(section.id, { color });
  }

  function escape(e: KeyboardEvent) {
    if (e.key !== 'Escape') return;
    e.stopPropagation();
    onclose();
  }

  function start(panel: HTMLElement) {
    panel.querySelector<HTMLButtonElement>('[aria-pressed="true"]')?.focus();
  }
</script>

<div
  class="colour-pick"
  role="group"
  aria-label="Colour for {section.name}"
  use:start
>
  <div class="colour-grid">
    {#each SWATCHES as [name, hex] (hex)}
      <button
        class="swatch"
        class:on={current === hex}
        style:background={safeColor(hex)}
        data-swatch={hex}
        aria-label={name}
        title={name}
        aria-pressed={current === hex}
        onclick={() => pick(hex)}
        onkeydown={escape}
      ></button>
    {/each}
  </div>
  <button class="link small" data-no-colour aria-pressed={!current} onclick={() => pick(null)} onkeydown={escape}>No colour</button>
</div>

<style>
  .colour-pick {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    margin: 2px 10px 6px 28px;
    padding: 8px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: var(--card);
  }
  .colour-grid {
    display: grid;
    grid-template-columns: repeat(6, 18px);
    gap: 7px;
  }
  .colour-grid button {
    padding: 0;
    cursor: pointer;
  }
  [data-no-colour][aria-pressed='true'] {
    font-weight: 600;
  }
</style>
