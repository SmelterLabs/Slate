<script lang="ts">
  import { safeColor } from '../lib/mdcore';
  import type { Section } from '../lib/types';
  import type { AlsoShowSetting } from '../lib/views';

  let { options, setting, saving = false, onchange }: {
    options: { section: Section; count: number }[];
    setting: AlsoShowSetting;
    saving?: boolean;
    onchange: (setting: AlsoShowSetting) => void;
  } = $props();

  function toggle(id: string) {
    const sections = setting.sections.includes(id)
      ? setting.sections.filter((section) => section !== id)
      : [...setting.sections, id];
    if (!saving) onchange({ ...setting, sections });
  }
  const anyOn = $derived(setting.sections.length > 0);
</script>

{#if options.length}
  <div class="also-show" role="group" aria-label="Also show">
    <span class="label">Also show cards from</span>
    {#each options as { section, count } (section.id)}
      {@const pressed = setting.sections.includes(section.id)}
      <button
        type="button"
        aria-pressed={pressed}
        aria-disabled={saving || undefined}
        disabled={!pressed && setting.sections.length >= 100}
        class="chip"
        style:--chip={safeColor(section.color, 'var(--accent)')}
        title={pressed ? `Showing ${section.name} here. Click to hide it.` : `Show ${section.name} cards on this board (${count} open)`}
        onclick={() => toggle(section.id)}
      >
        {#if pressed}<span class="tick" aria-hidden="true">✓</span>{:else}<span class="dot" aria-hidden="true"></span>{/if}
        {section.name} <span class="count">{count}</span>
      </button>
    {/each}
    {#if setting.sections.length}
      <div class="audience" role="group" aria-label="Whose cards">
        <button
          type="button"
          aria-pressed={!setting.everyone}
          aria-disabled={saving || undefined}
          onclick={() => !saving && setting.everyone && onchange({ ...setting, everyone: false })}
        >Mine</button>
        <button
          type="button"
          aria-pressed={setting.everyone}
          aria-disabled={saving || undefined}
          onclick={() => !saving && !setting.everyone && onchange({ ...setting, everyone: true })}
        >Everyone</button>
      </div>
      <button type="button" class="clear" aria-disabled={saving || undefined} onclick={() => !saving && onchange({ ...setting, sections: [] })}
        >Hide all</button
      >
    {/if}
  </div>
{/if}

<style>
  .also-show {
    display: flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 0.45rem;
    min-width: 0;
    overflow-x: auto;
    padding: 0.4rem 0.6rem;
    border: 1px solid var(--line);
    border-radius: 0.55rem;
    background: var(--panel);
    white-space: nowrap;
    font-size: 0.85rem;
  }
  .label {
    margin-inline-end: 0.2rem;
    font-weight: 600;
    color: var(--text);
  }
  .count {
    color: var(--muted);
  }
  button {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 0.35rem;
    min-height: 1.9rem;
    padding: 0.15rem 0.6rem;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: transparent;
    color: var(--text);
    cursor: pointer;
  }
  /* Off: an outlined chip with the section's dot. On: filled with the section's colour. */
  .chip[aria-pressed='true'] {
    border-color: var(--chip);
    background: color-mix(in srgb, var(--chip) 24%, var(--panel));
    font-weight: 600;
  }
  .chip[aria-pressed='true'] .count {
    color: var(--text);
  }
  .tick {
    color: var(--chip);
    font-weight: 700;
  }
  .audience button[aria-pressed='true'] {
    border-color: var(--accent);
    background: var(--accent-soft);
    font-weight: 600;
  }
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  button:disabled {
    cursor: default;
    opacity: 0.6;
  }
  .dot {
    width: 0.6rem;
    height: 0.6rem;
    border-radius: 50%;
    background: var(--chip);
  }
  .audience {
    display: flex;
    gap: 0.2rem;
    padding-inline-start: 0.5rem;
    border-inline-start: 1px solid var(--line);
  }
  .clear {
    border-color: transparent;
    color: var(--muted);
    text-decoration: underline;
  }
</style>
