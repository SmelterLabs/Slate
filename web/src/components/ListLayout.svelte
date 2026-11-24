<script lang="ts">
  // List layout: collapsible groups of table-like rows. Click a column header to sort by it
  // (click again to flip); rows can be dragged onto a group header, and with the manual sort
  // between two rows to put them in order (lib/slots.ts).
  import { beforeAt, slotFor } from '../lib/slots';
  import { board } from '../lib/store.svelte';
  import { GROUP_TYPE, isManual, markSourceGroup, refusal, type Group, type SortField, type ViewConfig } from '../lib/views';
  import ItemRow from './ItemRow.svelte';
  import Icon from './Icon.svelte';

  let { groups, config, wide }: { groups: Group[]; config: ViewConfig; wide: boolean } = $props();

  const PAGE = 100;
  let collapsed = $state<Record<string, boolean>>({});
  const isFolded = (key: string) => collapsed[key] ?? key === 'later';
  let limits = $state<Record<string, number>>({});
  let dragOver = $state<string | null>(null);

  // Grouped by status, the Status column would repeat every group's name: it goes.
  const noStatus = $derived(config.group_by === 'status');
  const columns: { label: string; field: SortField | null; cls: string }[] = $derived([
    { label: 'Title', field: 'title', cls: 'c-title' },
    ...(wide ? [{ label: 'List', field: null, cls: 'c-list' }] : []),
    ...(noStatus ? [] : [{ label: 'Status', field: null, cls: 'c-status' }]),
    { label: 'Due', field: 'due', cls: 'c-due' },
    { label: 'Priority', field: 'priority', cls: 'c-prio' },
    { label: 'Who', field: null, cls: 'c-who' },
    { label: 'Tags', field: null, cls: 'c-tags' },
  ]);

  function sortBy(field: SortField) {
    const first = config.sort[0];
    const dir = first?.field === field && first.dir === 'asc' ? 'desc' : 'asc';
    board.setConfig({ sort: [{ field, dir }, ...config.sort.filter((k) => k.field !== field)] });
  }

  function onDrop(e: DragEvent, g: Group) {
    e.preventDefault();
    dragOver = null;
    const id = e.dataTransfer?.getData('text/plain');
    if (!id) return;
    const from = e.dataTransfer?.getData(GROUP_TYPE) || undefined;
    if (g.drop) void board.applyDrop(id, g.drop, from);
    else if (g.key !== from) board.flash(refusal(g));
  }

  // Manual order: the row being dragged and the row it would land in front of.
  let dragId = $state<string | null>(null);
  let slot = $state<{ group: string; before: string | null } | null>(null);
  const manualOrder = $derived(isManual(config.sort));
  // On a list page, cards brought in from other sections keep their own list's order.
  const pageListId = $derived(board.selection.kind === 'list' ? board.selection.id : null);
  const ownRow = (id: string) => !pageListId || board.items[id]?.list_id === pageListId;

  function overRows(e: DragEvent, g: Group) {
    if ((e.target as Element).closest('.group-head')) return; // the header means the group
    if (!manualOrder || !dragId || !ownRow(dragId) || (!g.drop && !g.items.some((i) => i.id === dragId))) return;
    e.preventDefault();
    const rows = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('.row[data-item]')]
      .filter((r) => r.dataset.item !== dragId && ownRow(r.dataset.item!))
      .map((r) => {
        const b = r.getBoundingClientRect();
        return { id: r.dataset.item!, top: b.top, bottom: b.bottom };
      });
    const before = beforeAt(rows, e.clientY);
    if (slot?.group !== g.key || slot.before !== before) slot = { group: g.key, before };
  }

  async function dropInRows(e: DragEvent, g: Group) {
    const at = slot?.group === g.key ? slot : null;
    slot = null;
    if (!at || (e.target as Element).closest('.group-head')) return;
    e.preventDefault();
    const id = e.dataTransfer?.getData('text/plain');
    const item = id ? board.items[id] : null;
    if (!id || !item) return;
    const ordered = g.items.filter((i) => ownRow(i.id)).map((i) => ({ id: i.id, position: i.position }));
    if (g.drop) await board.applyDrop(id, g.drop, e.dataTransfer?.getData(GROUP_TYPE) || undefined);
    await board.reposition(slotFor(ordered, id, at.before, { id, position: item.position }));
  }
</script>

<div
  class="table"
  role="table"
  aria-label="Items"
  ondragstart={(e) => {
    dragId = (e.target as HTMLElement).closest<HTMLElement>('[data-item]')?.dataset.item ?? null;
    markSourceGroup(e);
  }}
  ondragend={() => {
    dragId = null;
    slot = null;
  }}
>
  <div class="row head" class:wide class:no-status={noStatus} role="row">
    {#each columns as c (c.label)}
      <span class="cell {c.cls}" role="columnheader">
        {#if c.field}
          {@const field = c.field}
          <button class="plain" onclick={() => sortBy(field)}
            >{c.label}{#if config.sort[0]?.field === field}{config.sort[0].dir === 'asc' ? ' ↑' : ' ↓'}{/if}</button
          >
        {:else}{c.label}{/if}
      </span>
    {/each}
  </div>
  {#each groups as g (g.key)}
    {@const limit = limits[g.key] ?? PAGE}
    <div
      class="list-group"
      data-group={g.key}
      role="rowgroup"
      ondragover={(e) => overRows(e, g)}
      ondragleave={(e) => {
        if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node) && slot?.group === g.key) slot = null;
      }}
      ondrop={(e) => dropInRows(e, g)}
    >
      {#if config.group_by !== 'none' || g.key === 'later'}
        <button
          class="group-head"
          class:over={dragOver === g.key}
          class:hot={g.hot && g.items.length > 0}
          aria-expanded={!isFolded(g.key)}
          onclick={() => (collapsed[g.key] = !isFolded(g.key))}
          ondragover={(e) => {
            // A group that can't take it still accepts, so the drop can say why.
            e.preventDefault();
            dragOver = g.key;
          }}
          ondragleave={() => (dragOver = null)}
          ondrop={(e) => onDrop(e, g)}
        >
          <span class="caret"><Icon name={isFolded(g.key) ? 'chevronRight' : 'down'} size={12} /></span>
          {#if g.color}<span class="dot" style:background={g.color}></span>{/if}
          <span>{g.key === 'later' ? `Later (${g.items.length})` : g.title}</span>
          {#if g.hint && g.hint !== g.title}<span class="muted small">{g.hint}</span>{/if}
          {#if g.key !== 'later'}<span class="count">{g.items.length}</span>{/if}
        </button>
      {/if}
      {#if !isFolded(g.key)}
        {#each g.items.slice(0, limit) as item (item.id)}
          <ItemRow {item} {config} showList={wide} {noStatus} slotBefore={slot?.group === g.key && slot.before === item.id} />
        {/each}
        {#if slot?.group === g.key && slot.before === null}<div class="slot-end" aria-hidden="true"></div>{/if}
        {#if g.items.length > limit}
          <button class="link small more" onclick={() => (limits[g.key] = limit + 300)}
            >Show {Math.min(300, g.items.length - limit)} more of {g.items.length - limit}</button
          >
        {/if}
      {/if}
    </div>
  {:else}
    <p class="muted pad">Nothing matches this view.</p>
  {/each}
</div>
