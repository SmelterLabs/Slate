<script lang="ts">
  // Dependencies (ClickUp "waiting on / blocking / linked", here "Depends on" so it never shares
  // a word with the person field "Waiting on", lib/words.ts): what this item depends on, what waits
  // on it, related items and duplicates, each removable; "+ Add" searches items by title.
  // Self-contained so the task window can mount it as is: props are just the item.
  import { board } from '../lib/store.svelte';
  import type { Item, LinkKind } from '../lib/types';
  import { isClosed } from '../lib/views';

  let { item }: { item: Item } = $props();

  type Kind = LinkKind;
  type Key = 'blocked_by' | 'blocking' | 'related' | 'duplicate_of' | 'duplicated_by';
  const KINDS: { kind: Kind; key: Key; label: string }[] = [
    { kind: 'blocked_by', key: 'blocked_by', label: 'Depends on' },
    { kind: 'blocks', key: 'blocking', label: 'Blocking' },
    { kind: 'relates', key: 'related', label: 'Linked' },
    { kind: 'duplicate_of', key: 'duplicate_of', label: 'Duplicate of' },
    { kind: 'duplicated_by', key: 'duplicated_by', label: 'Duplicates' },
  ];
  const KEYS = KINDS.map((k) => k.key);

  const live = $derived(board.items[item.id] ?? item);
  const total = $derived(KEYS.reduce((n, k) => n + (live[k]?.length ?? 0), 0));

  let adding = $state<Kind | null>(null);
  let q = $state('');
  const matches = $derived.by(() => {
    const t = q.trim().toLowerCase();
    if (!adding || t.length < 2) return [];
    const taken = new Set([live.id, ...KEYS.flatMap((k) => live[k] ?? [])]);
    return board.plainItems
      .filter((i) => !taken.has(i.id) && !i.archived_at && i.title.toLowerCase().includes(t))
      .sort((a, b) => Number(isClosed(a)) - Number(isClosed(b)) || a.title.localeCompare(b.title))
      .slice(0, 8);
  });

  function title(id: string): string {
    return board.items[id]?.title ?? 'An item not on the board';
  }

  async function pick(other: Item) {
    if (!adding) return;
    const kind = adding;
    adding = null;
    q = '';
    await board.link(live.id, kind, other.id);
  }
</script>

<div class="deps" aria-label="Dependencies">
  <h3>
    Dependencies {#if total}<span class="count">{total}</span>{/if}
    {#if live.blocked}<span class="blocked-badge" title="Depends on an open task">Depends on</span>{/if}
  </h3>
  {#each KINDS as k (k.kind)}
    {@const ids = live[k.key] ?? []}
    {#if ids.length}
      <div class="dep-group" data-kind={k.kind}>
        <span class="muted small">{k.label}</span>
        {#each ids as id (id)}
          {@const other = board.items[id]}
          <div class="dep-row" class:done={other && isClosed(other)}>
            <span class="dot" style:background={other?.status?.color ?? `var(--kind-${other?.status?.kind ?? 'open'})`}
            ></span>
            <button class="plain dep-title" onclick={() => board.open(id)}>{title(id)}</button>
            <button
              class="x"
              aria-label="Remove link to {title(id)}"
              onclick={() => board.link(live.id, k.kind, id, false)}>×</button
            >
          </div>
        {/each}
      </div>
    {/if}
  {/each}

  {#if adding}
    <div class="dep-add">
      <select aria-label="Dependency kind" bind:value={adding}>
        <option value="blocked_by">Depends on</option>
        <option value="blocks">Blocking</option>
        <option value="relates">Linked to</option>
        <option value="duplicate_of">Duplicate of</option>
      </select>
      <!-- svelte-ignore a11y_autofocus -->
      <input aria-label="Find item" placeholder="Search items…" autofocus bind:value={q} />
      <button class="link small" onclick={() => ((adding = null), (q = ''))}>Cancel</button>
      {#if matches.length}
        <ul class="suggest" role="listbox" aria-label="Items">
          {#each matches as m (m.id)}
            <li role="option" aria-selected={false}>
              <button onclick={() => pick(m)}
                >{m.title} <span class="muted small">{board.lists[m.list_id]?.name ?? ''}</span></button
              >
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {:else}
    <span class="dep-actions">
      <button class="link small" onclick={() => (adding = 'blocked_by')}>+ Add dependency</button>
    </span>
  {/if}
</div>
