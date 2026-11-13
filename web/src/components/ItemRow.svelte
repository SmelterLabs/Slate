<script lang="ts">
  import { dueLabel, isOverdue } from '../lib/format';
  import { priorityLabel } from '../lib/priority';
  import { rowHandlers } from '../lib/itemrow';
  import { board } from '../lib/store.svelte';
  import { swipe } from '../lib/swipe';
  import type { Item } from '../lib/types';
  import { daysLate, isClosed, type ViewConfig } from '../lib/views';
  import AgentBadge from './AgentBadge.svelte';
  import Avatars from './Avatars.svelte';
  import CardMenu from './CardMenu.svelte';
  import Icon from './Icon.svelte';
  import QuickActions from './QuickActions.svelte';
  import TagPill from './TagPill.svelte';

  let {
    item,
    config,
    showList = false,
    noStatus = false,
    slotBefore = false,
  }: { item: Item; config: ViewConfig; showList?: boolean; noStatus?: boolean; slotBefore?: boolean } = $props();

  const overdue = $derived(isOverdue(item, board.now));
  const late = $derived(daysLate(item, board.now));
  const closed = $derived(isClosed(item));
  const show = $derived(new Set(config.card_fields));
  const selected = $derived(board.selected.has(item.id));
  // Quick actions mount on first hover or focus: hundreds of cards stay cheap to render.
  let armed = $state(false);
  let menuOpen = $state(false);
</script>

<div
  class="row prio-stripe-{item.priority ?? 'none'}"
  class:closed
  class:selected
  class:slot-before={slotBefore}
  class:compact={config.density === 'compact'}
  class:wide={showList}
  class:no-status={noStatus}
  draggable="true"
  use:swipe={{
    right: { label: closed ? 'Reopen' : 'Complete', tone: 'done', run: () => void board.toggleComplete(item.id) },
    left: closed ? undefined : { label: 'Tomorrow', tone: 'snooze', run: () => void board.snooze(item.id) },
  }}
  role="row"
  tabindex="0"
  data-item={item.id}
  {...rowHandlers(item.id, { arm: () => (armed = true), menu: () => (menuOpen = true) })}
>
  <span class="cell c-title" role="cell">
    <input
      class="select"
      type="checkbox"
      aria-label="Select"
      checked={selected}
      onclick={(e) => {
        e.stopPropagation();
        board.toggleSelect(item.id, e.shiftKey);
        // Let the click tick the box, then match it to the selection (a shift-range can differ).
        e.currentTarget.checked = board.selected.has(item.id);
      }}
    />
    <button
      class="check"
      class:done={closed}
      aria-label={closed ? 'Mark not done' : 'Complete'}
      onclick={(e) => {
        e.stopPropagation();
        void board.toggleComplete(item.id);
      }}><Icon name="check" size={11} /></button
    >
    <span class="row-title">{item.title}</span>
    {#if item.recurrence_id && show.has('recurrence')}<span class="badge" title="Recurring"><Icon name="repeat" size={12} /></span>{/if}
    {#if item.subtask_count && show.has('subtasks')}<span class="badge" title="Subtasks"><Icon name="subtask" size={12} /> {item.subtask_count}</span>{/if}
    {#if item.agent}<AgentBadge flag={item.agent} {item} />{/if}
    {#if armed}<QuickActions {item} /><CardMenu {item} bind:open={menuOpen} />{/if}
  </span>
  {#if showList}<span class="cell c-list muted" role="cell">{board.lists[item.list_id]?.name}</span>{/if}
  {#if !noStatus}<span class="cell c-status" role="cell"><span class="status-pill">{item.status?.name ?? '—'}</span></span>{/if}
  <span class="cell c-due" role="cell">
    {#if item.due_at}<span class="due" class:overdue>{dueLabel(item.due_at, board.now, item.due_date_only)}</span>{/if}
    {#if late > 0}<span class="late">{late}d</span>{/if}
  </span>
  <span class="cell c-prio prio-{item.priority ?? 'none'}" role="cell">{item.priority ? priorityLabel(item.priority) : ''}</span>
  <span class="cell c-who" role="cell"><Avatars people={item.assignees} /></span>
  <span class="cell c-tags" role="cell">{#each item.tags as t (t)}<TagPill name={t} />{/each}</span>
</div>
