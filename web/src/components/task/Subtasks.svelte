<script lang="ts">
  // Subtasks as ClickUp shows them: a small table with a completion circle, status, assignees,
  // due date and priority, a progress count, and an inline row to add one (one POST each).
  import { statusColor } from '../../lib/activity';
  import { priorityLong } from '../../lib/priority';
  import { draftKey } from '../../lib/drafts';
  import { persisted } from '../../lib/drafts.svelte';
  import { dueLabel, isOverdue } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import type { ItemDetail } from '../../lib/types';
  import Avatars from '../Avatars.svelte';
  import Icon from './Icon.svelte';

  let { item, adding = $bindable(false), canAdd = true }: { item: ItemDetail; adding?: boolean; canAdd?: boolean } = $props();

  const draft = persisted(() => draftKey.subtask(item.id));
  let input = $state<HTMLInputElement>();
  const subs = $derived(item.subtasks);
  const closed = $derived(subs.filter((s) => s.status?.kind === 'done' || s.status?.kind === 'cancelled').length);
  let hideClosed = $state(false);
  const shown = $derived(hideClosed ? subs.filter((s) => s.status?.kind !== 'done' && s.status?.kind !== 'cancelled') : subs);

  $effect(() => {
    if (adding) queueMicrotask(() => input?.focus());
  });

  async function add(e: SubmitEvent) {
    e.preventDefault();
    const t = draft.value.trim();
    if (!t) return;
    draft.value = '';
    await board.addSubtask(item.id, t);
    input?.focus();
  }
</script>

{#if subs.length || adding || draft.value}
  <section class="block" aria-label="Subtasks">
    <header>
      <h3><Icon name="subtask" /> Subtasks</h3>
      {#if subs.length}
        <span class="progress" title="{closed} of {subs.length} closed">
          <span class="bar"><span style="width: {(100 * closed) / subs.length}%"></span></span>
          {closed}/{subs.length}
        </span>
        {#if closed}
          <button class="link small" onclick={() => (hideClosed = !hideClosed)}>{hideClosed ? 'Show closed' : 'Hide closed'}</button>
        {/if}
      {/if}
    </header>
    <ul class="rows">
      {#each shown as sub (sub.id)}
        {@const done = sub.status?.kind === 'done'}
        <li class:done>
          <button
            class="circle"
            class:on={done}
            aria-label={done ? `Reopen ${sub.title}` : `Complete ${sub.title}`}
            onclick={() => board.toggleComplete(sub.id).then(() => board.refreshDetail())}
            ><Icon name="check" size={11} /></button
          >
          <span class="sdot" style="background: {statusColor(sub.status)}" title={sub.status?.name}></span>
          <button class="title" onclick={() => board.open(sub.id)}>{sub.title}</button>
          {#if sub.subtask_count}<span class="nested" title="{sub.subtask_count} subtasks of its own (open it)"><Icon name="subtask" size={11} /> {sub.subtask_count}</span>{/if}
          <span class="meta">
            <Avatars people={sub.assignees} />
            {#if sub.due_at}
              <span class="due" class:overdue={isOverdue(sub, board.now)}>{dueLabel(sub.due_at, board.now, sub.due_date_only)}</span>
            {/if}
            {#if sub.priority}
              <span class="flag" style="color: var(--prio-{sub.priority})" title="{priorityLong(sub.priority)}"
                ><Icon name="flag" size={13} /></span
              >
            {/if}
          </span>
        </li>
      {/each}
    </ul>
    {#if canAdd}
    <form onsubmit={add} class="add">
      <Icon name="plus" size={14} />
      <input
        bind:this={input}
        aria-label="Add subtask"
        placeholder="Add a subtask, Enter to save"
        bind:value={draft.value}
        onblur={() => !draft.value && (adding = false)}
        onkeydown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            draft.value = '';
            input?.blur();
          }
        }}
      />
    </form>
    {/if}
  </section>
{/if}

<style>
  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--line);
    border-radius: 8px;
    overflow: hidden;
  }
  .rows:empty {
    display: none;
  }
  li {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    border-top: 1px solid var(--line);
    font-size: 13.5px;
  }
  li:first-child {
    border-top: 0;
  }
  li:hover {
    background: var(--card-hover);
  }
  .nested {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    color: var(--muted);
    font-size: 12px;
  }
  li.done .title {
    color: var(--muted);
    text-decoration: line-through;
  }
  .circle {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 1.5px solid var(--muted);
    background: none;
    padding: 0;
    color: transparent;
    display: inline-grid;
    place-items: center;
    flex: none;
  }
  .circle:hover {
    border-color: var(--kind-done);
    color: var(--kind-done);
  }
  .circle.on {
    background: var(--kind-done);
    border-color: var(--kind-done);
    color: #fff;
  }
  .sdot {
    width: 8px;
    height: 8px;
    border-radius: 2px;
    flex: none;
  }
  .title {
    flex: 1;
    min-width: 0;
    text-align: left;
    border: 0;
    background: none;
    padding: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title:hover {
    color: var(--accent);
  }
  .meta {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 12px;
  }
  .due.overdue {
    color: var(--overdue);
  }
  .flag {
    display: inline-flex;
  }
  .add {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 6px;
    color: var(--muted);
    padding-left: 10px;
  }
  .add input {
    flex: 1;
    border: 0;
    background: none;
    padding: 6px 0;
  }
  .add input:focus {
    outline: none;
  }
</style>
