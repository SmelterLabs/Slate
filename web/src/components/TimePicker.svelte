<script lang="ts">
  // Fast time picker: type any natural time ("930a", "2:30pm", "14:30", "noon") OR click a
  // quick-pick chip or a slot from the scrollable list. Blank clears back to date-only.
  import { tick } from 'svelte';
  import { TIME_PRESETS, filterTimeSlots, parseTimeInput, timeLabel } from '../lib/when';
  import Icon from './Icon.svelte';

  let {
    label,
    value,
    onchange,
    compact = false,
  }: {
    label: string;
    /** Canonical 24-hour "HH:MM", or "" when no time is set. */
    value: string;
    onchange: (hhmm: string) => void;
    compact?: boolean;
  } = $props();

  let draft = $state('');
  let editing = $state(false);
  let listOpen = $state(false);
  let active = $state(0);
  let inputEl = $state<HTMLInputElement | null>(null);
  let listEl = $state<HTMLDivElement | null>(null);
  const uid = $props.id();
  const listId = `time-list-${uid}`;

  const display = $derived(value ? timeLabel(value) : '');
  const shown = $derived(editing ? draft : display);
  const query = $derived(editing ? draft : '');
  const slots = $derived(filterTimeSlots(query));

  $effect(() => {
    if (active >= slots.length) active = Math.max(0, slots.length - 1);
  });

  async function scrollToTarget() {
    await tick();
    if (!listEl) return;
    const target =
      listEl.querySelector<HTMLElement>('[aria-selected="true"]') ??
      listEl.querySelector<HTMLElement>('[data-hhmm="09:00"]') ??
      listEl.querySelector<HTMLElement>('.time-opt');
    target?.scrollIntoView({ block: 'center' });
  }

  function openList(selectInput = false) {
    if (!editing) {
      draft = display;
      editing = true;
    }
    listOpen = true;
    const idx = value ? slots.findIndex((s) => s.hhmm === value) : -1;
    active = idx >= 0 ? idx : 0;
    if (selectInput) void tick().then(() => inputEl?.select());
    void scrollToTarget();
  }

  function pick(hhmm: string) {
    editing = false;
    draft = hhmm ? timeLabel(hhmm) : '';
    listOpen = false;
    if (hhmm !== value) onchange(hhmm);
  }

  function commitText(raw: string) {
    const trimmed = raw.trim();
    if (!trimmed) {
      pick('');
      return;
    }
    const parsed = parseTimeInput(trimmed);
    if (parsed) {
      pick(parsed);
      return;
    }
    // Invalid input: revert to the last valid value cleanly.
    editing = false;
    draft = display;
    listOpen = false;
  }

  function onInput(e: Event) {
    draft = (e.currentTarget as HTMLInputElement).value;
    editing = true;
    listOpen = true;
    active = 0;
  }

  function onChange(e: Event) {
    // Supports programmatic fill + change dispatch as well as keyboard/blur commit.
    commitText((e.currentTarget as HTMLInputElement).value);
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (!listOpen) {
        openList();
        return;
      }
      if (slots.length) {
        active = (active + 1) % slots.length;
        listEl?.children[active]?.scrollIntoView({ block: 'nearest' });
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (!listOpen) {
        openList();
        return;
      }
      if (slots.length) {
        active = (active - 1 + slots.length) % slots.length;
        listEl?.children[active]?.scrollIntoView({ block: 'nearest' });
      }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      const trimmed = draft.trim();
      if (!trimmed) {
        pick('');
      } else if (listOpen && slots[active] && trimmed !== display) {
        pick(slots[active].hhmm);
      } else {
        commitText(draft);
      }
    } else if (e.key === 'Escape') {
      if (listOpen || editing) {
        e.preventDefault();
        e.stopPropagation();
        editing = false;
        draft = display;
        listOpen = false;
      }
    }
  }

  function onBlur() {
    if (editing) commitText(draft);
    else listOpen = false;
  }
</script>

