<script lang="ts">
  // One spreadsheet row of the Table layout. Cells show plain text until the row is hovered or
  // focused; then the editors mount (status and priority selects, date pickers, pickers for
  // people and tags), so a few hundred rows stay cheap.
  import { blockedTitle, dueLabel, formatEstimate, isOpen, isOverdue, openBlockers, parseEstimate } from '../lib/format';
  import { priorityLabel } from '../lib/priority';
  import { board } from '../lib/store.svelte';
  import type { Item, Priority } from '../lib/types';
  import { isClosed } from '../lib/views';
  import AgentBadge from './AgentBadge.svelte';
  import AssigneePicker from './AssigneePicker.svelte';
  import CardMenu from './CardMenu.svelte';
  import Avatars from './Avatars.svelte';
  import DatePicker from './DatePicker.svelte';
  import Icon from './Icon.svelte';
  import TagEditor from './TagEditor.svelte';
  import TagPill from './TagPill.svelte';
  import type { Column } from './TableView.svelte';

  let { item, columns }: { item: Item; columns: Column[] } = $props();

  const list = $derived(board.lists[item.list_id]);
  const closed = $derived(isClosed(item));
  const overdue = $derived(isOverdue(item, board.now));
  const selected = $derived(board.selected.has(item.id));
  let armed = $state(false);
  let menuOpen = $state(false);
  let editingTitle = $state(false);
  let titleDraft = $state('');
  let editingEstimate = $state(false);
  let estimateDraft = $state('');

  function saveTitle() {
    if (!editingTitle) return;
    editingTitle = false;
    const t = titleDraft.trim();
    if (t && t !== item.title) void board.update(item.id, { title: t });
  }

  function saveEstimate() {
    if (!editingEstimate) return;
    editingEstimate = false;
    const t = estimateDraft.trim();
    const m = t ? parseEstimate(t) : null;
    if (t && m === null) return board.flash('Estimates look like 90, 45m, 1.5h, 1h 30m or 2d');
    if (m !== (item.estimate_minutes ?? null)) void board.setEstimate(item.id, m);
  }

  const save = (changes: Record<string, unknown>) => void board.update(item.id, changes);
</script>

<tr
  class="t-row prio-stripe-{item.priority ?? 'none'}"
  class:closed
  class:selected
  data-item={item.id}
  draggable="true"
  ondragstart={(e) => {
    e.dataTransfer?.setData('text/plain', item.id);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  }}
  oncontextmenu={(e) => {
    if ((e.target as HTMLElement).closest('input, select, textarea')) return;
    e.preventDefault();
    armed = true;
    menuOpen = true;
  }}
  onmouseenter={() => {
    armed = true;
    board.focusId = item.id;
  }}
  onfocusin={() => {
    armed = true;
    board.focusId = item.id;
  }}
