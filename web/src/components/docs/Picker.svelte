<script lang="ts" module>
  import { trapFocus } from '../../lib/focustrap';
  export interface PickerOption {
    id: string;
    label: string;
    hint?: string;
    icon?: string;
    /** Indents an option under the one before (a page under its parent). */
    depth?: number;
    disabled?: boolean;
  }
</script>

<script lang="ts">
  // A small searchable chooser: embed a task, link a page, move a page, pick a list. Arrow keys
  // and Enter, or tap; Esc closes. `oncreate` adds a "Create “…”" row for the typed text.
  import { fuzzy } from '../../lib/palette';

  let {
    title,
    placeholder = 'Search',
    options,
    onpick,
    onclose,
    oncreate = undefined,
    createLabel = 'Create',
  }: {
    title: string;
    placeholder?: string;
    options: PickerOption[];
    onpick: (id: string) => void;
    onclose: () => void;
    oncreate?: (text: string) => void;
    createLabel?: string;
  } = $props();

  let query = $state('');
  let cursor = $state(0);
  let input = $state<HTMLInputElement>();

  const shown = $derived.by(() => {
    const q = query.trim();
    if (!q) return options.slice(0, 200);
    return options
      .map((o) => ({ o, m: fuzzy(q, `${o.label} ${o.hint ?? ''}`) }))
      .filter((x) => x.m)
      .sort((a, b) => b.m!.score - a.m!.score)
      .slice(0, 60)
      .map((x) => ({ ...x.o, depth: 0 }));
  });
  const canCreate = $derived(!!oncreate && query.trim().length > 0);
  const total = $derived(shown.length + (canCreate ? 1 : 0));

  $effect(() => {
    input?.focus();
  });
  $effect(() => {
    void query;
    cursor = 0;
  });

  function choose(n: number) {
    if (n < shown.length) {
      const o = shown[n];
      if (o.disabled) return;
      onpick(o.id);
    } else if (canCreate) oncreate!(query.trim());
  }

  function keys(e: KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      cursor = Math.min(total - 1, cursor + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      cursor = Math.max(0, cursor - 1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      choose(cursor);
    }
  }
</script>

<div class="picker-scrim" role="presentation" onclick={onclose}></div>
<div class="picker" role="dialog" aria-modal="true" aria-label={title} use:trapFocus={{ onescape: onclose }}>
  <header>{title}</header>
  <input bind:this={input} bind:value={query} {placeholder} aria-label={placeholder} onkeydown={keys} />
  <div class="doc-picker-list" role="listbox">
    {#each shown as o, n (o.id)}
      <button
        role="option"
        aria-selected={n === cursor}
        class:active={n === cursor}
        disabled={o.disabled}
        style="padding-left:{12 + (o.depth ?? 0) * 16}px"
        onmouseenter={() => (cursor = n)}
        onclick={() => choose(n)}
      >
        {#if o.icon}<span class="pk-icon">{o.icon}</span>{/if}
        <span class="pk-label">{o.label}</span>
        {#if o.hint}<span class="pk-hint">{o.hint}</span>{/if}
      </button>
    {/each}
    {#if canCreate}
      <button
        role="option"
        aria-selected={cursor === shown.length}
        class="pk-create"
        class:active={cursor === shown.length}
        onmouseenter={() => (cursor = shown.length)}
        onclick={() => choose(shown.length)}>＋ {createLabel} “{query.trim()}”</button
      >
    {/if}
    {#if !total}<p class="muted pk-empty">Nothing matches.</p>{/if}
  </div>
</div>
