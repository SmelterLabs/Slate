<script lang="ts">
  import { LEAVES } from '../lib/words';
  import { priorityLong } from '../lib/priority';
  // Shown while items are selected: one change to all of them through POST /items/bulk.
  // Each action is an icon button that opens a small menu (the card menu's look), so the bar
  // stays one tidy row; Archive and Delete ask once before they act.
  import { board } from '../lib/store.svelte';
  import type { StatusKind } from '../lib/types';
  import { KINDS, KIND_NAMES, PRIORITIES, quickDays } from '../lib/views';
  import Dropdown from './Dropdown.svelte';
  import MovePicker from './MovePicker.svelte';
  import Icon from './Icon.svelte';

  const ids = $derived([...board.selected].filter((id) => board.items[id]));
  const scope = $derived(board.selection);
  const list = $derived(scope.kind === 'list' ? board.lists[scope.id] : undefined);
  const days = $derived(quickDays(board.now));
  const people = $derived([...board.pickable].sort((a, b) => a.display_name.localeCompare(b.display_name)));
  const tags = $derived([...new Set(ids.flatMap((id) => board.items[id]?.tags ?? []))].sort());
  const noun = $derived(ids.length === 1 ? 'task' : 'tasks');

  // The picker marks a list as "here now" only when every selected task is in it.
  const sameList = $derived.by(() => {
    const at = new Set(ids.map((id) => board.items[id]?.list_id));
    return at.size === 1 ? ([...at][0] ?? null) : null;
  });

  let tag = $state('');
  let confirm = $state<'archive' | 'delete' | null>(null);
  let open = $state({ status: false, due: false, prio: false, tags: false, assign: false, move: false });
  let dateEl: HTMLInputElement | undefined = $state();

  function closeAll() {
    for (const k of Object.keys(open) as (keyof typeof open)[]) open[k] = false;
  }
  async function run(changes: Record<string, unknown>) {
    closeAll();
    if ((await board.bulk(ids, changes)) && changes.archive) board.clearSelection();
  }
  // Subtasks live in their parent's list and move with it, so a subtask whose parent is not
  // selected stays put.
  async function move(listId: string) {
    closeAll();
    const picked = new Set(ids);
    const send = ids.filter((id) => {
      const p = board.items[id]?.parent_id;
      return !p || picked.has(p);
    });
    const left = ids.length - send.length;
    if (send.length) await board.bulk(send, { list_id: listId });
    if (left) board.flash(`${left} subtask${left === 1 ? '' : 's'} stayed with ${left === 1 ? 'its parent' : 'their parents'}`);
  }
  function pickDate() {
    if (!dateEl) return;
    try {
      dateEl.showPicker();
    } catch {
      dateEl.focus();
    }
  }
  async function archive() {
    confirm = null;
    await run({ archive: true });
  }
  async function remove() {
    confirm = null;
    if (await board.trashMany(ids)) board.clearSelection();
  }
</script>

