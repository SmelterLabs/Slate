<script lang="ts">
  // Calendar layout (ClickUp's Calendar view): the view's items on a month grid or one week, by
  // workspace due day; work with a start date stretches across its days. Drag a chip to another
  // day to reschedule (keeping its time of day), or onto "Unscheduled" to clear the date. "+" on
  // a day files an item due that day. Paging is local; Month/Week is saved with the view.
  import { calendarSpan, pageDays, pageTitle, placeItems, placeRepeats, shiftPage } from '../lib/calendar';
  import { workspaceDay, parseQuickAdd } from '../lib/quickadd';
  import { board } from '../lib/store.svelte';
  import type { Item, UpcomingRepeat } from '../lib/types';
  import CardMenu from './CardMenu.svelte';
  import Icon from './Icon.svelte';
  import { dayOf, isClosed, weekday, type Group, type Scope, type ViewConfig } from '../lib/views';

  let { groups, config, scope, viewKey }: { groups: Group[]; config: ViewConfig; scope: Scope; viewKey: string } =
    $props();

  const span = $derived(calendarSpan(config));
  const today = $derived(workspaceDay(board.now));
  let anchor = $state(workspaceDay(new Date()));
  const days = $derived(pageDays(anchor, span));
  const month = $derived(anchor.slice(0, 7));

  // One copy of each item, whatever the grouping put it under.
  const items = $derived.by(() => {
    const seen = new Map<string, Item>();
    for (const g of groups) for (const i of g.items) seen.set(i.id, i);
    return [...seen.values()];
  });
  const placed = $derived(placeItems(items, days));

  // Dates recurring rules will fill that are not tasks yet: drawn dashed, from today on.
  let repeats = $state<UpcomingRepeat[]>([]);
  $effect(() => {
    void board.recurRev;
    const first = days[0] < today ? today : days[0];
    const last = days[days.length - 1];
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
  const ghosts = $derived(placeRepeats(repeats, scope, (id) => board.lists[id]?.section_id));

  function openRule(r: UpcomingRepeat) {
    // The rule's most recent task carries its editor.
    const latest = Object.values(board.items)
      .filter((i) => i.recurrence_id === r.recurrence_id)
      .sort((a, b) => (b.due_at ?? '').localeCompare(a.due_at ?? ''))[0];
    if (latest) board.open(latest.id);
  }

  const PER_DAY = 4;
  let expanded = $state<Record<string, boolean>>({});
  let dragOver = $state<string | null>(null);
  let addingOn = $state<string | null>(null);
  let addText = $state('');
  let showUnscheduled = $state(true);

  // Right-click a chip: the same task menu as board cards and list rows, at the pointer.
  let menu = $state<{ item: Item; x: number; y: number } | null>(null);
  let menuOpen = $state(false);
  $effect(() => {
    if (!menuOpen) menu = null;
  });
  function openMenu(e: MouseEvent, item: Item) {
    e.preventDefault();
    menu = { item, x: e.clientX, y: e.clientY };
    menuOpen = true;
  }

  const weekdays = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  function drop(e: DragEvent, day: string | null) {
    e.preventDefault();
    dragOver = null;
    const id = e.dataTransfer?.getData('text/plain');
    if (!id) return;
    const it = board.items[id];
    if (it && day && it.due_at && dayOf(it.due_at) === day) return;
    void board.reschedule(id, day);
  }

  async function add(e: SubmitEvent, day: string) {
    e.preventDefault();
    const parsed = parseQuickAdd(addText);
    const listId = board.quickAddList(viewKey, scope);
    if (!parsed.title || !listId) return;
    if (await board.quickAdd(listId, { ...parsed, due: parsed.due ?? day })) {
      addText = '';
      addingOn = null;
    }
  }

  function chipColor(i: Item): string {
    return i.status?.color ?? `var(--kind-${i.status?.kind ?? 'open'})`;
  }
</script>

<div class="calendar" class:week={span === 'week'}>
  <div class="cal-head">
    <div class="seg" role="group" aria-label="Calendar span">
      <button class:on={span === 'month'} onclick={() => board.setConfig({ calendar_span: 'month' })}>Month</button>
      <button class:on={span === 'week'} onclick={() => board.setConfig({ calendar_span: 'week' })}>Week</button>
    </div>
    <button class="icon" aria-label="Previous" onclick={() => (anchor = shiftPage(anchor, span, -1))}>‹</button>
    <button class="chip" onclick={() => (anchor = today)}>Today</button>
    <button class="icon" aria-label="Next" onclick={() => (anchor = shiftPage(anchor, span, 1))}>›</button>
    <h3 class="cal-title">{pageTitle(anchor, span)}</h3>
    <button class="link small" aria-pressed={showUnscheduled} onclick={() => (showUnscheduled = !showUnscheduled)}
      >Unscheduled ({placed.unscheduled.length})</button
    >
  </div>

  <div class="cal-body">
    <div class="cal-grid" role="grid" aria-label="Calendar">
      {#each weekdays as w (w)}<div class="cal-wd muted small" role="columnheader">{w}</div>{/each}
      {#each days as day (day)}
        {@const list = placed.byDay.get(day) ?? []}
        {@const limit = expanded[day] || span === 'week' ? list.length : PER_DAY}
        <div
          class="cal-day"
          class:today={day === today}
          class:past={day < today}
          class:other={span === 'month' && day.slice(0, 7) !== month}
          class:weekend={weekday(day) === 0 || weekday(day) === 6}
          class:over={dragOver === day}
          role="gridcell"
          tabindex="-1"
          data-day={day}
          ondragover={(e) => {
            e.preventDefault();
            dragOver = day;
          }}
          ondragleave={() => (dragOver = null)}
          ondrop={(e) => drop(e, day)}
        >
          <div class="cal-date">
            <span class="cal-num">{Number(day.slice(8))}</span>
            <button
              class="icon small cal-add"
              aria-label="Add item on {day}"
              onclick={() => ((addingOn = addingOn === day ? null : day), (addText = ''))}>+</button
            >
          </div>
          {#if addingOn === day}
            <form class="cal-add-form" onsubmit={(e) => add(e, day)}>
              <!-- svelte-ignore a11y_autofocus -->
              <input
                aria-label="New item on {day}"
                placeholder="Title #tag !high @who"
                autofocus
                bind:value={addText}
                onkeydown={(e) => {
                  if (e.key === 'Escape') {
                    e.stopPropagation();
                    addingOn = null;
                  }
                }}
              />
            </form>
          {/if}
          {#each list.slice(0, limit) as i (i.id)}
            {@const cont = placed.spans.has(`${day}|${i.id}`)}
            <!-- a span runs from its start day to its due day: `cont` marks every day but the last -->
            {@const first = i.start_at ? dayOf(i.start_at) === day : !cont}
            {@const last = !cont}
            <button
              class="cal-chip prio-stripe-{i.priority ?? 'none'}"
              class:done={isClosed(i)}
              class:overdue={!isClosed(i) && day < today && !cont}
              class:cont
              class:span-start={first && !last}
              class:span-mid={!first && !last}
              class:span-end={!first && last}
              draggable="true"
              data-item={i.id}
              title={i.title}
              style:--opt={chipColor(i)}
              ondragstart={(e) => {
                e.dataTransfer?.setData('text/plain', i.id);
                if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
              }}
              onclick={() => board.open(i.id)}
              oncontextmenu={(e) => openMenu(e, i)}
            >
              <span class="dot" style:background={chipColor(i)}></span>
              <!-- a span repeats its title at the start of each week row, not on every day -->
              <span class="cal-chip-title" class:quiet={!first && weekday(day) !== 0}>{i.title}</span>
              {#if i.recurrence_id}<span class="muted">↻</span>{/if}
            </button>
          {/each}
          {#if list.length > limit}
            <button class="link small" onclick={() => (expanded[day] = true)}>+{list.length - limit} more</button>
          {/if}
          {#each ghosts.get(day) ?? [] as r (r.recurrence_id + r.occurrence_at)}
            <button
              class="cal-chip ghost"
              data-repeat-of={r.recurrence_id}
              title="{r.title}: repeats here (not created yet)"
              onclick={() => openRule(r)}
            >
              <Icon name="repeat" size={12} />
              <span class="cal-chip-title">{r.title}</span>
            </button>
          {/each}
        </div>
      {/each}
    </div>

    {#if showUnscheduled}
      <aside
        class="cal-side"
        class:over={dragOver === 'none'}
        aria-label="Unscheduled"
        ondragover={(e) => {
          e.preventDefault();
          dragOver = 'none';
        }}
        ondragleave={() => (dragOver = null)}
        ondrop={(e) => drop(e, null)}
      >
        <h4>Unscheduled <span class="count">{placed.unscheduled.length}</span></h4>
        <p class="muted small">Drag onto a day to schedule; drag here to clear a date.</p>
        {#each placed.unscheduled.slice(0, 200) as i (i.id)}
          <button
            class="cal-chip"
            class:done={isClosed(i)}
            draggable="true"
            data-item={i.id}
            style:--opt={chipColor(i)}
            ondragstart={(e) => {
              e.dataTransfer?.setData('text/plain', i.id);
              if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
            }}
            onclick={() => board.open(i.id)}
            oncontextmenu={(e) => openMenu(e, i)}
          >
            <span class="dot" style:background={chipColor(i)}></span>
            <span class="cal-chip-title">{i.title}</span>
          </button>
        {/each}
      </aside>
    {/if}
  </div>
  {#if menu}
    <div class="cal-point" style:left="{menu.x}px" style:top="{menu.y}px">
      {#key menu}<CardMenu item={menu.item} bind:open={menuOpen} />{/key}
    </div>
  {/if}
</div>
