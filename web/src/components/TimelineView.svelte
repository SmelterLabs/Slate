<script lang="ts">
  // Timeline (Gantt) layout: the view's items as bars from start to due on a day/week/month
  // axis, grouped the way the view is grouped, with a "today" line and dependency arrows from
  // blocked_by links. Drag a bar to move both dates; drag its left or right edge to change the
  // start or the due date; a task with only a due date is a diamond you can still drag. Click a
  // bar to open the task. Tasks with no due date sit in a tray below, ready to be dragged on.
  import { board } from '../lib/store.svelte';
  import { weekday } from '../lib/views';
  import { holdToDrag } from '../lib/touchdrag';
  import { workspaceDay } from '../lib/quickadd';
  import { dueLabel } from '../lib/format';
  import {
    arrows,
    axisDays,
    axisRange,
    BAR_H,
    type Bar,
    DAY_PX,
    dayIndex,
    dayToIso,
    dragDays,
    ROW_H,
    rows,
    ticks,
    type Zoom,
  } from '../lib/timeline';
  import type { Group, Scope, ViewConfig } from '../lib/views';
  import type { Item, UpcomingRepeat } from '../lib/types';
  import CardMenu from './CardMenu.svelte';
  import Icon from './Icon.svelte';

  let { groups, config, scope }: { groups: Group[]; config: ViewConfig; scope: Scope } = $props();

  // The same ⋯ / right-click task menu as board cards and list rows, one row at a time.
  let menuId = $state<string | null>(null);
  let menuOpen = $state(false);

  const zoom = $derived((config.timeline_zoom ?? 'week') as Zoom);
  const px = $derived(DAY_PX[zoom]);
  const today = $derived(workspaceDay(board.now));
  const withHeaders = $derived(config.group_by !== 'none');

  // One flat item list (deduped) for the axis range; the grouped rows for the layout.
  const allItems = $derived.by(() => {
    const seen = new Map<string, Item>();
    for (const g of groups) for (const i of g.items) seen.set(i.id, i);
    return [...seen.values()];
  });
  const range = $derived(axisRange(allItems, today));
  const days = $derived(axisDays(range.from, range.to));
  const width = $derived(days.length * px);
  const marks = $derived(ticks(range.from, range.to, zoom));
  const rowList = $derived(rows(groups, withHeaders));
  const unscheduled = $derived(allItems.filter((i) => !i.due_at));

  // Bars by id, and their row index, for the dependency arrows.
  const barRow = $derived.by(() => {
    const m = new Map<string, { bar: Bar; row: number }>();
    rowList.forEach((r, i) => {
      if (r.kind === 'item' && r.bar) m.set(r.item!.id, { bar: r.bar, row: i });
    });
    return m;
  });
  const bars = $derived(new Map([...barRow].map(([id, v]) => [id, v.bar])));
  const deps = $derived(arrows(bars));

  const todayX = $derived((dayIndex(range.from, today) + 0.5) * px);
  const left = (day: string) => dayIndex(range.from, day) * px;
  const barLeft = (b: Bar) => left(b.start);
  /** Narrower than this and the bar's own label would be three letters and an ellipsis. */
  const LABEL_MIN = 96;
  const barWidth = (b: Bar) => (dayIndex(range.from, b.end) - dayIndex(range.from, b.start) + 1) * px;
  const color = (i: Item) => i.status?.color ?? `var(--kind-${i.status?.kind ?? 'open'})`;

  // --- drag: move the whole bar, or one edge --------------------------------------------------
  type Mode = 'move' | 'start' | 'end';
  let drag = $state<{ id: string; mode: Mode; x0: number; days: number } | null>(null);
  // A drag ends with a click event on the bar; this suppresses the open that would follow.
  let justDragged = false;
  const shifted = (b: Bar) => {
    if (!drag || drag.id !== b.item.id || !drag.days) return b;
    if (drag.mode === 'move') return { ...b, start: shift(b.start, drag.days), end: shift(b.end, drag.days) };
    if (drag.mode === 'start') {
      const s = shift(b.start, drag.days);
      return { ...b, start: s > b.end ? b.end : s };
    }
    const e = shift(b.end, drag.days);
    return { ...b, end: e < b.start ? b.start : e };
  };
  function shift(day: string, n: number): string {
    const d = new Date(`${day}T12:00:00Z`);
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  function down(e: PointerEvent, b: Bar, mode: Mode) {
    if (e.button !== 0) return;
    e.stopPropagation();
    const el = e.currentTarget as HTMLElement;
    // A finger holds still a moment first, so a swipe across the chart still scrolls it.
    holdToDrag(e, (from) => {
      if (el.isConnected) el.setPointerCapture(from.pointerId);
      drag = { id: b.item.id, mode, x0: from.clientX, days: 0 };
    });
  }
  function move(e: PointerEvent) {
    if (!drag) return;
    drag.days = dragDays(e.clientX - drag.x0, zoom);
  }
  async function up(b: Bar) {
    if (!drag || drag.id !== b.item.id) return;
    const d = drag;
    drag = null;
    if (!d.days) return;
    justDragged = true;
    setTimeout(() => (justDragged = false), 0);
    const nb = shiftedFrom(b, d);
    const it = b.item;
    if (d.mode === 'move') {
      await board.setDates(it.id, {
        start_at: it.start_at ? dayToIso(nb.start, it.start_at) : null,
        due_at: dayToIso(nb.end, it.due_at),
      });
    } else if (d.mode === 'start') {
      await board.setDates(it.id, { start_at: dayToIso(nb.start, it.start_at) });
    } else {
      await board.setDates(it.id, { due_at: dayToIso(nb.end, it.due_at) });
    }
  }
  function shiftedFrom(b: Bar, d: { mode: Mode; days: number }): Bar {
    if (d.mode === 'move') return { ...b, start: shift(b.start, d.days), end: shift(b.end, d.days) };
    if (d.mode === 'start') {
      const s = shift(b.start, d.days);
      return { ...b, start: s > b.end ? b.end : s };
    }
    const e = shift(b.end, d.days);
    return { ...b, end: e < b.start ? b.start : e };
  }

  // Drop a task from the tray onto a day: give it that due date.
  let dragOverDay = $state<string | null>(null);
  function trayDrop(e: DragEvent) {
    e.preventDefault();
    const id = e.dataTransfer?.getData('text/plain');
    const day = dragOverDay;
    dragOverDay = null;
    if (id && day) void board.reschedule(id, day);
  }
  function dayAt(clientX: number, lane: HTMLElement): string {
    const rect = lane.getBoundingClientRect();
    const i = Math.floor((clientX - rect.left + lane.scrollLeft) / px);
    return days[Math.max(0, Math.min(days.length - 1, i))];
  }

  const bodyH = $derived(rowList.length * ROW_H);

  // Future repeats: dashed markers on the row of each rule's newest task, from today on.
  let repeats = $state<UpcomingRepeat[]>([]);
  $effect(() => {
    void board.recurRev;
    const first = today;
    const last = range.to;
    if (first > last) {
      repeats = [];
      return;
    }
    let live = true;
    void board.upcoming(first, last).then((r) => {
      if (live) repeats = r;
    });
    return () => (live = false);
  });
  const ghostsByItem = $derived.by(() => {
    const newest = new Map<string, Item>();
    for (const r of rowList) {
      const it = r.kind === 'item' ? r.item : undefined;
      if (!it?.recurrence_id) continue;
      const have = newest.get(it.recurrence_id);
      if (!have || (it.due_at ?? '') > (have.due_at ?? '')) newest.set(it.recurrence_id, it);
    }
    const m = new Map<string, UpcomingRepeat[]>();
    for (const g of repeats) {
      const it = newest.get(g.recurrence_id);
      if (it) m.set(it.id, [...(m.get(it.id) ?? []), g]);
    }
    return m;
  });
</script>

<div class="tl">
  <div class="tl-toolbar">
    <div class="seg" role="group" aria-label="Zoom">
      <button class:on={zoom === 'day'} onclick={() => board.setConfig({ timeline_zoom: 'day' })}>Day</button>
      <button class:on={zoom === 'week'} onclick={() => board.setConfig({ timeline_zoom: 'week' })}>Week</button>
      <button class:on={zoom === 'month'} onclick={() => board.setConfig({ timeline_zoom: 'month' })}>Month</button>
    </div>
    <span class="muted small">Start → due as bars. Drag to move, drag an edge to resize. Arrows show what waits on what.</span>
  </div>

  {#if !barRow.size && !unscheduled.length}
    <p class="muted pad-row">No tasks here yet.</p>
  {:else}
    <div class="tl-scroll">
      <div class="tl-inner" style:--px="{px}px" style:width="calc(var(--gutter) + {width}px)">
        <!-- header: the date axis -->
        <div class="tl-head">
          <div class="tl-gutter head">Task</div>
          <div class="tl-axis" style:width="{width}px">
            {#each marks as t (t.at)}
              <div class="tl-tick" class:major={t.major} style:left="{t.at * px}px" style:width="{t.span * px}px">
                <span>{t.label}</span>
              </div>
            {/each}
          </div>
        </div>

        <div class="tl-body" style:height="{bodyH}px">
          <!-- weekend/day grid + today line, behind the rows -->
          <div class="tl-grid" style:left="var(--gutter)" style:width="{width}px">
            {#each marks as t (t.at)}
              <div class="tl-col" class:major={t.major} style:left="{t.at * px}px"></div>
            {/each}
            {#each days as d, i (d)}
              {#if weekday(d) === 0 || weekday(d) === 6}
                <div class="tl-weekend" style:left="{i * px}px" style:width="{px}px"></div>
              {/if}
            {/each}
            <div class="tl-today" style:left="{todayX}px" title="Today"></div>
          </div>

          <!-- dependency arrows -->
          <svg class="tl-arrows" style:left="var(--gutter)" style:width="{width}px" style:height="{bodyH}px" aria-hidden="true">
            {#each deps as a (a.fromId + a.toId)}
              {@const f = barRow.get(a.fromId)}
              {@const t = barRow.get(a.toId)}
              {#if f && t}
                {@const x1 = barLeft(shifted(f.bar)) + barWidth(shifted(f.bar))}
                {@const y1 = f.row * ROW_H + ROW_H / 2}
                {@const x2 = barLeft(shifted(t.bar))}
                {@const y2 = t.row * ROW_H + ROW_H / 2}
                <path
                  d="M {x1} {y1} C {x1 + 16} {y1}, {x2 - 16} {y2}, {Math.max(x2 - 7, x1)} {y2}"
                  class="tl-arrow"
                />
                <path d="M {x2} {y2} l -7 -4 l 0 8 z" class="tl-arrowhead" />
              {/if}
            {/each}
          </svg>

          <!-- rows -->
          {#each rowList as r, i (r.kind === 'item' ? r.item!.id : `g${i}`)}
            {#if r.kind === 'group'}
              <div class="tl-grouprow" style:top="{i * ROW_H}px">
                <span class="dot" style:background={r.color ?? 'var(--muted)'}></span>{r.title}
              </div>
            {:else}
              {@const it = r.item!}
              {@const b = r.bar ? shifted(r.bar) : null}
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <div
                class="tl-row"
                style:top="{i * ROW_H}px"
                data-item={it.id}
                onmouseenter={() => {
                  if (!menuOpen) menuId = it.id;
                }}
                oncontextmenu={(e) => {
                  e.preventDefault();
                  menuId = it.id;
                  menuOpen = true;
                }}
              >
                <div class="tl-name">
                  <button class="tl-open" onclick={() => void board.open(it.id)} title={it.title}>
                    {#if it.status}<span class="tl-swatch" style:background={color(it)}></span>{/if}
                    <span class="tl-name-t">{it.title}</span>
                  </button>
                  {#if menuId === it.id}<CardMenu item={it} bind:open={menuOpen} />{/if}
                </div>
                <div class="tl-track">
                  {#if b}
                    {#if b.milestone}
                      <button
                        class="tl-milestone"
                        class:dragging={drag?.id === it.id}
                        class:blocked={it.blocked}
                        style:left="{barLeft(b) + px / 2}px"
                        style:background={color(it)}
                        onpointerdown={(e) => down(e, b, 'move')}
                        onpointermove={move}
                        onpointerup={() => up(b)}
                        onclick={() => !justDragged && void board.open(it.id)}
                        title="{it.title} · due {dueLabel(it.due_at!, board.now, it.due_date_only)}"
                        aria-label="{it.title}, due {dueLabel(it.due_at!, board.now, it.due_date_only)}"
                      ></button>
                      <span class="tl-label" style:left="{barLeft(b) + px / 2 + 14}px" aria-hidden="true">{it.title}</span>
                    {:else}
                      <div
                        class="tl-barwrap"
                        style:left="{barLeft(b)}px"
                        style:width="{barWidth(b)}px"
                        style:height="{BAR_H}px"
                      >
                        <span
                          class="tl-handle start"
                          onpointerdown={(e) => down(e, b, 'start')}
                          onpointermove={move}
                          onpointerup={() => up(b)}
                          role="slider"
                          tabindex="-1"
                          aria-label="Start date"
                          aria-valuenow={0}
                        ></span>
                        <button
                          class="tl-bar"
                          class:dragging={drag?.id === it.id && drag.mode === 'move'}
                          class:blocked={it.blocked}
                          style:background={color(it)}
                          onpointerdown={(e) => down(e, b, 'move')}
                          onpointermove={move}
                          onpointerup={() => up(b)}
                          onclick={() => !justDragged && void board.open(it.id)}
                          title="{it.title} · {dueLabel(it.due_at!, board.now, it.due_date_only)}"
                        >
                          <!-- a short bar cannot hold its name: it sits to the right instead -->
                          {#if barWidth(b) >= LABEL_MIN}<span class="tl-bar-t">{it.title}</span>{/if}
                        </button>
                        <span
                          class="tl-handle end"
                          onpointerdown={(e) => down(e, b, 'end')}
                          onpointermove={move}
                          onpointerup={() => up(b)}
                          role="slider"
                          tabindex="-1"
                          aria-label="Due date"
                          aria-valuenow={0}
                        ></span>
                      </div>
                      {#if barWidth(b) < LABEL_MIN}
                        <span class="tl-label" style:left="{barLeft(b) + barWidth(b) + 8}px" aria-hidden="true">{it.title}</span>
                      {/if}
                    {/if}
                  {/if}
                  {#each ghostsByItem.get(it.id) ?? [] as g (g.occurrence_at)}
                    <span
                      class="tl-ghost"
                      data-repeat-of={g.recurrence_id}
                      style:left="{left(g.local_date)}px"
                      style:top="{(ROW_H - 18) / 2}px"
                      title="Repeats {dueLabel(g.due_at, board.now)} (not created yet)"
                    ><Icon name="repeat" size={11} /></span>
                  {/each}
                </div>
              </div>
            {/if}
          {/each}
        </div>
      </div>
    </div>

    {#if unscheduled.length}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="tl-tray"
        ondragover={(e) => {
          e.preventDefault();
          dragOverDay = dayAt(e.clientX, e.currentTarget as HTMLElement);
        }}
        ondragleave={() => (dragOverDay = null)}
        ondrop={trayDrop}
      >
        <span class="tl-tray-label muted small">No due date — drag onto the timeline to schedule{dragOverDay ? ` · ${dueLabel(dayToIso(dragOverDay), board.now)}` : ''}:</span>
        {#each unscheduled as it (it.id)}
          <button
            class="tl-chip"
            draggable="true"
            ondragstart={(e) => e.dataTransfer?.setData('text/plain', it.id)}
            onclick={() => void board.open(it.id)}
          >
            <span class="tl-swatch" style:background={color(it)}></span>{it.title}
          </button>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<style>
  .tl {
    display: flex;
    flex-direction: column;
    min-height: 0;
    flex: 1;
  }
  .tl-toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 2px 10px;
  }
  /* The zoom switch is the shared .seg control (design.css), same as the Calendar's span. */
  .seg {
    flex: none;
  }
  .tl-scroll {
    overflow: auto;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--panel);
    flex: 1;
    min-height: 0;
  }
  .tl-inner {
    position: relative;
    --gutter: 280px;
  }
  /* On a phone the task names keep a narrow column so the bars stay in view. */
  @media (max-width: 760px) {
    .tl-inner {
      --gutter: 136px;
    }
    .tl-toolbar {
      flex-wrap: wrap;
      gap: 6px 12px;
    }
  }
  .tl-head {
    display: flex;
    position: sticky;
    top: 0;
    z-index: 3;
    background: var(--raised);
    border-bottom: 1px solid var(--line);
  }
  .tl-gutter {
    width: var(--gutter);
    flex: none;
    position: sticky;
    left: 0;
    z-index: 2;
    background: var(--raised);
    border-right: 1px solid var(--line);
    padding: 8px 12px;
    font-size: var(--fs-sm);
    font-weight: 600;
    color: var(--muted);
  }
  .tl-axis {
    position: relative;
    height: 34px;
  }
  .tl-tick {
    position: absolute;
    top: 0;
    bottom: 0;
    display: flex;
    align-items: center;
    padding-left: 6px;
    border-left: 1px solid var(--line);
    font-size: 11px;
    color: var(--muted);
    white-space: nowrap;
    overflow: hidden;
  }
  .tl-tick.major {
    border-left-color: var(--text);
    color: var(--text);
    font-weight: 600;
  }
  .tl-body {
    position: relative;
  }
  .tl-grid {
    position: absolute;
    top: 0;
    bottom: 0;
    pointer-events: none;
  }
  .tl-col {
    position: absolute;
    top: 0;
    bottom: 0;
    border-left: 1px solid color-mix(in srgb, var(--line) 55%, transparent);
  }
  .tl-col.major {
    border-left-color: var(--line);
  }
  .tl-weekend {
    position: absolute;
    top: 0;
    bottom: 0;
    background: color-mix(in srgb, var(--panel) 70%, transparent);
  }
  .tl-label {
    position: absolute;
    top: 50%;
    translate: 0 -50%;
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    white-space: nowrap;
    pointer-events: none;
  }
  .tl-today {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 2px;
    background: var(--danger);
    opacity: 0.8;
  }
  .tl-arrows {
    position: absolute;
    top: 0;
    pointer-events: none;
    z-index: 1;
    overflow: visible;
  }
  .tl-arrow {
    fill: none;
    stroke: var(--muted);
    stroke-width: 1.5;
    opacity: 0.7;
  }
  .tl-arrowhead {
    fill: var(--muted);
    opacity: 0.8;
  }
  .tl-grouprow {
    position: absolute;
    left: 0;
    right: 0;
    height: 36px;
    display: flex;
    align-items: center;
    gap: 8px;
    padding-left: 12px;
    font-size: var(--fs-sm);
    font-weight: 600;
    color: var(--muted);
    background: color-mix(in srgb, var(--panel) 70%, var(--raised));
  }
  .tl-grouprow .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .tl-row {
    position: absolute;
    left: 0;
    right: 0;
    height: 36px;
    display: flex;
    align-items: center;
  }
  .tl-row:hover {
    background: color-mix(in srgb, var(--card-hover) 60%, transparent);
  }
  .tl-name {
    width: var(--gutter);
    flex: none;
    position: sticky;
    left: 0;
    z-index: 2;
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 6px 0 0;
    height: 100%;
    border-right: 1px solid var(--line);
  }
  .tl-open {
    flex: 1;
    min-width: 0;
    height: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 0 0 12px;
    border: 0;
    background: transparent;
    color: var(--text);
    text-align: left;
    font: inherit;
  }
  .tl-name :global(.card-more) {
    visibility: hidden;
  }
  .tl-row:hover .tl-name :global(.card-more),
  .tl-name :global(.card-more.on) {
    visibility: visible;
  }
  .tl-row:hover .tl-name {
    background: var(--raised);
  }
  .tl-name-t {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tl-swatch {
    width: 8px;
    height: 8px;
    border-radius: 2px;
    flex: none;
  }
  .tl-track {
    position: relative;
    flex: 1;
    height: 100%;
  }
  .tl-barwrap {
    position: absolute;
    top: 50%;
    translate: 0 -50%;
    display: flex;
    align-items: stretch;
  }
  .tl-bar {
    flex: 1;
    min-width: 0;
    border: 0;
    border-radius: 5px;
    color: #fff;
    display: flex;
    align-items: center;
    padding: 0 8px;
    cursor: grab;
    box-shadow: var(--shadow-sm, 0 1px 2px rgba(0, 0, 0, 0.2));
    /* A swipe scrolls the chart; a held finger grabs the bar (holdToDrag). */
    touch-action: manipulation;
  }
  .tl-bar.dragging {
    cursor: grabbing;
    opacity: 0.85;
  }
  .tl-bar.blocked {
    outline: 2px dashed var(--warning);
    outline-offset: 1px;
  }
  .tl-bar-t {
    font-size: 11px;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-shadow: 0 1px 1px rgba(0, 0, 0, 0.25);
  }
  .tl-handle {
    width: 7px;
    flex: none;
    cursor: ew-resize;
    touch-action: manipulation;
    border-radius: 5px;
  }
  .tl-barwrap:hover .tl-handle {
    background: rgba(255, 255, 255, 0.35);
  }
  .tl-milestone {
    position: absolute;
    top: 50%;
    width: 16px;
    height: 16px;
    translate: -50% -50%;
    rotate: 45deg;
    border: 0;
    border-radius: 3px;
    background: var(--accent);
    cursor: grab;
    touch-action: manipulation;
    box-shadow: var(--shadow-sm, 0 1px 2px rgba(0, 0, 0, 0.2));
  }
  .tl-milestone.dragging {
    cursor: grabbing;
    opacity: 0.85;
  }
  .tl-milestone.blocked {
    outline: 2px dashed var(--warning);
    outline-offset: 2px;
  }
  .tl-tray {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    padding: 10px 2px 2px;
  }
  .tl-tray-label {
    flex-basis: 100%;
  }
  .tl-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px;
    border: 1px solid var(--line);
    border-radius: var(--radius-pill);
    background: var(--panel);
    color: var(--text);
    font-size: var(--fs-sm);
    cursor: grab;
    max-width: 240px;
  }
  .tl-chip .tl-swatch {
    border-radius: 50%;
  }
  .pad-row {
    padding: 24px;
  }
</style>
