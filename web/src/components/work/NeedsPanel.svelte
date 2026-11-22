<script lang="ts">
  // "Needs you" on My Work and the Inbox (grouped by reason), and in the bottom-right pill's
  // pop-up (rows): each card that waits on the viewer, colored by why
  // (lib/attention.ts), with the agent's note and the one action that moves it on.
  import { countBy, needLabel, NEEDS, type Attention, type Need } from '../../lib/attention';
  import { askQuestions } from '../../lib/conversation';
  import { ago } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import Icon, { type IconName } from '../Icon.svelte';

  let { layout = 'rows' }: { layout?: 'rows' | 'groups' } = $props();

  const list = $derived(board.needsMe);
  const counts = $derived(countBy(list));
  const LABEL = $derived(
    Object.fromEntries(NEEDS.map((n) => [n.key, needLabel(n.key, board.me?.account.display_name)])) as Record<Need, string>,
  );
  const ICON: Record<Need, IconName> = { waiting: 'hourglass', blocked: 'alert', asked: 'at', review: 'eye', stalled: 'clock' };
  let cleared = $state<Set<string>>(new Set());

  function when(a: Attention): string {
    return ago(a.item.agent?.at ?? a.item.updated_at, board.now);
  }
  async function reviewed(e: MouseEvent, a: Attention) {
    e.stopPropagation();
    cleared = new Set(cleared).add(a.item.id);
    await task.clearAgentFlag(a.item.id);
  }
  function runStalled(e: MouseEvent, a: Attention) {
    e.stopPropagation();
    void board.runAction(a.item);
  }
  const shown = $derived(list.filter((a) => !cleared.has(a.item.id)));
  const projects = $derived(board.projectNeeds ?? []);
  const total = $derived(shown.length + projects.length);
  const groups = $derived(
    NEEDS.map((n) => ({ ...n, label: LABEL[n.key], rows: shown.filter((a) => a.need === n.key) })).filter((g) => g.rows.length),
  );
</script>

