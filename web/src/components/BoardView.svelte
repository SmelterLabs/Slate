<script lang="ts">
  // Board layout, ClickUp-style: one column per group, each scrolling on its own, with a count,
  // a ⋯ menu, and "+ Add Task" at the foot that files straight into that column. Empty columns
  // fold to a thin strip (still a drop target); the viewer can fold or open any column and the
  // board remembers it per view. Columns render 25 cards and grow on demand, so a 2,000-card
  // "No due date" column stays instant. With the manual sort, a card dropped between two others
  // stays there (lib/slots.ts); a line shows where it will land.
  import { isCollapsed } from '../lib/cards';
  import { beforeAt, slotFor } from '../lib/slots';
  import { board } from '../lib/store.svelte';
  import { isManual, refusal, type Group, type Scope, type ViewConfig } from '../lib/views';
  import Card from './Card.svelte';
  import ColumnAdd from './ColumnAdd.svelte';
  import Dropdown from './Dropdown.svelte';
  import Icon from './Icon.svelte';
  import SubtaskLinks from './SubtaskLinks.svelte';

  let {
    groups,
    config,
    wide,
    scope,
    viewKey,
    kiosk = false,
  }: { groups: Group[]; config: ViewConfig; wide: boolean; scope: Scope; viewKey: string; kiosk?: boolean } =
    $props();

  const PAGE = 25;
  let limits = $state<Record<string, number>>({});
  let dragOver = $state<string | null>(null);
  const manual = $derived(board.collapsedFor(viewKey));
  let adding = $state<string | null>(null);
  let menuFor = $state<string | null>(null);
  const tagMap = $derived(board.tagMap);

  function setCollapsed(key: string, value: boolean) {
    board.setCollapsed(viewKey, { ...manual, [key]: value });
  }

  // The card being dragged (dragover cannot read the drag's data), the column it came from,
  // and where it would land.
  let dragId = $state<string | null>(null);
  let dragFrom = $state<string | null>(null);
  let slot = $state<{ group: string; before: string | null } | null>(null);
  const manualOrder = $derived(!kiosk && isManual(config.sort));
  // A column that cannot take the card still accepts the drop, so letting go there says why
  // instead of the card silently snapping back.
  const accepts = (g: Group) =>
    !kiosk && (!!g.drop || (!!dragId && (g.key !== dragFrom || (manualOrder && g.items.some((i) => i.id === dragId)))));

  function overColumn(e: DragEvent, g: Group, col = e.currentTarget as HTMLElement) {
    if (!accepts(g)) return;
    e.preventDefault();
    dragOver = g.key;
    if (!manualOrder || !dragId) return;
    const cards = [...col.querySelectorAll<HTMLElement>('.cards > .card[data-item]')]
      .filter((c) => c.dataset.item !== dragId)
      .map((c) => {
        const r = c.getBoundingClientRect();
        return { id: c.dataset.item!, top: r.top, bottom: r.bottom };
      });
    const before = beforeAt(cards, e.clientY);
    if (slot?.group !== g.key || slot.before !== before) slot = { group: g.key, before };
  }

  async function onDrop(e: DragEvent, g: Group) {
    e.preventDefault();
    dragOver = null;
    const at = slot?.group === g.key ? slot : null;
    slot = null;
    const id = e.dataTransfer?.getData('text/plain') || dragId;
    const from = dragFrom ?? undefined;
    if (!id) return;
    if (!g.drop && g.key !== from) return board.flash(refusal(g));
    const ordered = g.items.map((i) => ({ id: i.id, position: i.position }));
    const item = board.items[id];
    if (g.drop) await board.applyDrop(id, g.drop, from);
    if (at && item) await board.reposition(slotFor(ordered, id, at.before, { id, position: item.position }));
  }

  // Columns are only as tall as their cards, so most of the board below a short column is bare
  // board. A card let go there belongs to the column above it, as on ClickUp.
  let boardEl = $state<HTMLElement | null>(null);
  function columnBelow(e: DragEvent): { g: Group; col: HTMLElement } | null {
    if (!boardEl || (e.target as Element | null)?.closest?.('.column')) return null;
    let best: { g: Group; col: HTMLElement; d: number } | null = null;
    for (const col of boardEl.querySelectorAll<HTMLElement>(':scope > .column[data-group]')) {
      const r = col.getBoundingClientRect();
      const d = e.clientX < r.left ? r.left - e.clientX : e.clientX > r.right ? e.clientX - r.right : 0;
      if (e.clientY < r.top || d > 8 || (best && best.d <= d)) continue;
      const g = groups.find((x) => x.key === col.dataset.group);
      if (g) best = { g, col, d };
    }
    return best;
  }

  /** Overdue reads red, Today gets the amber column, Done is green (the classic due-board look). */
  function tone(g: Group): string {
    if (g.key === 'done') return 'tone-done';
    if (config.group_by !== 'due') return '';
    if (g.key === 'overdue') return 'tone-overdue';
    if (g.key === 'today') return 'tone-today';
    return '';
  }

  // Overdue cards keep their date; a card cannot be added "overdue" or "done".
  const canAdd = (g: Group) =>
    !kiosk && g.drop?.kind !== 'done' && g.drop?.kind !== 'section' && (g.drop !== null || config.group_by === 'none');

  function selectAll(g: Group) {
    for (const i of g.items) board.selected.add(i.id);
  }
