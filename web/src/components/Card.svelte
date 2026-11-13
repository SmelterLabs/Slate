<script lang="ts">
  // A board card in ClickUp's shape: title, a row of small counts (description, subtasks,
  // comments…), then a wrap of property pills. Which pills show is the view's `card_fields`.
  // The due and priority pills, the ⋯ menu and right-click become live on first hover, so a
  // board of hundreds of cards mounts no menus until they are needed.
  import { cardDue, cardStart, tagColor } from '../lib/cards';
  import { priorityLabel } from '../lib/priority';
  import { DOT_TITLE } from '../lib/commentdots';
  import { copyText } from '../lib/clipboard';
  import { itemHasDraft } from '../lib/drafts.svelte';
  import { blockedTitle, formatEstimate, openBlockers } from '../lib/format';
  import { shortRule } from '../lib/rrule';
  import { board } from '../lib/store.svelte';
  import type { Item, Tag } from '../lib/types';
  import { isClosed, type ViewConfig } from '../lib/views';
  import AgentBadge from './AgentBadge.svelte';
  import Avatars from './Avatars.svelte';
  import CardMenu from './CardMenu.svelte';
  import DuePicker from './DuePicker.svelte';
  import Icon from './Icon.svelte';
  import PriorityPicker from './PriorityPicker.svelte';
  import RunNow from './RunNow.svelte';
  import { RUN_TITLE } from '../lib/runmode';
  import { DEPENDS_ON, LEAVES, waitingOn } from '../lib/words';
  import { needOf } from '../lib/attention';
  import { rowHandlers } from '../lib/itemrow';

  let {
    item,
    config,
    showList = false,
    tagMap = {},
    slotBefore = false,
  }: {
    item: Item;
    config: ViewConfig;
    showList?: boolean;
    tagMap?: Record<string, Tag>;
    /** A dragged card would land just above this one (manual order). */
    slotBefore?: boolean;
  } = $props();

  let dragging = $state(false);
  let armed = $state(false);
  let menuOpen = $state(false);
  const closed = $derived(isClosed(item));
  const pickup = $derived(board.pickup(item));
  const show = $derived(new Set(config.card_fields));
  const selected = $derived(board.selected.has(item.id));
  const due = $derived(item.due_at ? cardDue(item.due_at, board.now, closed) : null);
  const waitingFor = $derived(item.waiting_on_account_id ? board.accounts[item.waiting_on_account_id] : null);
  // An agent's blocked badge already says "Waiting on Alex": the pill would say it twice.
  const waitingInBadge = $derived(item.agent?.state === 'blocked' && !!item.waiting_on_account_id);
  // Why this card is in the viewer's "Needs you" (the same rule as the pill and the sidebar
  // count): the card gets that tone's edge, and a wait on the viewer gets its own chip under the
  // title, whatever the card's field settings, unless the agent badge already says it.
  const need = $derived(needOf(item, board.me?.account.id ?? '', board.accounts));
  const waitsOnMe = $derived(need === 'waiting' && !waitingInBadge);
  const blockers = $derived(item.blocked && !closed ? openBlockers(item, board.items) : []);
  const hasBody = $derived(!!item.body_md.trim());
  // Open Asks (assigned comments) addressed to whoever is looking at the board.
  const dot = $derived(board.commentDots[item.id]);
  const askedMe = $derived((item.open_asks ?? []).filter((id) => id === board.me?.account.id).length);
  const counts = $derived(
    (show.has('description') && hasBody) ||
      (show.has('subtasks') && item.subtask_count > 0) ||
      (show.has('comments') && item.comment_count > 0) ||
      (show.has('checklist') && !!item.checklist_total) ||
      (show.has('attachments') && !!item.attachment_count),
  );

  let copied = $state(false);
  let copiedTimer: ReturnType<typeof setTimeout> | undefined;
  async function copyId(e: MouseEvent) {
    e.stopPropagation();
    if (!(await copyText(item.id))) return board.flash('Could not copy the ID');
    copied = true;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => (copied = false), 1400);
  }
</script>

<!-- A div, not a button: the circle, checkbox, pills and menu are buttons inside it. -->
<div
  class="card cu prio-stripe-{item.priority ?? 'none'}"
  class:overdue={due?.tone === 'overdue'}
  class:dragging
  class:slot-before={slotBefore}
  class:closed
  class:subtask={!!item.parent_id}
  class:selected
  class:compact={config.density === 'compact'}
  class:menu-open={menuOpen}
  class:needs-me={!!need}
  data-need={need ?? undefined}
  style:--lines={config.title_lines || 'none'}
  draggable="true"
  role="button"
  tabindex="0"
  data-item={item.id}
  {...rowHandlers(item.id, { arm: () => (armed = true), menu: () => (menuOpen = true), drag: (on) => (dragging = on) })}
