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
</script>

{#if options.length}
  <div class="also-show" role="group" aria-label="Also show">
    <span class="label">Also show</span>
    {#each options as { section, count } (section.id)}
      {@const pressed = setting.sections.includes(section.id)}
      <button
        type="button"
        aria-pressed={pressed}
        aria-disabled={saving || undefined}
        disabled={!pressed && setting.sections.length >= 100}
        onclick={() => toggle(section.id)}
      >
        <span class="dot" style:background={safeColor(section.color, 'var(--muted)')} aria-hidden="true"></span>
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
    {/if}
  </div>
{/if}

<style>
  .also-show {
    display: flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 0.4rem;
    min-width: 0;
    overflow-x: auto;
    padding: 0.25rem 0.125rem;
    white-space: nowrap;
    font-size: 0.8rem;
  }
  .label, .count {
    color: var(--muted);
  }
  .label {
    margin-inline-end: 0.2rem;
  }
  button {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 0.35rem;
    min-height: 1.9rem;
    padding: 0.15rem 0.55rem;
    border: 1px solid var(--line);
    border-radius: 0.45rem;
    background: var(--panel);
    color: var(--text);
    cursor: pointer;
  }
  button[aria-pressed='true'] {
    border-color: var(--accent);
    background: var(--accent-soft);
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
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
  }
  .audience {
    display: flex;
    gap: 0.2rem;
    padding-inline-start: 0.45rem;
    border-inline-start: 1px solid var(--line);
  }
</style>
