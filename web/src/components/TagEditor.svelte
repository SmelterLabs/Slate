<script lang="ts">
  // Tags: chips plus an input that autocompletes from /tags. Enter adds the highlighted
  // suggestion, or creates the typed tag (Slate creates unknown tags on PATCH). Backspace on an
  // empty input removes the last tag. Every change is one PATCH of the whole set. The prop
  // trails that save, so the chips show the set last sent and the next edit includes it. The
  // saves run one at a time: a slower first write must not land last and drop a tag. The
  // suggestions are picked with the arrow keys or the mouse, never Tab stops: the list closes
  // a moment after the input loses focus, and a suggestion holding focus would vanish under it.
  import { board } from '../lib/store.svelte';
  import TagPill from './TagPill.svelte';

  let { tags, onchange, maxTags = Infinity }: {
    tags: string[];
    onchange: (tags: string[]) => void | Promise<unknown>;
    maxTags?: number;
  } = $props();

  let text = $state('');
  let focused = $state(false);
  let active = $state(0);
  let picked = $state(false);
  // Null once the prop has caught up with the set we sent.
  let ahead = $state<string[] | null>(null);
  const shown = $derived(ahead ?? tags);

  let sending = false;
  let queued: string[] | null = null;

  const typed = $derived(text.trim().replace(/^#/, '').toLowerCase());
  const suggestions = $derived(
    board.tags
      .map((t) => t.name)
      .filter((n) => !shown.includes(n) && (!typed || n.includes(typed)))
      .sort((a, b) => Number(!a.startsWith(typed)) - Number(!b.startsWith(typed)) || a.localeCompare(b))
      .slice(0, 8),
  );

  $effect(() => {
    if (!ahead) return;
    const have = new Set(tags);
    if (ahead.length === tags.length && ahead.every((t) => have.has(t))) ahead = null;
  });

  function commit(next: string[]) {
    ahead = next;
    queued = next;
    if (sending) return;
    sending = true;
    void drain();
  }

  async function drain() {
    while (queued) {
      const send = queued;
      queued = null;
      const saved = onchange(send);
      // Form drafts update synchronously. Only network saves need to hold the queue.
      if (saved) await saved;
    }
    sending = false;
    if (queued) {
      sending = true;
      void drain();
    }
  }

  function add(name: string) {
    const t = name.trim().replace(/^#/, '').toLowerCase();
    if (t && shown.length < maxTags && !shown.includes(t)) commit([...shown, t]);
    text = '';
    active = 0;
    picked = false;
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      // The typed text, unless the arrow keys picked a suggestion.
      add(picked ? (suggestions[active] ?? '') : typed);
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      active = picked ? Math.min(active + 1, suggestions.length - 1) : 0;
      picked = suggestions.length > 0;
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      active = Math.max(active - 1, 0);
      picked = suggestions.length > 0;
    } else if (e.key === 'Backspace' && !text && shown.length) {
      commit(shown.slice(0, -1));
    } else if (e.key === 'Escape' && text) {
      e.stopPropagation();
      text = '';
    }
  }
</script>

<div class="tag-editor">
  {#each shown as t (t)}
    <TagPill name={t}
      ><button class="x" aria-label="Remove tag {t}" onclick={() => commit(shown.filter((x) => x !== t))}
        >×</button
      ></TagPill
    >
  {/each}
  <span class="autocomplete">
    <input
      class="tiny"
      aria-label="Add tag"
      placeholder="+ tag"
      autocomplete="off"
      bind:value={text}
      onfocus={() => {
        focused = true;
        void board.loadTags();
      }}
      onblur={() => setTimeout(() => (focused = false), 150)}
      oninput={() => {
        active = 0;
        picked = false;
      }}
      onkeydown={onKey}
    />
    {#if focused && (suggestions.length || typed)}
      <ul class="suggest" role="listbox" aria-label="Tag suggestions">
        {#each suggestions as s, i (s)}
          <li role="option" aria-selected={picked && i === active}>
            <button tabindex="-1" onmousedown={(e) => e.preventDefault()} onclick={() => add(s)}>#{s}</button>
          </li>
        {/each}
        {#if typed && !suggestions.includes(typed) && !shown.includes(typed)}
          <li role="option" aria-selected={false}>
            <button tabindex="-1" onmousedown={(e) => e.preventDefault()} onclick={() => add(typed)}
              >Create #{typed}</button
            >
          </li>
        {/if}
      </ul>
    {/if}
  </span>
</div>
