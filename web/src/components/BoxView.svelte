<script lang="ts">
  // Box view (layout 'box'), ClickUp-style: a box per person and agent, and one for tasks
  // nobody has. Each box heads with open, in progress, overdue and done-in-7-days counts, a bar
  // of how much is done, and the open load against this week's capacity (red when over). Its
  // tasks follow, in progress first. Drag a task onto another box to hand it over, or use
  // "Assign to" on it, which does the same on every screen. The gear on a box sets that
  // account's capacity (yours, or anyone's as an owner or admin). Numbers: lib/box.ts.
  import { buildBoxes, doneShare, reassigned, DONE_DAYS, type Box } from '../lib/box';
  import { workspaceDay } from '../lib/quickadd';
  import { dueLabel, isOverdue } from '../lib/format';
  import { board } from '../lib/store.svelte';
  import { inScope, type Group, type Scope, type ViewConfig } from '../lib/views';
  import { fmtLoad } from '../lib/workload';
  import CapacityEditor from './CapacityEditor.svelte';
  import Icon from './Icon.svelte';

  let { groups, config, scope }: { groups: Group[]; config: ViewConfig; scope: Scope } = $props();

  const accounts = $derived(
    [...board.pickable].sort((a, b) =>
      a.kind === b.kind ? a.display_name.localeCompare(b.display_name) : a.kind === 'person' ? -1 : 1,
    ),
  );
  const boxes = $derived.by(() => {
    const all = buildBoxes({
      items: groups.flatMap((g) => g.items),
      scoped: Object.values(board.items).filter((i) => inScope(i, scope, board.lists)),
      accounts,
      capacities: board.capacities,
      now: board.now,
      today: workspaceDay(board.now),
    });
    return config.hide_empty_groups ? all.filter((b) => b.items.length || b.done) : all;
  });

  let over = $state<string | null>(null);
  let assigning = $state<string | null>(null); // `${itemId}|${boxKey}`
  let folded = $state<Record<string, boolean>>({});
  let sizing = $state<string | null>(null); // the account whose capacity editor is open
  const SHOWN = 8;
  let more = $state<Record<string, boolean>>({});

  const canEdit = (listId: string) => (board.levelOn(listId) ?? 'edit') === 'edit';
  const canSize = (accountId: string) => board.canManage || board.me?.account.id === accountId;

  async function move(itemId: string, from: string, to: string) {
    const item = board.items[itemId];
    if (!item || from === to) return;
    const target = to === 'none' ? null : (board.accounts[to] ?? null);
    await board.update(itemId, { assignees: reassigned(item, from, target) });
  }

  function drop(e: DragEvent, box: Box) {
    e.preventDefault();
    over = null;
    const [itemId, from] = (e.dataTransfer?.getData('application/x-slate-box') ?? '').split('|');
    if (itemId) void move(itemId, from, box.key);
  }

  function drag(e: DragEvent, itemId: string, from: string) {
    e.dataTransfer?.setData('application/x-slate-box', `${itemId}|${from}`);
    e.dataTransfer?.setData('text/plain', itemId);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  }

  function loadText(box: Box): string {
    const parts: string[] = [];
    if (box.minutes || box.capacityMinutes)
      parts.push(box.capacityMinutes !== null ? `${fmtLoad(box.minutes, 'estimate')} of ${fmtLoad(box.capacityMinutes, 'estimate')}` : fmtLoad(box.minutes, 'estimate'));
    return parts.join(' · ');
  }
</script>

