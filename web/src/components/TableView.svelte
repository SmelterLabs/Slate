<script lang="ts" module>
  export type ColumnKind =
    | 'list'
    | 'status'
    | 'assignees'
    | 'due'
    | 'start'
    | 'priority'
    | 'tags'
    | 'estimate';

  export interface Column {
    key: string;
    kind: ColumnKind;
    label: string;
    sort: SortField | null;
  }

  import type { SortField } from '../lib/views';
</script>

<script lang="ts">
  // Table layout (ClickUp's Table view): a spreadsheet of the view's items, every cell editable
  // in place, grouped like the List layout, with a totals row per group (count and estimate).
  // The columns follow the view's card fields ("Show").
  import { formatEstimate } from '../lib/format';
  import { board } from '../lib/store.svelte';
  import { GROUP_TYPE, markSourceGroup, refusal, type Group, type Scope, type ViewConfig } from '../lib/views';
  import TableRow from './TableRow.svelte';
  import Icon from './Icon.svelte';

  let { groups, config, scope }: { groups: Group[]; config: ViewConfig; scope: Scope } = $props();

  const PAGE = 100;
  let collapsed = $state<Record<string, boolean>>({});
  let limits = $state<Record<string, number>>({});
  let dragOver = $state<string | null>(null);

  const columns: Column[] = $derived.by(() => {
    const show = new Set<string>(config.card_fields);
    const cols: Column[] = [];
    if (scope.kind !== 'list' && show.has('list')) cols.push({ key: 'list', kind: 'list', label: 'List', sort: null });
    cols.push({ key: 'status', kind: 'status', label: 'Status', sort: null });
    if (show.has('assignees')) cols.push({ key: 'assignees', kind: 'assignees', label: 'Assignees', sort: null });
    if (show.has('due')) {
      cols.push({ key: 'due', kind: 'due', label: 'Due date', sort: 'due' });
      cols.push({ key: 'start', kind: 'start', label: 'Start date', sort: 'start' });
    }
    if (show.has('priority')) cols.push({ key: 'priority', kind: 'priority', label: 'Priority', sort: 'priority' });
    if (show.has('tags')) cols.push({ key: 'tags', kind: 'tags', label: 'Tags', sort: null });
    cols.push({ key: 'estimate', kind: 'estimate', label: 'Estimate', sort: 'estimate' });
    return cols;
  });

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

  /** The totals row: estimates are summed; other columns are blank. */
  function total(g: Group, c: Column): string {
    if (c.kind === 'estimate') {
      return formatEstimate(g.items.reduce((n, i) => n + (i.estimate_minutes ?? 0), 0));
    }
    return '';
  }
</script>

<div class="table-wrap">
  <table class="grid-table" aria-label="Items table" ondragstart={markSourceGroup}>
    <thead>
      <tr>
        <th class="t-title">
          <button class="plain" onclick={() => sortBy('title')}
            >Name{#if config.sort[0]?.field === 'title'}{config.sort[0].dir === 'asc' ? ' ↑' : ' ↓'}{/if}</button
          >
        </th>
        {#each columns as c (c.key)}
          <th class="t-{c.kind}" data-col={c.key}>
            {#if c.sort}
              {@const s = c.sort}
              <button class="plain" onclick={() => sortBy(s)}
                >{c.label}{#if config.sort[0]?.field === s}{config.sort[0].dir === 'asc' ? ' ↑' : ' ↓'}{/if}</button
              >
            {:else}{c.label}{/if}
          </th>
        {/each}
      </tr>
    </thead>
    {#each groups as g (g.key)}
      {@const limit = limits[g.key] ?? PAGE}
      <tbody data-group={g.key}>
        {#if config.group_by !== 'none'}
          <tr class="t-group">
            <th colspan={columns.length + 1}>
              <button
                class="group-head"
                class:over={dragOver === g.key}
                class:hot={g.hot && g.items.length > 0}
                aria-expanded={!collapsed[g.key]}
                onclick={() => (collapsed[g.key] = !collapsed[g.key])}
                ondragover={(e) => {
                  // A group that can't take it still accepts, so the drop can say why.
                  e.preventDefault();
                  dragOver = g.key;
                }}
                ondragleave={() => (dragOver = null)}
                ondrop={(e) => onDrop(e, g)}
              >
                <span class="caret"><Icon name={collapsed[g.key] ? 'chevronRight' : 'down'} size={12} /></span>
                {#if g.color}<span class="dot" style:background={g.color}></span>{/if}
                <span>{g.title}</span>
                {#if g.hint && g.hint !== g.title}<span class="muted small">{g.hint}</span>{/if}
                <span class="count">{g.items.length}</span>
              </button>
            </th>
          </tr>
        {/if}
        {#if !collapsed[g.key]}
          {#each g.items.slice(0, limit) as item (item.id)}
            <TableRow {item} {columns} />
          {/each}
          {#if g.items.length > limit}
            <tr>
              <td colspan={columns.length + 1}>
                <button class="link small more" onclick={() => (limits[g.key] = limit + 300)}
                  >Show {Math.min(300, g.items.length - limit)} more of {g.items.length - limit}</button
                >
              </td>
            </tr>
          {/if}
          {#if g.items.length}
            <tr class="t-total">
              <td class="muted small">{g.items.length} item{g.items.length === 1 ? '' : 's'}</td>
              {#each columns as c (c.key)}<td class="muted small" data-col={c.key}>{total(g, c)}</td>{/each}
            </tr>
          {/if}
        {/if}
      </tbody>
    {:else}
      <tbody><tr><td class="muted pad" colspan={columns.length + 1}>Nothing matches this view.</td></tr></tbody>
    {/each}
  </table>
</div>