>
  <!-- Multi-select sits on the top-left corner, clear of the title and the subtask line. -->
  <span class="card-select">
    <input
      class="select"
      type="checkbox"
      aria-label="Select"
      title="Select (x)"
      checked={selected}
      onclick={(e) => {
        e.stopPropagation();
        board.toggleSelect(item.id, e.shiftKey);
        // Let the click tick the box, then match it to the selection (a shift-range can differ).
        e.currentTarget.checked = board.selected.has(item.id);
      }}
    />
  </span>

  <span class="card-tools">
    <button
      class="check"
      class:done={closed}
      aria-label={closed ? 'Mark not done' : 'Complete'}
      title={closed ? 'Mark not done' : `Complete. ${LEAVES.complete}`}
      onclick={(e) => {
        e.stopPropagation();
        void board.toggleComplete(item.id);
      }}><Icon name="check" size={11} /></button
    >
    {#if board.canRun(item) && !(item.id in board.runRequested)}
      <button
        class="card-run"
        aria-label={board.runLabel(item)}
        title={RUN_TITLE[board.runMode(item) ?? 'run']}
        onclick={(e) => {
          e.stopPropagation();
          void board.runAction(item);
        }}><Icon name="robot" size={13} /></button
      >
    {/if}
    <!-- Until armed, an empty slot holds the menu's place so Complete and Run never shift
         under a finger mid-tap (the tap's focus arms the card). -->
    {#if armed}<CardMenu {item} bind:open={menuOpen} />{:else}<span class="card-more card-more-slot" aria-hidden="true"
      ></span>{/if}
  </span>

  {#if item.parent_id}
    {@const parent = board.items[item.parent_id]}
    <button
      class="card-parent"
      title={parent ? `Subtask of ${parent.title}. Open it` : 'Subtask'}
      onclick={(e) => {
        e.stopPropagation();
        if (parent) board.open(parent.id);
      }}><Icon name="subtask" size={11} /><span class="card-parent-of">Subtask of</span>
      <span class="card-parent-name">{parent?.title ?? 'another task'}</span></button
    >
  {/if}

  <span class="card-title" class:clamped={config.title_lines > 0}>{item.title}</span>

  {#if item.agent}<span class="card-agent"><AgentBadge flag={item.agent} {item} /></span>{/if}
  {#if (pickup || item.id in board.runRequested) && item.agent?.state !== 'working'}<span class="card-agent"
      ><RunNow {item} /></span
    >{/if}

  {#if itemHasDraft(item.id)}<span class="card-draft" title="You started typing on this card and didn't send it"
      ><Icon name="edit" size={11} />Draft</span
    >{/if}

  {#if waitsOnMe}<span class="card-needs" title="This card is waiting on you: open it to answer" data-waits-on-me
      ><Icon name="hourglass" size={11} />{waitingOn(board.me?.account.display_name)}</span
    >{/if}

  {#if askedMe}<span class="card-ask" title="{askedMe} open question{askedMe > 1 ? 's' : ''} for you" data-asks={askedMe}
      >?&nbsp;{askedMe}</span
    >{/if}

  {#if counts}
    <span class="card-counts">
      {#if show.has('description') && hasBody}<span class="count-ic" title="Has a description"
          ><Icon name="lines" /></span
        >{/if}
      {#if show.has('subtasks') && item.subtask_count > 0}<span class="count-ic" title="Subtasks"
          ><Icon name="subtasks" />{item.subtask_count}</span
        >{/if}
      {#if show.has('comments') && item.comment_count > 0}<span class="count-ic" title="Comments"
          ><Icon name="comment" />{item.comment_count}</span
        >{/if}
      {#if show.has('checklist') && item.checklist_total}<span
          class="count-ic"
          class:all-done={item.checklist_done === item.checklist_total}
          title="Checklist"><Icon name="checklist" />{item.checklist_done ?? 0}/{item.checklist_total}</span
        >{/if}
      {#if show.has('attachments') && item.attachment_count}<span class="count-ic" title="Attachments"
          ><Icon name="clip" />{item.attachment_count}</span
        >{/if}
    </span>
  {/if}

  <span class="pills">
    {#if show.has('due') && due}
      {#if armed}
        <DuePicker {item}>
          <span class="due tone-{due.tone}" class:overdue={due.tone === 'overdue'}
            ><Icon name="calendar" />{#if item.recurrence_id}<Icon name="repeat" size={12} />{/if}{due.text}</span
          >
        </DuePicker>
      {:else}
        <span class="pill due tone-{due.tone}" class:overdue={due.tone === 'overdue'}
          ><Icon name="calendar" />{#if item.recurrence_id}<Icon name="repeat" size={12} />{/if}{due.text}</span
        >
      {/if}
    {/if}
    {#if show.has('start') && item.start_at}
      <span class="pill muted-pill" title="Start date"><Icon name="start" size={12} />{cardStart(item.start_at, board.now)}</span>
    {/if}
    {#if show.has('priority') && item.priority}
      {#if armed}
        <PriorityPicker {item}>
          <span class="prio prio-flag-{item.priority}"><Icon name="flag" fill />{priorityLabel(item.priority)}</span>
        </PriorityPicker>
      {:else}
        <span class="pill prio prio-flag-{item.priority}"
          ><Icon name="flag" fill />{priorityLabel(item.priority)}</span
        >
      {/if}
    {/if}
    {#if show.has('tags') && item.tags.length}
      <span class="pill tags"
        ><Icon name="tag" />{#each item.tags as t (t)}{@const c = tagColor(t, tagMap)}<span
            class="tag-pill"
            style:background={c.bg}
            style:color={c.fg}>{t}</span
          >{/each}</span
      >
    {/if}
    {#if show.has('status') && item.status}
      <span class="pill status-pill" title="Status"
        ><span class="status-dot" style:background={item.status.color ?? `var(--kind-${item.status.kind})`}
        ></span>{item.status.name}</span
      >
    {/if}
    {#if show.has('assignees') && item.assignees.length}
      <span class="pill people"><Avatars people={item.assignees} /></span>
    {/if}
    {#if showList && show.has('list') && board.lists[item.list_id]}
      <span class="pill muted-pill list-chip" title="List"><Icon name="list" />{board.lists[item.list_id].name}</span>
    {/if}
    {#if show.has('waiting') && waitingFor && !waitingInBadge && !waitsOnMe}
      <span class="pill muted-pill" title="Waiting on {waitingFor.display_name}" data-waiting
        ><Icon name="hourglass" />{waitingOn(waitingFor.display_name)}</span
      >
    {/if}
    {#if show.has('recurrence') && item.recurrence_id}
      {@const rule = board.recurrences[item.recurrence_id]}
      <span class="pill muted-pill" title={rule?.description ? `Repeats ${rule.description}` : 'Recurring'} data-repeat
        ><Icon name="repeat" />{rule ? shortRule(rule) : 'Recurring'}</span
      >
    {/if}
    {#if show.has('estimate') && item.estimate_minutes}
      <span class="pill muted-pill" title="Time estimate"><Icon name="hourglass" />{formatEstimate(item.estimate_minutes)}</span>
    {/if}
    {#if item.duplicate_of?.length && !item.archived_at}
      <span class="pill muted-pill" title="Marked as a duplicate of {board.items[item.duplicate_of[0]]?.title ?? 'another task'}"
        ><Icon name="copy" size={12} /> Duplicate</span
      >
    {/if}
    {#if show.has('blocked') && item.blocked && !closed}
      <span class="pill blocked-badge" data-blocked title={blockedTitle(blockers)}
        ><Icon name="alert" size={12} /><span class="dep-text"
          >{DEPENDS_ON}{#if blockers.length}&nbsp;<span class="dep-name">{blockers[0].title}</span>{/if}</span
        >{#if blockers.length > 1}<span class="pill-more">+{blockers.length - 1}</span>{/if}</span
      >
    {/if}
  </span>

  <!-- New comments since you last opened the card (lib/commentdots.ts). Bottom-right, under
       the copy button, which takes its place on hover. -->
  {#if dot}<span class="card-dot dot-{dot}" role="img" aria-label={DOT_TITLE[dot]} title={DOT_TITLE[dot]} data-dot={dot}
    ></span>{/if}

  <!-- Copy the task ID. Its own draggable whose dragstart is cancelled, so pressing it never
       opens the card or picks the card up; data-no-drag keeps the finger drag off it too. -->
  <button
    class="card-copy"
    class:copied
    type="button"
    draggable="true"
    data-no-drag
    aria-label={copied ? 'Copied task ID' : 'Copy task ID'}
    title={copied ? 'Copied!' : `Copy ID ${item.id}`}
    onclick={copyId}
    onkeydown={(e) => e.stopPropagation()}
    ondragstart={(e) => {
      e.preventDefault();
      e.stopPropagation();
    }}
    ><Icon name={copied ? 'check' : 'copy'} size={11} />{#if copied}<span class="card-copy-tip" role="status"
        >Copied</span
      >{/if}</button
  >
</div>

<style>
  .pill-more {
    margin-left: 3px;
    color: var(--muted);
  }
  .blocked-badge {
    max-width: 100%;
    min-width: 0;
  }
  .dep-text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dep-name {
    font-weight: 500;
  }
  .card-agent {
    display: flex;
    min-width: 0;
    margin: 2px 0 1px;
  }
</style>