</script>

<div
  class="kanban cu-board"
  class:compact={config.density === 'compact'}
  class:linked={config.show_subtasks}
  role="presentation"
  bind:this={boardEl}
  ondragover={(e) => {
    const at = columnBelow(e);
    if (at) overColumn(e, at.g, at.col);
    else if (!(e.target as Element | null)?.closest?.('.column')) {
      dragOver = null;
      slot = null;
    }
  }}
  ondrop={(e) => {
    const at = columnBelow(e);
    if (at) void onDrop(e, at.g);
  }}
  ondragstart={(e) => {
    const el = e.target as HTMLElement;
    dragId = el.closest<HTMLElement>('[data-item]')?.dataset.item ?? null;
    dragFrom = el.closest<HTMLElement>('.column[data-group]')?.dataset.group ?? null;
  }}
  ondragend={() => {
    dragId = null;
    dragFrom = null;
    slot = null;
    dragOver = null;
  }}
>
  {#if config.show_subtasks}<SubtaskLinks {groups} host={boardEl} hidden={!!dragId} />{/if}
  {#each groups as g (g.key)}
    {@const limit = limits[g.key] ?? PAGE}
    {@const folded = isCollapsed(g.key, g.items.length, manual, config.collapse_empty) && adding !== g.key}
    <div
      class="column {tone(g)}"
      class:collapsed={folded}
      class:over={dragOver === g.key}
      class:nodrop={!g.drop}
      data-group={g.key}
      data-status={g.drop?.kind === 'status' ? g.title : undefined}
      role="list"
      aria-label={g.title}
      ondragover={(e) => overColumn(e, g)}
      ondragleave={(e) => {
        if ((e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) return;
        dragOver = null;
        if (slot?.group === g.key) slot = null;
      }}
      ondrop={(e) => onDrop(e, g)}
    >
      {#if folded}
        <button class="column-strip" title="Show {g.title}" onclick={() => setCollapsed(g.key, false)}>
          <span class="strip-title">{g.title}</span>
          <span class="count">{g.items.length}</span>
        </button>
      {:else}
        <div class="column-head" class:hot={g.hot && g.items.length > 0} title={g.hint && g.hint !== g.title ? g.hint : undefined}>
          <span class="col-title">
            {#if g.color && g.key !== 'done'}<span class="dot" style:background={g.color}></span>{/if}
            <span class="col-name">{g.title}</span>
            <!-- Day columns keep their date in the tooltip; Future says where it starts. -->
            {#if g.hint && g.hint !== g.title && (g.key === 'future' || config.group_by !== 'due')}<span
                class="muted small col-hint">{g.hint}</span
              >{/if}
            <span class="count">{g.items.length}</span>
          </span>
          {#if !kiosk}
            <span class="col-tools" class:on={menuFor === g.key}>
              <Dropdown
                float
                title="Column options"
                cls="icon"
                align="right"
                bind:open={() => menuFor === g.key, (v) => (menuFor = v ? g.key : null)}
              >
                {#snippet label()}<Icon name="more" size={16} />{/snippet}
                <div class="menu">
                  <button
                    onclick={() => {
                      menuFor = null;
                      setCollapsed(g.key, true);
                    }}><Icon name="collapse" /> Collapse column</button
                  >
                  {#if g.items.length}
                    <button
                      onclick={() => {
                        menuFor = null;
                        selectAll(g);
                      }}><Icon name="checklist" /> Select all {g.items.length}</button
                    >
                  {/if}
                  {#if canAdd(g)}
                    <button
                      onclick={() => {
                        menuFor = null;
                        adding = g.key;
                      }}><Icon name="plus" /> Add task</button
                    >
                  {/if}
                </div>
              </Dropdown>
              {#if canAdd(g)}
                <button class="icon" title="Add task to {g.title}" aria-label="Add task to {g.title}" onclick={() => (adding = g.key)}
                  ><Icon name="plus" size={16} /></button
                >
              {/if}
            </span>
          {/if}
        </div>
        <div class="cards">
          {#each g.items.slice(0, limit) as item (item.id)}
            <Card {item} {config} showList={wide} {tagMap} slotBefore={slot?.group === g.key && slot.before === item.id} />
          {/each}
          {#if slot?.group === g.key && slot.before === null}<div class="slot-end" aria-hidden="true"></div>{/if}
          {#if g.items.length > limit}
            <button class="link small more" onclick={() => (limits[g.key] = limit + 200)}
              >Show {Math.min(200, g.items.length - limit)} more of {g.items.length - limit}</button
            >
          {/if}
          {#if canAdd(g)}
            <ColumnAdd
              group={g}
              {scope}
              {viewKey}
              open={adding === g.key}
              onopen={() => (adding = g.key)}
              onclose={() => adding === g.key && (adding = null)}
            />
          {/if}
        </div>
      {/if}
    </div>
  {:else}
    <p class="muted pad">Nothing matches this view.</p>
  {/each}
</div>