{#if ids.length}
  <div class="bulk-bar" role="region" aria-label="Bulk edit">
    <span class="bb-count">
      <strong>{ids.length} selected</strong>
      <button class="bb-x" title="Clear selection (Esc)" aria-label="Clear selection" onclick={() => board.clearSelection()}
        ><Icon name="x" size={14} /></button
      >
    </span>
    <span class="bb-sep" aria-hidden="true"></span>

    <div class="bb-group">
      <Dropdown title="Set status" cls="bb-btn" bind:open={open.status}>
        {#snippet label()}<Icon name="status" size={14} /><span class="bb-text">Status</span>{/snippet}
        <div class="menu card-menu" role="menu">
          {#if list}
            {#each list.statuses as s (s.id)}
              <button role="menuitem" onclick={() => run({ status: s.id })}
                ><span class="bb-dot" style:background={s.color}></span>{s.name}</button
              >
            {/each}
          {:else}
            {#each KINDS as k (k)}
              <button
                role="menuitem"
                onclick={() => {
                  closeAll();
                  void board.bulkKind(ids, k as StatusKind);
                }}>{KIND_NAMES[k]}</button
              >
            {/each}
          {/if}
        </div>
      </Dropdown>

      <Dropdown title="Set due date" cls="bb-btn" bind:open={open.due}>
        {#snippet label()}<Icon name="calendar" size={14} /><span class="bb-text">Due</span>{/snippet}
        <div class="menu card-menu" role="menu">
          <div class="menu-row chips">
            <button onclick={() => run({ due_at: days.today })}>Today</button>
            <button onclick={() => run({ due_at: days.tomorrow })}>Tomorrow</button>
          </div>
          <div class="menu-row chips">
            <button onclick={() => run({ due_at: days.nextWeek })}>Next Mon</button>
            <span class="menu-pick">
              <button title="Pick a date" aria-label="Pick a date" onclick={pickDate}
                ><Icon name="calendar" size={13} />Date</button
              >
              <input
                bind:this={dateEl}
                type="date"
                tabindex="-1"
                aria-label="Due on date"
                onchange={(e) => {
                  const v = e.currentTarget.value;
                  e.currentTarget.value = '';
                  if (v) void run({ due_at: v });
                }}
              />
            </span>
          </div>
          <button role="menuitem" onclick={() => run({ due_at: null })}><Icon name="x" /> Clear date</button>
        </div>
      </Dropdown>

      <Dropdown title="Set priority" cls="bb-btn" bind:open={open.prio}>
        {#snippet label()}<Icon name="flag" size={14} /><span class="bb-text">Priority</span>{/snippet}
        <div class="menu card-menu" role="menu">
          <div class="menu-row chips prio-row">
            {#each PRIORITIES as p (p)}
              <button class="prio-flag-{p}" title={priorityLong(p)} aria-label="Priority {priorityLong(p)}" onclick={() => run({ priority: p })}
                ><Icon name="flag" fill /></button
              >
            {/each}
          </div>
          <button role="menuitem" onclick={() => run({ priority: null })}><Icon name="x" /> No priority</button>
        </div>
      </Dropdown>

      <Dropdown title="Tags" cls="bb-btn" bind:open={open.tags}>
        {#snippet label()}<Icon name="tag" size={14} /><span class="bb-text">Tags</span>{/snippet}
        <div class="menu card-menu" role="menu">
          <form
            class="bb-tag-form"
            onsubmit={(e) => {
              e.preventDefault();
              const t = tag.trim().replace(/^#/, '');
              if (t) void run({ add_tags: [t] }).then(() => (tag = ''));
            }}
          >
            <input aria-label="Add tag" placeholder="Add a tag, Enter" bind:value={tag} />
          </form>
          {#if tags.length}
            <div class="menu-label">Remove</div>
            {#each tags as t (t)}
              <button role="menuitem" aria-label="Remove tag {t}" onclick={() => run({ remove_tags: [t] })}
                >#{t}<Icon name="x" size={13} /></button
              >
            {/each}
          {/if}
        </div>
      </Dropdown>

      <Dropdown title="Assign" cls="bb-btn" bind:open={open.assign}>
        {#snippet label()}<Icon name="user" size={14} /><span class="bb-text">Assign</span>{/snippet}
        <div class="menu card-menu bb-scroll" role="menu">
          {#each people as a (a.id)}
            <button role="menuitem" onclick={() => run({ assignees: [a.handle] })}
              ><Icon name={a.kind === 'agent' ? 'robot' : 'user'} /> {a.display_name}</button
            >
          {/each}
          <button role="menuitem" onclick={() => run({ assignees: [] })}><Icon name="x" /> Nobody</button>
        </div>
      </Dropdown>

      <Dropdown title="Move to list" cls="bb-btn" bind:open={open.move}>
        {#snippet label()}<Icon name="folder" size={14} /><span class="bb-text">Move</span>{/snippet}
        <div class="menu card-menu" role="menu">
          <MovePicker current={sameList} onpick={move} />
        </div>
      </Dropdown>
    </div>

    <span class="bb-sep" aria-hidden="true"></span>

    {#if confirm}
      <span class="bb-confirm" role="group" aria-label="Confirm">
        <span
          >{confirm === 'delete' ? 'Trash' : 'Put away'} {ids.length} {noun}? {confirm === 'delete'
            ? LEAVES.trash
            : LEAVES.putAway}</span
        >
        <button class="bb-btn danger solid" onclick={confirm === 'delete' ? remove : archive}
          >{confirm === 'delete' ? 'Trash' : 'Put away'}</button
        >
        <button class="bb-btn" onclick={() => (confirm = null)}>Cancel</button>
      </span>
    {:else}
      <div class="bb-group">
        <button class="bb-btn" title="Put away: {LEAVES.putAway}" aria-label="Put away" onclick={() => (confirm = 'archive')}
          ><Icon name="archive" size={14} /><span class="bb-text">Put away</span></button
        >
        <button
          class="bb-btn danger"
          title="Trash: {LEAVES.trash}"
          aria-label="Trash"
          onclick={() => (confirm = 'delete')}><Icon name="trash" size={14} /><span class="bb-text">Trash</span></button
        >
      </div>
    {/if}
  </div>
{/if}