<div class="timepick" class:compact>
  <div class="time-input-wrap">
    <span class="time-ic" aria-hidden="true"><Icon name="clock" size={13} /></span>
    <input
      bind:this={inputEl}
      class="time-input"
      type="text"
      role="combobox"
      autocomplete="off"
      aria-label="{label} time"
      aria-controls={listId}
      aria-expanded={listOpen}
      aria-autocomplete="list"
      placeholder="Add time"
      value={shown}
      onfocus={() => openList(true)}
      onclick={() => openList(false)}
      oninput={onInput}
      onchange={onChange}
      onkeydown={onKeydown}
      onblur={onBlur}
    />
    {#if value}
      <button
        type="button"
        class="time-btn"
        aria-label="Clear time"
        title="Remove time (date only)"
        onmousedown={(e) => e.preventDefault()}
        onclick={() => pick('')}>×</button
      >
    {/if}
    <button
      type="button"
      class="time-btn"
      aria-label="Choose time"
      title={listOpen ? 'Hide times' : 'Pick a time'}
      onmousedown={(e) => e.preventDefault()}
      onclick={() => {
        if (listOpen) {
          listOpen = false;
        } else {
          inputEl?.focus();
          openList(false);
        }
      }}><span class="time-caret" class:open={listOpen}><Icon name="chevronRight" size={12} /></span></button
    >
  </div>

  <div class="time-presets" role="group" aria-label="Quick times">
    {#each TIME_PRESETS as p (p.hhmm)}
      <button
        type="button"
        class="pick time-chip"
        class:on={value === p.hhmm}
        onmousedown={(e) => e.preventDefault()}
        onclick={() => pick(p.hhmm)}>{p.short}</button
      >
    {/each}
  </div>

  {#if listOpen}
    <div bind:this={listEl} id={listId} class="time-list" role="listbox" aria-label="Time options">
      {#each slots as s, i (s.hhmm)}
        <button
          type="button"
          class="time-opt"
          class:active={i === active}
          class:selected={s.hhmm === value}
          role="option"
          aria-selected={s.hhmm === value}
          data-hhmm={s.hhmm}
          onmousedown={(e) => e.preventDefault()}
          onmouseenter={() => (active = i)}
          onclick={() => pick(s.hhmm)}>{s.label}</button
        >
      {:else}
        <span class="time-empty muted small">Type a time like 9:30am or 14:00</span>
      {/each}
    </div>
  {/if}
</div>

<style>
  .timepick {
    display: grid;
    gap: 6px;
    min-width: 0;
  }
  .time-input-wrap {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 4px 0 8px;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--panel);
    min-height: 30px;
  }
  .time-input-wrap:focus-within {
    border-color: var(--accent);
  }
  .time-ic {
    display: inline-flex;
    color: var(--muted);
    flex: none;
  }
  .time-input {
    flex: 1 1 90px;
    min-width: 0;
    border: 0;
    padding: 4px 2px;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 0.88rem;
    outline: none;
  }
  .time-input::placeholder {
    color: var(--muted);
  }
  .time-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    padding: 0;
    border: 0;
    border-radius: 4px;
    background: transparent;
    color: var(--muted);
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
    flex: none;
  }
  .time-btn:hover {
    background: var(--chip);
    color: var(--text);
  }
  .time-caret {
    display: inline-flex;
    rotate: 90deg;
    transition: rotate 0.12s;
  }
  .time-caret.open {
    rotate: -90deg;
  }
  .time-presets {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .time-chip {
    padding: 2px 7px;
    font-size: 0.78rem;
  }
  .time-list {
    max-height: 180px;
    overflow-y: auto;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--panel);
    padding: 3px;
    display: grid;
    gap: 1px;
  }
  .timepick.compact .time-list {
    max-height: 150px;
  }
  .time-opt {
    display: flex;
    align-items: center;
    width: 100%;
    padding: 4px 8px;
    border: 0;
    border-radius: 4px;
    background: transparent;
    color: var(--text);
    font: inherit;
    font-size: 0.85rem;
    text-align: left;
    cursor: pointer;
  }
  .time-opt.active,
  .time-opt:hover {
    background: var(--chip);
  }
  .time-opt.selected {
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    color: var(--accent);
    font-weight: 600;
  }
  .time-empty {
    padding: 6px 8px;
  }
</style>