{#snippet action(a: Attention)}
  {#if a.need === 'review'}
    <button class="nd-act" data-mark-reviewed onclick={(e) => reviewed(e, a)}>✓ Mark reviewed</button>
  {:else if a.need === 'asked'}
    <span class="nd-act ghost">Reply →</span>
  {:else if a.need === 'waiting'}
    <span class="nd-act ghost">Answer →</span>
  {:else if a.need === 'stalled'}
    {#if board.canRun(a.item)}
      <button class="nd-act" data-stalled-run onclick={(e) => runStalled(e, a)}>{board.runLabel(a.item)}</button>
    {:else}
      <span class="nd-act ghost">Open →</span>
    {/if}
  {:else}
    <span class="nd-act ghost">Unblock →</span>
  {/if}
{/snippet}

{#snippet row(a: Attention)}
  {@const viewer = board.me?.account.id}
  {@const ask = viewer && a.item.waiting_on_account_id === viewer ? a.item.ask : null}
  {@const questions = ask ? askQuestions(ask) : []}
  {@const options = questions.length === 1 ? questions[0].options : []}
  {@const by = ask?.by_id ?? a.item.agent?.by}
  {@const who = by ? board.accountName(by) : null}
  {@const note = a.need === 'review'
    ? a.item.standing?.line || a.item.agent?.note
    : a.item.ask && !ask ? null : a.item.agent?.note}
  <div
    class="nd-row need-{a.need}"
    class:has-opts={options.length > 0}
    role="listitem"
    data-item={a.item.id}
    data-need={a.need}
  >
    <button class="nd-main" onclick={() => board.open(a.item.id)}>
      <span class="nd-top">
        <span class="nd-chip"><Icon name={ICON[a.need]} size={11} />{LABEL[a.need]}</span>
        {#if ask}
          <span class="nd-title" data-ask-question>{ask.question}</span>
        {:else}
          <span class="nd-title">{a.item.title}</span>
        {/if}
      </span>
      <span class="nd-meta">
        {#if ask}
          {#if who}<Icon name="robot" size={11} /><b>{who}</b><span class="nd-note">{a.item.title}</span>{:else}<span>{a.item.title}</span>{/if}
        {:else if who}<Icon name="robot" size={11} /><b>{who}</b>{#if note}<span class="nd-note">{note}</span>{/if}{:else}<span
            >{board.lists[a.item.list_id]?.name ?? ''}</span
          >{/if}
      </span>
    </button>
    <span class="nd-when">{when(a)}</span>
    {#if ask && options.length}
      <span class="nd-opts">
        {#each options as option}
          <button class="nd-act" data-answer-option={option} onclick={(e) => {
            e.stopPropagation();
            void task.answer(ask.comment_id, option);
          }}>{option}</button>
        {/each}
      </span>
    {:else}
      {@render action(a)}
    {/if}
  </div>
{/snippet}

{#if total}
  <section class="needs layout-{layout}" aria-label="Needs you" data-needs={total}>
    <header class="nd-head">
      <h2><span class="nd-pulse" aria-hidden="true"></span>Needs you <span class="nd-n">{total}</span></h2>
      <span class="nd-legend">
        {#each NEEDS as n (n.key)}
          {#if counts[n.key]}<span class="need-{n.key}"><i></i>{counts[n.key]} {LABEL[n.key]}</span>{/if}
        {/each}
        {#if projects.length}<span class="need-blocked"><i></i>{projects.length} No next step</span>{/if}
      </span>
    </header>

    {#if layout === 'groups'}
      {#each groups as g (g.key)}
        <div class="nd-group need-{g.key}">
          <h3><i></i>{g.label} <span>{g.rows.length}</span></h3>
          <div role="list">{#each g.rows as a (a.item.id)}{@render row(a)}{/each}</div>
        </div>
      {/each}
      {#if projects.length}
        <div class="nd-group need-blocked">
          <h3><i></i>Projects <span>{projects.length}</span></h3>
          <div role="list">
            {#each projects as p (p.section.id)}
              <div class="nd-row need-blocked" role="listitem" data-project-need={p.section.id}>
                <a class="nd-main" href="#/section/{p.section.id}">
                  <span class="nd-top"><span class="nd-title">No next step: {p.section.name}</span></span>
                </a>
                <span class="nd-act ghost">Open →</span>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    {:else}
      <div role="list">
        {#each shown as a (a.item.id)}{@render row(a)}{/each}
        {#each projects as p (p.section.id)}
          <div class="nd-row need-blocked" role="listitem" data-project-need={p.section.id}>
            <a class="nd-main" href="#/section/{p.section.id}">
              <span class="nd-top">
                <span class="nd-chip"><Icon name="alert" size={11} />Project</span>
                <span class="nd-title">No next step: {p.section.name}</span>
              </span>
            </a>
            <span class="nd-act ghost">Open →</span>
          </div>
        {/each}
      </div>
    {/if}
  </section>
{/if}

<style>
  .needs {
    --t-waiting: var(--need-waiting);
    --t-blocked: var(--need-blocked);
    --t-asked: var(--need-asked);
    --t-review: var(--need-review);
    --t-stalled: var(--need-stalled);
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--panel);
    padding: 12px 12px 8px;
    margin-bottom: 16px;
  }
  .need-waiting,
  .need-blocked {
    --tone: var(--t-waiting);
  }
  .need-asked {
    --tone: var(--t-asked);
  }
  .need-review {
    --tone: var(--t-review);
  }
  .need-stalled {
    --tone: var(--t-stalled);
  }
  .nd-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 8px;
  }
  h2 {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.95rem;
    margin: 0;
  }
  .nd-n {
    font-size: 12px;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--prio-high) 22%, transparent);
    color: var(--text);
  }
  .nd-pulse {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--prio-high);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--prio-high) 25%, transparent);
  }
  .nd-legend {
    display: flex;
    gap: 12px;
    font-size: 12px;
    color: var(--muted);
  }
  .nd-legend span {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .nd-legend i,
  .nd-group h3 i {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--tone);
  }
  .nd-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 8px 7px 12px;
    margin: 0 -4px;
    border-radius: 8px;
    position: relative;
  }
  .nd-row::before {
    content: '';
    position: absolute;
    left: 2px;
    top: 8px;
    bottom: 8px;
    width: 3px;
    border-radius: 2px;
    background: var(--tone);
  }
  .nd-row:hover {
    background: var(--card-hover);
  }
  .nd-main {
    all: unset;
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
    flex: 1;
  }
  .nd-top {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .nd-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex: none;
    font-size: 11px;
    font-weight: 600;
    padding: 1px 7px;
    border-radius: 999px;
    color: var(--tone);
    background: color-mix(in srgb, var(--tone) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--tone) 35%, transparent);
  }
  .nd-title {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .nd-meta {
    display: flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
    color: var(--muted);
    min-width: 0;
  }
  .nd-meta b {
    color: var(--text);
    font-weight: 600;
  }
  .nd-note {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .nd-meta .nd-note::before {
    content: '· ';
  }
  .nd-when {
    flex: none;
    font-size: 12px;
    color: var(--faint);
  }
  .nd-meta .nd-when {
    margin-left: auto;
  }
  .nd-row .nd-act {
    min-width: 118px;
    text-align: center;
  }
  .nd-row.has-opts {
    flex-wrap: wrap;
    row-gap: 6px;
  }
  .nd-opts {
    display: flex;
    gap: 6px;
    flex: 1 0 100%;
    flex-wrap: wrap;
    min-width: 0;
  }
  .nd-opts .nd-act {
    min-width: 0;
    min-height: 28px;
    max-width: 100%;
    white-space: normal;
    text-align: left;
  }
  .nd-act {
    flex: none;
    font: inherit;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 6px;
    border: 1px solid color-mix(in srgb, var(--tone) 55%, transparent);
    background: color-mix(in srgb, var(--tone) 16%, transparent);
    color: var(--text);
    cursor: pointer;
  }
  .nd-act:hover {
    background: color-mix(in srgb, var(--tone) 28%, transparent);
  }
  .nd-act.ghost {
    border-color: transparent;
    background: none;
    color: var(--tone);
    cursor: inherit;
    pointer-events: none;
  }
  /* groups */
  /* the group's heading already says why */
  .nd-group .nd-chip {
    display: none;
  }
  .nd-group + .nd-group {
    margin-top: 6px;
  }
  .nd-group h3 {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--tone);
    margin: 6px 0 2px;
  }
  .nd-group h3 span {
    color: var(--muted);
  }
  @media (max-width: 720px) {
    .nd-legend {
      display: none;
    }
    .nd-row {
      flex-wrap: wrap;
      row-gap: 6px;
    }
    .nd-main {
      flex-basis: 100%;
    }
    .nd-top {
      flex-wrap: wrap;
      row-gap: 3px;
    }
    .nd-title {
      white-space: normal;
    }
    .nd-row > .nd-when {
      display: none;
    }
    .nd-row .nd-act {
      min-width: 0;
    }
  }
</style>