>
  <td class="t-title">
    <div class="t-title-in">
      <input
        class="select"
        type="checkbox"
        aria-label="Select"
        checked={selected}
        onclick={(e) => {
          board.toggleSelect(item.id, e.shiftKey);
          // Let the click tick the box, then match it to the selection (a shift-range can differ).
          e.currentTarget.checked = board.selected.has(item.id);
        }}
      />
      <button
        class="check"
        class:done={closed}
        aria-label={closed ? 'Mark not done' : 'Complete'}
        onclick={() => board.toggleComplete(item.id)}><Icon name="check" size={11} /></button
      >
      {#if editingTitle}
        <!-- svelte-ignore a11y_autofocus -->
        <input
          class="t-title-edit"
          aria-label="Title"
          autofocus
          bind:value={titleDraft}
          onblur={saveTitle}
          onkeydown={(e) => {
            if (e.key === 'Enter') saveTitle();
            if (e.key === 'Escape') {
              e.stopPropagation();
              editingTitle = false;
            }
          }}
        />
      {:else}
        <button class="plain t-title-text" title="Open" onclick={() => board.open(item.id)}>{item.title}</button>
        {#if item.blocked && isOpen(item)}<span class="blocked-badge" data-blocked title={blockedTitle(openBlockers(item, board.items))}><Icon name="alert" size={13} /></span>{/if}
        {#if item.duplicate_of?.length && !item.archived_at}<span class="badge" title="Marked as a duplicate"><Icon name="copy" size={12} /> Duplicate</span>{/if}
        {#if item.subtask_count}<span class="badge" title="Subtasks"><Icon name="subtask" size={12} /> {item.subtask_count}</span>{/if}
        {#if item.agent}<AgentBadge flag={item.agent} {item} />{/if}
        <button
          class="icon t-rename"
          aria-label="Rename {item.title}"
          title="Rename"
          onclick={() => {
            titleDraft = item.title;
            editingTitle = true;
          }}><Icon name="edit" size={13} /></button
        >
        {#if armed}<CardMenu {item} bind:open={menuOpen} />{/if}
      {/if}
    </div>
  </td>
  {#each columns as c (c.key)}
    <td class="t-cell t-{c.kind}" data-col={c.key}>
      {#if c.kind === 'list'}
        <span class="muted">{list?.name ?? ''}</span>
      {:else if c.kind === 'status'}
        {#if armed && list}
          <select
            aria-label="Status"
            class="status-select"
            style:--opt={item.status?.color ?? `var(--kind-${item.status?.kind ?? 'open'})`}
            value={item.status?.id ?? ''}
            onchange={(e) => {
              const s = list.statuses.find((x) => x.id === e.currentTarget.value);
              if (s) void board.moveToStatus(item.id, s);
            }}
          >
            {#each list.statuses as s (s.id)}<option value={s.id}>{s.name}</option>{/each}
          </select>
        {:else}
          <span class="status-pill" style:--opt={item.status?.color ?? `var(--kind-${item.status?.kind ?? 'open'})`}
            >{item.status?.name ?? '—'}</span
          >
        {/if}
      {:else if c.kind === 'due' || c.kind === 'start'}
        {@const v = c.kind === 'due' ? item.due_at : item.start_at}
        {#if armed}
          <DatePicker
            label={c.kind === 'due' ? 'Due' : 'Start'}
            value={v}
            dateOnly={c.kind === 'due' ? item.due_date_only : undefined}
            onchange={(when) => save({ [c.kind === 'due' ? 'due_at' : 'start_at']: when })}
          />
        {:else if v}
          <span class="due" class:overdue={c.kind === 'due' && overdue}>{dueLabel(v, board.now, c.kind === 'due' ? item.due_date_only : undefined)}</span>
        {/if}
      {:else if c.kind === 'priority'}
        {#if armed}
          <select
            aria-label="Priority"
            class="prio-{item.priority ?? 'none'}"
            value={item.priority ?? ''}
            onchange={(e) => board.setPriority(item.id, (e.currentTarget.value || null) as Priority | null)}
          >
            <option value="">–</option>
            <option value="urgent">Urgent</option>
            <option value="high">High</option>
            <option value="normal">Normal</option>
            <option value="low">Low</option>
          </select>
        {:else}
          <span class="prio-{item.priority ?? 'none'}">{item.priority ? priorityLabel(item.priority) : ''}</span>
        {/if}
      {:else if c.kind === 'assignees'}
        {#if armed}
          <AssigneePicker assignees={item.assignees} onchange={(handles) => save({ assignees: handles })} />
        {:else}
          <Avatars people={item.assignees} />
        {/if}
      {:else if c.kind === 'tags'}
        {#if armed}
          <TagEditor tags={item.tags} onchange={(tags) => save({ tags })} />
        {:else}
          {#each item.tags as t (t)}<TagPill name={t} />{/each}
        {/if}
      {:else if c.kind === 'estimate'}
        {#if editingEstimate}
          <!-- svelte-ignore a11y_autofocus -->
          <input
            class="field-text"
            aria-label="Time estimate"
            autofocus
            bind:value={estimateDraft}
            onblur={saveEstimate}
            onkeydown={(e) => {
              if (e.key === 'Enter') saveEstimate();
              if (e.key === 'Escape') {
                e.stopPropagation();
                editingEstimate = false;
              }
            }}
          />
        {:else}
          <button
            class="field-show"
            class:empty={!item.estimate_minutes}
            aria-label="Edit time estimate"
            onclick={() => {
              estimateDraft = formatEstimate(item.estimate_minutes);
              editingEstimate = true;
            }}>{formatEstimate(item.estimate_minutes)}</button
          >
        {/if}
      {/if}
    </td>
  {/each}
</tr>
