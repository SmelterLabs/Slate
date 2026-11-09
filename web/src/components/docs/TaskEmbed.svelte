<script lang="ts">
  // A task embedded in a doc (`[[task:<id>]]` on its own line): a live card. It reads the
  // board's copy of the task, so a status change anywhere shows here at once. Tick it off, or
  // open the task window. A task the board does not hold (archived) falls back to the copy the
  // page came with.
  import { statusColor } from '../../lib/activity';
  import { priorityLong } from '../../lib/priority';
  import { docs } from '../../lib/docs.svelte';
  import { dueLabel, isOpen, isOverdue } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import Avatars from '../Avatars.svelte';
  import AgentBadge from '../AgentBadge.svelte';
  import Icon from '../Icon.svelte';

  let { id }: { id: string } = $props();
  const item = $derived(board.items[id] ?? docs.current?.tasks.find((t) => t.id === id));
  const open = $derived(item ? isOpen(item) : true);
  const late = $derived(item ? isOverdue(item, board.now) : false);
  const done = $derived(item?.status?.kind === 'done');
</script>

{#if item}
  <div class="task-embed" class:closed={!open} data-item={item.id} contenteditable="false">
    <button
      class="work-check"
      class:done={!open}
      aria-label={open ? `Complete ${item.title}` : `Reopen ${item.title}`}
      onclick={() => void board.toggleComplete(item.id)}
    ></button>
    <button class="te-main" onclick={() => board.open(item.id)} title="Open the task">
      <span class="te-title">
        {#if item.priority && item.priority !== 'normal'}
          <span class="work-prio prio-{item.priority}" title="{priorityLong(item.priority)}"></span>
        {/if}
        <span class="t" class:struck={done}>{item.title}</span>
      </span>
      <span class="te-meta">
        <span class="te-status" style="--st:{statusColor(item.status)}">{item.status?.name ?? 'No status'}</span>
        <span class="te-list"><Icon name="list" size={12} />{board.lists[item.list_id]?.name ?? 'List'}</span>
        {#if item.due_at}<span class="te-due" class:late>{dueLabel(item.due_at, board.now, item.due_date_only)}</span>{/if}
        {#if item.subtask_count}<span class="te-sub"><Icon name="subtask" size={12} />{item.subtask_count}</span>{/if}
      </span>
    </button>
    <span class="te-side">
      {#if item.agent}<AgentBadge flag={item.agent} />{/if}
      <Avatars people={item.assignees} />
    </span>
  </div>
{:else}
  <div class="task-embed missing" contenteditable="false">
    <Icon name="alert" size={14} /> This task is gone (deleted or in the trash).
  </div>
{/if}
