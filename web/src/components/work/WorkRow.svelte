<script lang="ts">
  import { blockedTitle, dueLabel, isOpen, isOverdue, openBlockers } from '../../lib/format';
  import { priorityLong } from '../../lib/priority';
  import { board } from '../../lib/store.svelte';
  import { swipe } from '../../lib/swipe';
  import type { Item } from '../../lib/types';
  import Avatars from '../Avatars.svelte';
  import Icon from '../Icon.svelte';
  import TagPill from '../TagPill.svelte';

  // One task on My Work: tick it off, open it, see where it lives and when it is due. A finger
  // swipes it right to complete or left to snooze to tomorrow (lib/swipe.ts).
  let {
    item,
    showPeople = false,
    showBlockers = false,
  }: { item: Item; showPeople?: boolean; showBlockers?: boolean } = $props();
  const open = $derived(isOpen(item));
  const waitsOn = $derived(openBlockers(item, board.items));
  const late = $derived(isOverdue(item, board.now));
</script>

<div
  class="work-row"
  class:closed={!open}
  data-item={item.id}
  role="listitem"
  use:swipe={{
    right: { label: open ? 'Complete' : 'Reopen', tone: 'done', run: () => void board.toggleComplete(item.id) },
    left: open ? { label: 'Tomorrow', tone: 'snooze', run: () => void board.snooze(item.id) } : undefined,
  }}
>
  <button
    class="work-check"
    class:done={!open}
    aria-label={open ? `Complete ${item.title}` : `Reopen ${item.title}`}
    onclick={() => void board.toggleComplete(item.id)}
  ></button>
  <button
    class="work-title"
    onclick={() => board.open(item.id)}
    onmouseenter={() => (board.focusId = item.id)}
  >
    {#if item.priority && item.priority !== 'normal'}
      <span class="work-prio prio-{item.priority}" title="{priorityLong(item.priority)}"></span>
    {/if}
    <span class="t">{item.title}</span>
    {#if showBlockers && waitsOn.length}
      <span class="work-list">depends on {waitsOn.map((b) => b.title).join(', ')}</span>
    {:else}
      <span class="work-list">{board.lists[item.list_id]?.name ?? ''}</span>
    {/if}
  </button>
  {#if open && item.blocked && !showBlockers}
    <span class="blocked-badge work-blocked" title={blockedTitle(waitsOn)}><Icon name="alert" size={12} /> Depends on</span>
  {/if}
  {#if item.tags.length}
    <span class="work-tags">
      {#each item.tags.slice(0, 2) as t (t)}<TagPill name={t} />{/each}
    </span>
  {/if}
  {#if showPeople && item.assignees.length}<Avatars people={item.assignees} />{/if}
  {#if item.due_at}
    <span class="work-due" class:late>{dueLabel(item.due_at, board.now, item.due_date_only)}</span>
  {/if}
</div>
