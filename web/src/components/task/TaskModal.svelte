<script lang="ts">
  // The task window: a large centered window over the board (ClickUp's task view). Left: status,
  // title, the field grid, description (autosaving), subtasks, checklists and files. Right: the
  // activity column, comments interleaved with history, with the composer and the ways the next
  // turn starts (Ask, Run now, Hand off) together at its foot. Esc or the scrim closes it; the
  // URL carries `?task=<id>` so a link opens it again. Under 760px it fills the screen and stacks.
  // Its size and place are remembered (lib/taskwindow.ts): the corner grip resizes the centered
  // window about its center, the grip on the activity column's edge widens it leftward, and the
  // dock button parks the whole window on the right third or quarter of the screen (no scrim
  // then: the board beside it stays usable) or back in the center.
  // On a card an agent is on, the conversation is the action: the reference fields (assignees,
  // dates, tags, list, repeats, estimate, dependencies) fold into one Details line once any is
  // set, and on the phone the thread comes straight after the title instead of under them.
  import { onDestroy, onMount } from 'svelte';
  import { api } from '../../lib/api';
  import { stamp } from '../../lib/activity';
  import { purgeLabel } from '../../lib/archive';
  import { docs } from '../../lib/docs.svelte';
  import type { DocSummary } from '../../lib/docs';
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import { taskWindow } from '../../lib/taskwindow.svelte';
  import { clampActivity, DOCK_LABELS, DOCKS, STACK_BELOW, windowWidth, type Dock } from '../../lib/taskwindow';
  import type { Item, Priority } from '../../lib/types';
  import AgentBadge from '../AgentBadge.svelte';
  import AssigneePicker from '../AssigneePicker.svelte';
  import HandOff from './HandOff.svelte';
  import ThreadLinks from './ThreadLinks.svelte';
  import RunNow from '../RunNow.svelte';
  import Reminders from './Reminders.svelte';
  import DatePicker from '../DatePicker.svelte';
  import DependenciesPanel from '../DependenciesPanel.svelte';
  import EstimatePanel from '../EstimatePanel.svelte';
  import Menu, { type MenuEntry } from '../Menu.svelte';
  import Dropdown from '../Dropdown.svelte';
  import MovePicker from '../MovePicker.svelte';
  import RecurrenceEditor from '../RecurrenceEditor.svelte';
  import TagEditor from '../TagEditor.svelte';
  import ActivityPanel from './ActivityPanel.svelte';
  import Attachments from './Attachments.svelte';
  import AutoText from './AutoText.svelte';
  import Checklists from './Checklists.svelte';
  import Icon from './Icon.svelte';
  import { formatEstimate, fullStamp, isOpen, openBlockers, shortDate } from '../../lib/format';
  import { DEPENDS_ON, LEAVES, PUT_AWAY, waitingOn } from '../../lib/words';
  import { waitingQuestion } from '../../lib/conversation';
  import PriorityPicker from './PriorityPicker.svelte';
  import StatusPicker from './StatusPicker.svelte';
  import Subtasks from './Subtasks.svelte';

  const d = $derived(board.detail);
  // Blocked follows the board's row (item.blocked / item.unblocked land there live).
  const blockedNow = $derived(
    !!d && !d.archived_at && isOpen(d) && (board.items[d.id]?.blocked ?? d.blocked),
  );
  const waitsOn = $derived(d ? openBlockers(board.items[d.id] ?? d, board.items) : []);
  const list = $derived(d ? board.lists[d.list_id] : undefined);
  const section = $derived(list ? board.sections.find((s) => s.id === list.section_id) : undefined);
  // "Move to…" in the header; subtasks live in their parent's list, so they have none.
  let moveOpen = $state(false);
  let fieldMoveOpen = $state(false);
  const canMove = $derived(!!d && !d.parent_id && !d.trashed_at);
  function moveTo(listId: string) {
    moveOpen = false;
    fieldMoveOpen = false;
    if (d) void board.moveItem(d.id, listId);
  }
  const parent = $derived(d?.parent_id ? board.items[d.parent_id] : undefined);
  // Subtasks nest four deep (service/items.py MAX_DEPTH): the parent, grandparent... nearest
  // first, from the board's items (a missing ancestor ends the trail), and this task's depth.
  const ancestors = $derived.by(() => {
    const out: Item[] = [];
    const seen = new Set<string>(d ? [d.id] : []);
    let cur = parent;
    while (cur && !seen.has(cur.id)) {
      out.push(cur);
      seen.add(cur.id);
      cur = cur.parent_id ? board.items[cur.parent_id] : undefined;
    }
    return out;
  });
  const MAX_DEPTH = 4;
  const canNest = $derived(!!d && ancestors.length < MAX_DEPTH && !(d.parent_id && !parent));
  const rule = $derived(d?.recurrence_id ? board.recurrences[d.recurrence_id] : undefined);
  // The live copy on the board follows item.reported events; the detail is a fetched snapshot.
  const agentFlag = $derived(
    d ? (d.id in board.items ? (board.items[d.id].agent ?? null) : (d.agent ?? null)) : null,
  );
  // The agents' sessions for this card: the live row's (kept current by item.reported), else
  // the detail snapshot's.
  const agentThreads = $derived(
    d ? ((d.id in board.items ? board.items[d.id].agent_threads : undefined) ?? d.agent_threads ?? []) : [],
  );
  const accounts = $derived(
    [...board.pickable]
      .sort((a, b) => a.display_name.localeCompare(b.display_name)),
  );
  // An agent is on this card: it reported, worked it before, or is assigned.
  const agentOn = $derived(
    !!d &&
      (!!agentFlag || agentThreads.length > 0 || d.assignees.some((a) => board.accounts[a.id]?.kind === 'agent')),
  );
  // Waiting on me: who asked and what, pulled up from the thread so the question is never just
  // the last line of a long comment (the "Needs you" pill's Answer lands here).
  const waitQ = $derived.by(() => {
    if (!d || !task.me || d.waiting_on_account_id !== task.me) return null;
    const feed = task.activity ?? [];
    const comments = [...d.comments, ...feed.flatMap((e) => (e.comment ? [e.comment] : []))];
    const events = [...d.events, ...feed.flatMap((e) => (e.event ? [e.event] : []))];
    return waitingQuestion(d, comments, events, task.me);
  });

  // The folded Details line: what is set, in a few words.
  const refSummary = $derived.by((): string[] => {
    if (!d) return [];
    const out: string[] = [];
    if (d.assignees.length) out.push(d.assignees.map((a) => board.accountName(a.id) ?? a.handle).join(', '));
    if (d.due_at) out.push(`Due ${shortDate(d.due_at, board.now)}`);
    else if (d.start_at) out.push(`Starts ${shortDate(d.start_at, board.now)}`);
    if (d.priority) out.push(d.priority[0].toUpperCase() + d.priority.slice(1));
    if (d.waiting_on_account_id) out.push(waitingOn(board.accountName(d.waiting_on_account_id)));
    for (const t of d.tags.slice(0, 3)) out.push(`#${t}`);
    if (d.tags.length > 3) out.push(`+${d.tags.length - 3}`);
    if (d.recurrence_id) out.push('Repeats');
    if (d.estimate_minutes) out.push(formatEstimate(d.estimate_minutes));
    const deps = (d.blocked_by?.length ?? 0) + (d.blocking?.length ?? 0) + (d.related?.length ?? 0);
    if (deps) out.push(`${deps} link${deps > 1 ? 's' : ''}`);
    return out;
  });
  let refsOpen = $state(true);
  const canUndoCreate = $derived(
    !!d && d.created_by === task.me && board.now.getTime() - new Date(d.created_at).getTime() < 10 * 60_000,
  );

  let title = $state('');
  let titleArea = $state<HTMLTextAreaElement>();
  let addingSubtask = $state(false);
  let picker = $state<HTMLInputElement>();
  let dropping = $state(false);
  let showDone = $state(false);
  // Doc pages that embed or link this task, so a task filed from a meeting note leads back to it.
  let inDocs = $state<DocSummary[]>([]);
  const detailId = $derived(d?.id);
  $effect(() => {
    const id = detailId;
    inDocs = [];
    if (!id) return;
    void docs
      .embedding(id)
      .then((r) => {
        if (board.detail?.id === id) inDocs = r;
      })
      .catch(() => {});
  });

  // A different task opened (or this one renamed elsewhere): take its title, reset the column.
  let shownId = '';
  $effect(() => {
    if (!d) return;
    if (d.id !== shownId) {
      shownId = d.id;
      task.reset();
      addingSubtask = false;
      showDone = !!d.acceptance_md;
      refsOpen = !(agentOn && refSummary.length);
    }
    if (document.activeElement !== titleArea) title = d.title;
  });

  // The URL names the open task, so reload and copy-link land on it.
  $effect(() => {
    const id = board.openId;
    if (!id) return;
    const [path, query = ''] = location.hash.split('?');
    const q = new URLSearchParams(query);
    if (q.get('task') === id) return;
    q.set('task', id);
    history.replaceState(history.state, '', `${path || '#/everything'}?${q}`);
  });
  onDestroy(() => {
    // Closing the task with the title still being typed keeps the new title.
    if (titleFor) {
      const t = title.replace(/\s+/g, ' ').trim();
      if (t && t !== titleFor.was) void board.update(titleFor.id, { title: t });
    }
    const [path, query = ''] = location.hash.split('?');
    const q = new URLSearchParams(query);
    if (!q.has('task')) return;
    q.delete('task');
    const rest = q.toString();
    history.replaceState(history.state, '', `${path}${rest ? `?${rest}` : ''}`);
    task.reset();
  });

  $effect(() => {
    void title;
    if (titleArea) {
      titleArea.style.height = 'auto';
      titleArea.style.height = `${titleArea.scrollHeight}px`;
    }
  });

  function save(changes: Record<string, unknown>): Promise<boolean> {
    return d ? board.update(d.id, changes) : Promise.resolve(false);
  }

  /** The task whose title is being typed, and its title before (d is gone by the time we close). */
  let titleFor: { id: string; was: string } | null = null;

  function saveTitle() {
    titleFor = null;
    const t = title.replace(/\s+/g, ' ').trim();
    if (!d) return;
    if (!t) title = d.title;
    else if (t !== d.title) void save({ title: t });
  }

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      board.flash(`${what} copied`);
    } catch {
      board.flash(text);
    }
  }

  async function remove() {
    if (!d || !confirm(`Delete “${d.title}”? This is the undo for a task you just made.`)) return;
    try {
      await api('DELETE', `/items/${d.id}`);
      board.close();
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  const menu = $derived.by((): MenuEntry[] => {
    if (!d) return [];
    const out: MenuEntry[] = [
      { label: 'Copy link', onselect: () => copy(task.link(d.id), 'Link') },
      { label: 'Copy ID', onselect: () => copy(d.id, 'ID') },
      { label: 'Copy as Markdown', onselect: () => copy(`- [${d.status?.kind === 'done' ? 'x' : ' '}] ${d.title} (${task.link(d.id)})`, 'Markdown') },
      { label: 'Attach files', onselect: () => picker?.click() },
      { label: 'Add checklist', onselect: () => void task.addChecklist(d.id) },
    ];
    if (canMove) out.push({ label: 'Move to…', onselect: () => (moveOpen = true) });
    if (parent) out.push({ label: 'Open parent task', onselect: () => board.open(parent.id) });
    if (d.trashed_at) {
      out.push({ label: 'Restore from trash', onselect: () => void board.restore(d.id) });
    } else {
      out.push(
        d.archived_at
          ? { label: 'Bring back', onselect: () => board.unarchive(d.id) }
          : { label: PUT_AWAY, title: LEAVES.putAway, danger: true, onselect: () => board.archive(d.id) },
        { label: 'Move to trash', title: LEAVES.trash, danger: true, onselect: () => board.trash(d.id) },
      );
    }
    if (canUndoCreate) out.push({ label: 'Delete (undo create)', danger: true, onselect: remove });
    return out;
  });

  function onDrop(e: DragEvent) {
    e.preventDefault();
    dropping = false;
    const files = [...(e.dataTransfer?.files ?? [])];
    if (d && files.length) void task.upload(d.id, files);
  }

  function hasFiles(e: DragEvent): boolean {
    return [...(e.dataTransfer?.types ?? [])].includes('Files');
  }

  function goToList() {
    if (!list) return;
    board.close();
    location.hash = board.href({ kind: 'list', id: list.id });
  }

  // A modal dialog: focus moves into the window when it opens, Tab cycles inside it, and the
  // control that opened it (a card, a favorite chip) gets focus back when it closes.
  let dialog = $state<HTMLDivElement>();
  onMount(() => {
    const previous = document.activeElement as HTMLElement | null;
    if (dialog && !dialog.contains(document.activeElement)) dialog.focus({ preventScroll: true });
    return () => {
      if (previous && previous !== document.body && document.contains(previous)) previous.focus({ preventScroll: true });
    };
  });
  const FOCUSABLE =
    'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [contenteditable="true"], [tabindex]:not([tabindex="-1"])';
  function trapTab(e: KeyboardEvent) {
    // Docked, the window is not modal: Tab moves on to the board beside it.
    if (e.key !== 'Tab' || !dialog || docked) return;
    const active = document.activeElement;
    // Something over the window (the viewer, the palette, a floated menu) has focus: its turn.
    if (active && active !== document.body && !dialog.contains(active)) return;
    const controls = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.getClientRects().length);
    const first = controls[0];
    const last = controls.at(-1);
    if (!first || !last) {
      e.preventDefault();
      dialog.focus();
    } else if (!controls.includes(active as HTMLElement)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    } else if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  // Size and place. `innerWidth` is bound so a dock's share and the stacking decision follow
  // the screen; the phone layout (<= 760px) is the same stacked body as a narrow dock.
  let innerWidth = $state(typeof window === 'undefined' ? 1920 : window.innerWidth);
  let phone = $derived(innerWidth <= 760);
  let docked = $derived(!phone && taskWindow.docked);
  let shown = $derived(windowWidth(taskWindow.state, innerWidth));
  let stacked = $derived(phone || shown < STACK_BELOW);
  let dockOpen = $state(false);
  function setDock(dock: Dock) {
    dockOpen = false;
    taskWindow.setDock(dock);
  }
  // The grips: pointer capture so a fast drag past the edge still follows; arrows nudge; a
  // double-click resets. The corner grip moves the centered window's edge, and since the window
  // stays centered the width grows by twice the pointer's distance from the center.
  let sizing = $state(false);
  let activitySizing = $state(false);
  function grabCorner(e: PointerEvent) {
    if (e.button !== 0) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    sizing = true;
  }
  function dragCorner(e: PointerEvent) {
    if (!sizing || !dialog) return;
    const r = dialog.getBoundingClientRect();
    taskWindow.resize(2 * (e.clientX - (r.left + r.width / 2)), 2 * (e.clientY - (r.top + r.height / 2)));
  }
  function releaseCorner(e: PointerEvent) {
    if (!sizing) return;
    sizing = false;
    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
  }
  function cornerKey(e: KeyboardEvent) {
    const w = taskWindow.width;
    const h = taskWindow.height;
    if (e.key === 'ArrowRight') taskWindow.resize(w + 48, h);
    else if (e.key === 'ArrowLeft') taskWindow.resize(w - 48, h);
    else if (e.key === 'ArrowDown') taskWindow.resize(w, h + 48);
    else if (e.key === 'ArrowUp') taskWindow.resize(w, h - 48);
    else if (e.key === 'Home') taskWindow.resetSize();
    else return;
    e.preventDefault();
  }
  function grabActivity(e: PointerEvent) {
    if (e.button !== 0) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    activitySizing = true;
  }
  function dragActivity(e: PointerEvent) {
    if (!activitySizing || !dialog) return;
    taskWindow.resizeActivity(dialog.getBoundingClientRect().right - e.clientX);
  }
  function releaseActivity(e: PointerEvent) {
    if (!activitySizing) return;
    activitySizing = false;
    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
  }
  function activityKey(e: KeyboardEvent) {
    if (e.key === 'ArrowLeft') taskWindow.resizeActivity(taskWindow.activity + 24);
    else if (e.key === 'ArrowRight') taskWindow.resizeActivity(taskWindow.activity - 24);
    else if (e.key === 'Home') taskWindow.resetActivity();
    else return;
    e.preventDefault();
  }
  // The activity column never squeezes the main column below its keep (a window docked to a
  // quarter after the activity was widened in the center).
  let activityWidth = $derived(clampActivity(taskWindow.activity, shown));
</script>

<svelte:window onkeydown={trapTab} bind:innerWidth />

{#if !docked}
  <button class="tw-scrim" aria-label="Close task" tabindex="-1" onclick={() => board.close()}></button>
{/if}
<div
  bind:this={dialog}
  class="tw"
  class:docked
  class:stacked
  class:sizing={sizing || activitySizing}
  data-dock={taskWindow.dock}
  style="--tw-w: {taskWindow.width}px; --tw-h: {taskWindow.height}px; --tw-activity: {activityWidth}px"
  role="dialog"
  aria-modal={!docked}
  aria-label={d ? `Task: ${d.title}` : 'Task'}
  tabindex="-1"
  ondragover={(e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dropping = true;
  }}
  ondragleave={(e) => e.currentTarget === e.target && (dropping = false)}
  ondrop={onDrop}
>
  <header class="tw-top">
    <nav class="crumbs" aria-label="Where this task lives">
      {#if section}<span class="muted">{section.name}</span><Icon name="chevron" size={12} />{/if}
      {#if list}<button class="crumb" onclick={goToList}>{list.name}</button>{/if}
      {#each [...ancestors].reverse() as a, i (a.id)}
        <Icon name="chevron" size={12} />
        <button class="crumb" title={i === ancestors.length - 1 ? 'Open parent task' : 'Open this task'} onclick={() => board.open(a.id)}>{a.title}</button>
      {/each}
      {#if d}
        <button class="id" title="Copy ID" onclick={() => copy(d.id, 'ID')}>#{d.id}</button>
      {/if}
    </nav>
    <div class="top-actions">
      {#if d}
        <span class="meta muted" title={fullStamp(d.created_at)}>
          Created {stamp(d.created_at, board.now)}{#if d.created_by} by {d.created_by === task.me ? 'you' : (board.accountName(d.created_by) ?? 'someone')}{/if}
        </span>
        <button
          class="ghost fav-star"
          class:on={!!board.favoriteOf('item', d.id)}
          title={board.favoriteOf('item', d.id) ? 'Remove from favorites (s)' : 'Add to favorites (s)'}
          aria-label={board.favoriteOf('item', d.id) ? 'Remove from favorites' : 'Add to favorites'}
          aria-pressed={!!board.favoriteOf('item', d.id)}
          onclick={() => board.toggleFavorite('item', d.id)}><Icon name="star" fill={!!board.favoriteOf('item', d.id)} /></button
        >
        <button class="ghost" title="Copy link" aria-label="Copy link" onclick={() => copy(task.link(d.id), 'Link')}
          ><Icon name="link" /></button
        >
        {#if canMove}
          <Dropdown float title="Move to another list" cls="ghost tw-move" align="right" bind:open={moveOpen}>
            {#snippet label()}<Icon name="folder" /><span class="tw-move-text">Move</span>{/snippet}
            <div class="menu card-menu" role="menu">
              <MovePicker current={d.list_id} onpick={moveTo} />
            </div>
          </Dropdown>
        {/if}
        <Menu label="Task actions" entries={menu} />
      {/if}
      {#if !phone}
        <Dropdown float title="Where the window sits" cls="ghost tw-dock" align="right" bind:open={dockOpen}>
          {#snippet label()}<Icon name={taskWindow.docked ? 'dockRight' : 'dockCenter'} />{/snippet}
          <div class="menu card-menu tw-dock-menu" role="menu" aria-label="Window position">
            {#each DOCKS as dock (dock)}
              <button role="menuitemradio" aria-checked={taskWindow.dock === dock} class:active={taskWindow.dock === dock} onclick={() => setDock(dock)}>
                <Icon name={dock === 'center' ? 'dockCenter' : 'dockRight'} size={14} />
                {DOCK_LABELS[dock]}
                {#if taskWindow.dock === dock}<Icon name="check" size={14} />{/if}
              </button>
            {/each}
          </div>
        </Dropdown>
      {/if}
      <button class="ghost" aria-label="Close" title="Close (Esc)" onclick={() => board.close()}><Icon name="x" size={18} /></button>
    </div>
  </header>

  {#if !d}
    <p class="muted loading">Loading…</p>
  {:else}
    <div class="tw-body" class:agent-first={agentOn}>
      <div class="main">
        <div class="tw-head">
        {#if d.trashed_at}
          <div class="banner trashed">
            This task is in the trash. {purgeLabel(d.purge_at, board.now)}.
            <button class="link" onclick={() => board.restore(d.id)}>Restore</button>
          </div>
        {:else if d.archived_at}
          <div class="banner">
            This task was put away {stamp(d.archived_at, board.now)}: off the board, history kept.
            <button class="link" onclick={() => board.unarchive(d.id)}>Bring back</button>
          </div>
        {/if}

        {#if blockedNow}
          <div class="banner blocked" data-banner="blocked">
            <Icon name="alert" size={13} /> {DEPENDS_ON}
            {#each waitsOn as b, i (b.id)}{i ? ', ' : ' '}<button class="link" onclick={() => board.open(b.id)}>{b.title}</button>{/each}{waitsOn.length ? '' : ' an open task'}.
            It moves on by itself when {waitsOn.length > 1 ? 'they are' : 'that is'} done.
          </div>
        {/if}

        {#if waitQ}
          <div class="banner waiting" data-banner="waiting">
            <div class="wq-head">
              <Icon name="hourglass" size={13} />
              <strong>{waitQ.by ? (board.accountName(waitQ.by) ?? 'Someone') : 'Someone'} is waiting on you</strong>
            </div>
            {#if waitQ.question}<p class="wq-text" data-waiting-question>{waitQ.question}</p>{/if}
            <div class="wq-actions">
              <button class="primary" data-answer onclick={() => task.reveal(waitQ.commentId)}>Answer</button>
              <button class="link" data-not-waiting onclick={() => save({ waiting_on_account_id: null })}>Not waiting on me</button>
            </div>
          </div>
        {/if}

        {#if agentFlag}
          <div class="agent-now"><AgentBadge flag={agentFlag} item={d} full /></div>
        {/if}

        <div class="status-row">
          <StatusPicker item={d} />
          {#if d.recurrence_id}<span class="badge" title="Repeats"><Icon name="repeat" size={13} /> Repeats</span>{/if}
        </div>
        <ThreadLinks threads={agentThreads} flag={agentFlag} />

        <textarea
          bind:this={titleArea}
          class="title"
          aria-label="Title"
          rows="1"
          bind:value={title}
          onfocus={() => d && (titleFor = { id: d.id, was: d.title })}
          onblur={saveTitle}
          onkeydown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              titleArea?.blur();
            }
            if (e.key === 'Escape') {
              e.stopPropagation();
              title = d.title;
              titleArea?.blur();
            }
          }}
        ></textarea>
        </div>

        <div class="tw-rest">
        {#if agentOn && refSummary.length}
          <button class="refs-toggle" aria-expanded={refsOpen} data-refs-toggle onclick={() => (refsOpen = !refsOpen)}>
            <span class="caret" class:open={refsOpen}><Icon name="chevronRight" size={13} /></span>
            <span class="refs-label">Details</span>
            {#if !refsOpen}<span class="refs-sum">{refSummary.join(' · ')}</span>{/if}
          </button>
        {/if}
        {#if refsOpen || !agentOn || !refSummary.length}
        <div class="fields">
          <div class="field">
            <span class="label"><Icon name="user" /> Assignees</span>
            <div class="value"><AssigneePicker assignees={d.assignees} onchange={(handles) => save({ assignees: handles })} /></div>
          </div>
          <div class="field">
            <span class="label"><Icon name="flag" /> Priority</span>
            <div class="value">
              <PriorityPicker value={d.priority} onchange={(p: Priority | null) => save({ priority: p })} />
            </div>
          </div>
          <div class="field">
            <span class="label"><Icon name="calendar" /> Dates</span>
            <div class="value dates">
              <DatePicker label="Start" value={d.start_at} onchange={(when) => save({ start_at: when })} />
              <span class="muted arrow">→</span>
              <DatePicker label="Due" value={d.due_at} dateOnly={d.due_date_only} onchange={(when) => save({ due_at: when })} />
            </div>
          </div>
          <div class="field">
            <span class="label"><Icon name="tag" /> Tags</span>
            <div class="value"><TagEditor tags={d.tags} onchange={(tags) => save({ tags })} /></div>
          </div>
          <div class="field">
            <span class="label"><Icon name="wait" /> Waiting on</span>
            <div class="value">
              <select
                class="bare"
                aria-label="Waiting on"
                value={d.waiting_on_account_id ?? ''}
                onchange={(e) => save({ waiting_on_account_id: e.currentTarget.value || null })}
              >
                <option value="">Nobody</option>
                {#each accounts as a (a.id)}<option value={a.id}>{a.display_name}{a.kind === 'agent' ? ' 🤖' : ''}</option>{/each}
              </select>
            </div>
          </div>
          <div class="field wide">
            <span class="label"><Icon name="bell" /> Reminders</span>
            <div class="value"><Reminders item={d} /></div>
          </div>
          {#if !d.parent_id}
            <div class="field">
              <span class="label"><Icon name="list" /> List</span>
              <div class="value">
                {#if d.trashed_at}
                  <span>{section ? `${section.name} › ` : ''}{list?.name ?? ''}</span>
                {:else}
                  <Dropdown title="Move to list" cls="bare tw-list-pick" bind:open={fieldMoveOpen}>
                    {#snippet label()}{section ? `${section.name} › ` : ''}{list?.name ?? 'Pick a list'}{/snippet}
                    <div class="menu card-menu" role="menu">
                      <MovePicker current={d.list_id} onpick={moveTo} />
                    </div>
                  </Dropdown>
                {/if}
              </div>
            </div>
          {/if}
          {#if !d.parent_id || d.recurrence_id}
            <div class="field wide">
              <span class="label"><Icon name="repeat" /> Repeats</span>
              <div class="value"><RecurrenceEditor item={d} {rule} /></div>
            </div>
          {/if}
          {#if showDone}
            <div class="field wide">
              <span class="label"><Icon name="target" /> Done means</span>
              <div class="value">
                <AutoText
                  label="What done means"
                  placeholder="What has to be true for this to be done?"
                  value={d.acceptance_md ?? ''}
                  onsave={(t) => save({ acceptance_md: t || null })}
                />
              </div>
            </div>
          {/if}
        </div>
        <!-- panels: estimate, dependencies -->
        <div class="panels">
          <EstimatePanel item={d} />
          <DependenciesPanel item={d} />
        </div>
        {/if}

        <section class="desc" aria-label="Description">
          <AutoText
            big
            label="Description"
            placeholder="Add a description. Markdown works; - [ ] makes a tick box."
            value={d.body_md}
            onsave={(t) => save({ body_md: t })}
            onpaste={(files) => void task.upload(d.id, files)}
          />
        </section>

        <div class="adders">
          {#if canNest}
            <button onclick={() => (addingSubtask = true)}><Icon name="subtask" size={14} /> Subtask</button>
          {/if}
          <button onclick={() => task.addChecklist(d.id)}><Icon name="checklist" size={14} /> Checklist</button>
          <button onclick={() => picker?.click()}><Icon name="clip" size={14} /> Attach</button>
          {#if !showDone}
            <button onclick={() => (showDone = true)}><Icon name="target" size={14} /> Done means</button>
          {/if}
        </div>

        {#if canNest || d.subtasks.length}<Subtasks item={d} bind:adding={addingSubtask} canAdd={canNest} />{/if}
        <Checklists item={d} />
        <Attachments item={d} bind:picker />
        {#if inDocs.length}
          <p class="task-docs">
            <span class="muted">In docs</span>
            {#each inDocs as p (p.id)}
              <a class="chip" href="#/docs/{p.id}">{p.icon ?? '📄'} {p.title}</a>
            {/each}
          </p>
        {/if}
        </div>
      </div>

      {#if !stacked}
        <button
          type="button"
          class="tw-activity-grip"
          aria-label="Resize activity column"
          title="Drag to widen the activity column. Double-click to reset."
          onpointerdown={grabActivity}
          onpointermove={dragActivity}
          onpointerup={releaseActivity}
          onpointercancel={releaseActivity}
          ondblclick={() => taskWindow.resetActivity()}
          onkeydown={activityKey}
        ></button>
      {/if}
      <ActivityPanel item={d} onattach={() => picker?.click()}>
        {#snippet actions()}
          <div class="turn-actions" data-turn-actions>
            <RunNow item={d} />
            {#if !agentFlag}<HandOff item={d} />{/if}
          </div>
        {/snippet}
      </ActivityPanel>
    </div>
  {/if}

  {#if !phone && !docked}
    <button
      type="button"
      class="tw-corner-grip"
      aria-label="Resize window"
      title="Drag to resize. Double-click to reset."
      onpointerdown={grabCorner}
      onpointermove={dragCorner}
      onpointerup={releaseCorner}
      onpointercancel={releaseCorner}
      ondblclick={() => taskWindow.resetSize()}
      onkeydown={cornerKey}
    ></button>
  {/if}

  {#if dropping}
    <div class="dropzone" aria-hidden="true"><Icon name="clip" size={28} /> Drop files to attach</div>
  {/if}
</div>

<style>
  .tw-scrim {
    position: fixed;
    inset: 0;
    z-index: 40;
    border: 0;
    padding: 0;
    background: rgb(10 12 16 / 0.55);
    cursor: default;
  }
  .tw {
    position: fixed;
    z-index: 41;
    /* --kb: the on-screen keyboard's height (lib/viewport.ts), so the composer stays above it. */
    top: calc((100dvh - var(--kb, 0px)) / 2);
    left: 50%;
    translate: -50% -50%;
    width: min(var(--tw-w, 1280px), calc(100vw - 48px));
    height: min(var(--tw-h, 900px), calc(100dvh - var(--kb, 0px) - 48px));
    display: flex;
    flex-direction: column;
    background: var(--panel);
    color: var(--text);
    border: 1px solid var(--line);
    border-radius: 12px;
    box-shadow: 0 24px 64px rgb(0 0 0 / 0.45);
    overflow: hidden;
  }
  .tw:focus {
    outline: none;
  }
  /* Docked: the right third or quarter of the screen, full height, the board beside it live. */
  .tw.docked {
    top: 0;
    left: auto;
    right: 0;
    translate: none;
    height: calc(100dvh - var(--kb, 0px));
    border-radius: 0;
    border-top: 0;
    border-right: 0;
    border-bottom: 0;
    box-shadow: -12px 0 40px rgb(0 0 0 / 0.3);
  }
  .tw.docked[data-dock='third'] {
    width: calc(100vw / 3);
  }
  .tw.docked[data-dock='quarter'] {
    width: calc(100vw / 4);
  }
  .tw.sizing {
    user-select: none;
  }
  .tw.sizing .main,
  .tw.sizing :global(.activity) {
    pointer-events: none; /* the content under a fast drag must not grab the pointer */
  }
  .tw-corner-grip {
    all: unset;
    position: absolute;
    right: 0;
    bottom: 0;
    width: 18px;
    height: 18px;
    cursor: nwse-resize;
    z-index: 2;
    touch-action: none;
  }
  .tw-corner-grip::after {
    content: '';
    position: absolute;
    right: 4px;
    bottom: 4px;
    width: 8px;
    height: 8px;
    border-right: 2px solid var(--muted);
    border-bottom: 2px solid var(--muted);
    border-radius: 0 0 2px 0;
    opacity: 0.6;
    transition: opacity var(--dur-2) var(--ease), border-color var(--dur-2) var(--ease);
  }
  .tw-corner-grip:hover::after,
  .tw-corner-grip:focus-visible::after,
  .tw.sizing .tw-corner-grip::after {
    opacity: 1;
    border-color: var(--accent);
  }
  .tw-corner-grip:focus-visible {
    outline: none;
  }
  .tw-activity-grip {
    all: unset;
    position: absolute;
    top: 0;
    bottom: 0;
    right: calc(var(--tw-activity, 420px) - 5px);
    width: 10px;
    cursor: col-resize;
    z-index: 1;
    touch-action: none;
  }
  .tw-activity-grip::after {
    content: '';
    position: absolute;
    top: 0;
    bottom: 0;
    left: 4px;
    width: 2px;
    background: var(--accent);
    opacity: 0;
    transition: opacity var(--dur-2) var(--ease);
  }
  .tw-activity-grip:hover::after,
  .tw-activity-grip:focus-visible::after,
  .tw.sizing .tw-activity-grip::after {
    opacity: 1;
  }
  .tw-activity-grip:focus-visible {
    outline: none;
  }
  .tw-dock-menu button {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .tw-dock-menu button > :global(svg:last-child) {
    margin-left: auto;
    color: var(--accent);
  }
  .tw-top {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 14px 10px 20px;
    border-bottom: 1px solid var(--line);
    min-height: 50px;
  }
  .crumbs {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    flex: 1;
    font-size: 13px;
    color: var(--muted);
    white-space: nowrap;
  }
  .crumb {
    border: 0;
    background: none;
    padding: 2px 4px;
    border-radius: 4px;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 260px;
    min-width: 3.5em; /* the list name never shrinks to a sliver on a phone */
  }
  .crumbs > .muted {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .crumb:hover {
    background: var(--card-hover);
  }
  .id {
    flex: none;
    margin-left: 6px;
    border: 1px solid var(--line);
    border-radius: 5px;
    background: none;
    color: var(--muted);
    font-family: ui-monospace, monospace;
    font-size: 11.5px;
    padding: 1px 6px;
  }
  .top-actions {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .meta {
    font-size: 12px;
    margin-right: 8px;
  }
  .ghost {
    border: 0;
    background: none;
    color: var(--muted);
    width: 32px;
    height: 32px;
    border-radius: 6px;
    display: inline-grid;
    place-items: center;
    padding: 0;
  }
  .ghost:hover {
    background: var(--card-hover);
    color: var(--text);
  }
  .panels {
    display: grid;
    gap: 12px;
    margin: 4px 0 18px;
  }
  .fav-star {
    font-size: 17px;
  }
  .fav-star.on {
    color: var(--fav-star);
  }
  .top-actions :global(.menu-button) {
    width: 32px;
    height: 32px;
  }
  .tw-body {
    position: relative;
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) var(--tw-activity, 420px);
    grid-template-rows: minmax(0, 1fr);
  }
  .main {
    overflow-y: auto;
    padding: 20px 32px 40px;
  }
  .loading {
    padding: 32px;
  }
  .banner {
    background: color-mix(in srgb, var(--prio-high) 15%, transparent);
    border: 1px solid color-mix(in srgb, var(--prio-high) 40%, transparent);
    border-radius: 8px;
    padding: 8px 12px;
    margin-bottom: 12px;
    font-size: 13px;
  }
  .banner.blocked {
    background: color-mix(in srgb, var(--overdue) 10%, transparent);
    border-color: color-mix(in srgb, var(--overdue) 35%, transparent);
  }
  .banner.waiting {
    background: color-mix(in srgb, var(--danger) 8%, transparent);
    border-color: color-mix(in srgb, var(--danger) 40%, transparent);
    border-left: 3px solid var(--danger);
  }
  .wq-head {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--danger);
  }
  .wq-text {
    margin: 6px 0 8px;
    font-size: 14px;
    color: var(--text);
  }
  .wq-actions {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .banner.trashed {
    background: color-mix(in srgb, var(--danger) 12%, transparent);
    border-color: color-mix(in srgb, var(--danger) 40%, transparent);
  }
  .agent-now {
    margin-bottom: 10px;
  }
  /* Run now and Hand off, at the composer (ActivityPanel's `actions`). */
  .turn-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }
  .turn-actions:not(:has(*)) {
    display: none;
  }
  .refs-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
    min-width: 0;
    margin: 0 0 12px;
    padding: 7px 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: none;
    color: var(--text);
    font: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
  }
  .refs-toggle:hover {
    background: var(--card-hover);
  }
  .refs-toggle .caret {
    display: inline-flex;
    color: var(--muted);
    transition: rotate 0.12s;
  }
  .refs-toggle .caret.open {
    rotate: 90deg;
  }
  .refs-label {
    font-weight: 600;
    flex: none;
  }
  .refs-sum {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--muted);
  }
  .status-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 10px;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--muted);
    border: 1px solid var(--line);
    border-radius: 5px;
    padding: 2px 7px;
  }
  .title {
    width: 100%;
    font-size: 26px;
    font-weight: 700;
    line-height: 1.25;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    padding: 4px 8px;
    margin: 0 -8px 12px;
    resize: none;
    overflow: hidden;
  }
  .title:hover {
    border-color: var(--line);
  }
  .title:focus {
    outline: none;
    border-color: var(--accent);
    background: var(--panel);
  }
  .fields {
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: 28px;
    row-gap: 2px;
    margin-bottom: 18px;
  }
  .field {
    display: grid;
    grid-template-columns: 128px minmax(0, 1fr);
    align-items: start;
    min-height: 38px;
    padding: 4px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--line) 50%, transparent);
  }
  .field.wide {
    grid-column: 1 / -1;
    grid-template-columns: 128px minmax(0, 1fr);
  }
  .label {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 13px;
    padding-top: 6px;
  }
  .value {
    min-width: 0;
    padding-top: 2px;
    font-size: 13.5px;
  }
  .dates {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-wrap: wrap;
  }
  .arrow {
    padding: 0 2px;
  }
  .bare {
    border: 0;
    background: transparent;
    padding: 4px 6px;
    border-radius: 6px;
    max-width: 100%;
  }
  .bare:hover {
    background: var(--card-hover);
  }
  .desc {
    margin: 0 0 8px;
  }
  .adders {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 4px 0 18px;
  }
  .adders button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    border: 1px dashed var(--line);
    background: none;
    color: var(--muted);
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12.5px;
  }
  .adders button:hover {
    border-style: solid;
    color: var(--text);
    background: var(--card-hover);
  }
  .dropzone {
    position: absolute;
    inset: 8px;
    border: 2px dashed var(--accent);
    border-radius: 10px;
    background: color-mix(in srgb, var(--accent) 12%, var(--panel));
    display: grid;
    place-content: center;
    justify-items: center;
    gap: 8px;
    font-size: 16px;
    font-weight: 600;
    color: var(--accent);
    pointer-events: none;
    z-index: 50;
  }

  /* Shared by the task window's sections (subtasks, checklists, attachments). */
  .tw :global(.block) {
    margin: 0 0 20px;
  }
  .tw :global(.block > header) {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }
  .tw :global(.block h3) {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin: 0;
    font-size: 14px;
    font-weight: 600;
  }
  .tw :global(.block .count) {
    color: var(--muted);
    font-weight: 500;
  }
  .tw :global(.progress) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--muted);
  }
  .tw :global(.progress .bar) {
    width: 90px;
    height: 6px;
    border-radius: 3px;
    background: var(--chip);
    overflow: hidden;
  }
  .tw :global(.progress .bar > span) {
    display: block;
    height: 100%;
    background: var(--kind-done);
    border-radius: 3px;
  }
  .tw :global(.block > header .menu) {
    margin-left: auto;
  }

  @media (max-width: 1000px) {
    .fields {
      grid-template-columns: 1fr;
    }
    .meta {
      display: none;
    }
  }
  /* A narrow window (a dock under 760px, the phone): the activity stacks under the task. */
  .tw.stacked .tw-body {
    display: block;
    overflow-y: auto;
  }
  .tw.stacked .main {
    overflow: visible;
    padding: 0;
  }
  .tw.stacked .tw-head {
    padding: 16px 16px 0;
  }
  .tw.stacked .tw-rest {
    padding: 0 16px 8px;
  }
  /* An agent is on the card: the thread and its composer come straight after the title. The
     head, the conversation and the rest are siblings for the column, in that order. */
  .tw.stacked .tw-body.agent-first {
    display: flex;
    flex-direction: column;
  }
  .tw.stacked .agent-first .main {
    display: contents;
  }
  .tw.stacked .agent-first .tw-head,
  .tw.stacked .agent-first .tw-rest,
  .tw.stacked .agent-first :global(.activity) {
    flex: none;
  }
  .tw.stacked .agent-first .tw-head {
    order: 0;
  }
  .tw.stacked .agent-first :global(.activity) {
    order: 1;
    min-height: 0;
    border-bottom: 1px solid var(--line);
  }
  .tw.stacked .agent-first .tw-rest {
    order: 2;
    padding-top: 14px;
  }
  .tw.stacked .tw-body :global(.activity) {
    border-left: 0;
    border-top: 1px solid var(--line);
    min-height: 60dvh;
  }
  .tw.stacked .field,
  .tw.stacked .field.wide {
    grid-template-columns: 110px minmax(0, 1fr);
  }
  .tw.stacked .fields {
    grid-template-columns: 1fr;
  }
  .tw.stacked .title {
    font-size: 21px;
  }
  .tw.stacked .crumb {
    max-width: 140px;
  }
  .tw.stacked .meta {
    display: none;
  }
  /* Phone and installed app: the window is the screen. */
  @media (max-width: 760px) {
    .tw {
      top: 0;
      left: 0;
      translate: none;
      width: 100vw;
      height: calc(100dvh - var(--kb, 0px));
      border: 0;
      border-radius: 0;
      padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom)
        env(safe-area-inset-left);
    }
    /* The keyboard covers the home indicator: no bottom inset while it is up. */
    :global(html.keyboard-open) .tw {
      padding-bottom: 0;
    }
  }
</style>