<div class="box-view" aria-label="Boxes by assignee">
  {#each boxes as box (box.key)}
    {@const name = box.account?.display_name ?? 'Unassigned'}
    {@const shown = more[box.key] ? box.items : box.items.slice(0, SHOWN)}
    <section
      class="box"
      class:over={box.over}
      class:drop={over === box.key}
      aria-label={name}
      data-box={box.account?.handle ?? 'none'}
      ondragover={(e) => {
        e.preventDefault();
        over = box.key;
      }}
      ondragleave={() => (over = null)}
      ondrop={(e) => drop(e, box)}
    >
      <header class="box-head">
        <span class="box-avatar" class:agent={box.account?.kind === 'agent'} class:nobody={!box.account} aria-hidden="true"
          >{#if box.account?.kind === 'agent'}<Icon name="robot" size={14} />{:else if box.account}{name.slice(0, 1)}{:else}<Icon
              name="user"
              size={14}
            />{/if}</span
        >
        <span class="box-name">
          <strong>{name}</strong>
          {#if box.account?.kind === 'agent'}<span class="muted small">Agent</span>{/if}
        </span>
        {#if box.account && canSize(box.account.id)}
          <button
            class="icon"
            aria-label="Capacity for {name}"
            title="Capacity"
            aria-expanded={sizing === box.account.id}
            onclick={() => (sizing = sizing === box.account?.id ? null : (box.account?.id ?? null))}><Icon name="gear" size={14} /></button
          >
        {/if}
        <button
          class="icon"
          aria-label="{folded[box.key] ? 'Show' : 'Hide'} {name}'s tasks"
          aria-expanded={!folded[box.key]}
          onclick={() => (folded = { ...folded, [box.key]: !folded[box.key] })}
          ><span class="box-fold" class:folded={folded[box.key]}><Icon name="down" size={14} /></span></button
        >
      </header>
      <div class="box-stats">
        <span class="box-stat"><b>{box.open}</b> to do</span>
        <span class="box-stat active"><b>{box.active}</b> in progress</span>
        <span class="box-stat" class:late={box.overdue}><b>{box.overdue}</b> overdue</span>
        <span class="box-stat done" title="Finished in the last {DONE_DAYS} days"><b>{box.done}</b> done</span>
      </div>
      <div class="box-bar" title="{Math.round(doneShare(box) * 100)}% done">
        <i style:width="{doneShare(box) * 100}%"></i>
      </div>
      {#if loadText(box)}
        <p class="box-load" class:late={box.over}>
          {box.over ? 'Over this week: ' : 'This week: '}{loadText(box)}
        </p>
      {/if}
      {#if box.account && sizing === box.account.id}
        <CapacityEditor account={box.account} onclose={() => (sizing = null)} />
      {/if}
      {#if !folded[box.key]}
        {#if box.items.length}
          <ul class="box-tasks">
            {#each shown as item (item.id)}
              {@const key = `${item.id}|${box.key}`}
              <li
                class="box-task"
                draggable={canEdit(item.list_id)}
                ondragstart={(e) => drag(e, item.id, box.key)}
                data-item={item.id}
              >
                <span class="status-dot" style:background={item.status?.color ?? `var(--kind-${item.status?.kind ?? 'open'})`}></span>
                <button class="link box-title" onclick={() => board.open(item.id)}>{item.title}</button>
                {#if item.due_at}<span class="box-due" class:late={isOverdue(item, board.now)}>{dueLabel(item.due_at, board.now, item.due_date_only)}</span>{/if}
                {#if canEdit(item.list_id)}
                  <button
                    class="icon box-assign"
                    aria-label="Assign {item.title} to someone else"
                    title="Assign to"
                    aria-expanded={assigning === key}
                    onclick={() => (assigning = assigning === key ? null : key)}><Icon name="user" size={13} /></button
                  >
                {/if}
                {#if assigning === key}
                  <label class="box-pick small"
                    >Assign to
                    <select
                      aria-label="Assign {item.title} to"
                      value={box.key}
                      onchange={(e) => {
                        assigning = null;
                        void move(item.id, box.key, e.currentTarget.value);
                      }}
                    >
                      {#each accounts as a (a.id)}<option value={a.id}>{a.display_name}</option>{/each}
                      <option value="none">Nobody</option>
                    </select></label
                  >
                {/if}
              </li>
            {/each}
          </ul>
          {#if box.items.length > SHOWN}
            <button class="link small box-more" onclick={() => (more = { ...more, [box.key]: !more[box.key] })}
              >{more[box.key] ? 'Show fewer' : `Show all ${box.items.length}`}</button
            >
          {/if}
        {:else}
          <p class="muted small box-empty">Nothing open.</p>
        {/if}
      {/if}
    </section>
  {/each}
</div>
