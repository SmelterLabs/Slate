<script lang="ts">
  // The Agents page (#/agents): every agent account with its presence and what it is on now,
  // and a live feed of what agents did, newest first. The roster comes from GET /agents; the
  // feed is seeded from GET /events?tail=true&actor_kind=agent and then follows the board's one
  // SSE stream (lib/agents.svelte.ts), so nothing here polls. Admins also connect agents here
  // (account + scoped token + setup snippets, agents/ConnectAgent) and see each agent's usage
  // and tokens (agents/AgentManage).
  import { onDestroy, onMount } from 'svelte';
  import { agents, isOnline, STATE_LABEL } from '../lib/agents.svelte';
  import { get } from '../lib/api';
  import { shownProgress } from '../lib/conversation';
  import { ago, fullStamp } from '../lib/format';
  import { verb } from '../lib/history';
  import { heldFor, movesPlan, planWhen, REASON_LABEL, type AgentPlan, type PlanOut } from '../lib/plan';
  import { board } from '../lib/store.svelte';
  import type { AgentEntry, SlateEvent } from '../lib/types';
  import Icon from './Icon.svelte';
  import AgentManage from './agents/AgentManage.svelte';
  import AgentWake from './agents/AgentWake.svelte';
  import AgentConnections from './agents/AgentConnections.svelte';
  import { canManageConnections } from '../lib/connections';
  import ConnectAgent from './agents/ConnectAgent.svelte';
  import CapacityEditor from './CapacityEditor.svelte';
  import { capacityOf } from '../lib/workload';

  interface Stats {
    routes: { method: string; route: string; count: number; p50_ms: number; p95_ms: number }[];
    live_streams: number;
  }

  let only = $state<string | null>(null); // filter the feed to one agent
  let connect = $state<{ preset: string | null } | null>(null); // the Connect dialog
  let managing = $state<string | null>(null); // the agent whose usage and tokens are open
  let sizing = $state<string | null>(null); // the agent whose capacity editor is open

  /** "8h a day · 2 tasks a day · every day": what this agent can take on (GET /workload/boxes). */
  function capacityText(account: AgentEntry['account']): string {
    const c = capacityOf(account, board.capacities);
    const parts = [
      c.hours_per_day !== null ? `${c.hours_per_day}h a day` : null,
      c.tasks_per_day !== null ? `${c.tasks_per_day} task${c.tasks_per_day === 1 ? '' : 's'} a day` : null,
    ].filter(Boolean);
    const days =
      c.work_days.length === 7 ? 'every day' : c.work_days.join() === '0,1,2,3,4' ? 'weekdays' : `${c.work_days.length} days a week`;
    return `${parts.length ? parts.join(' · ') : 'No limit'} · ${days}`;
  }
  const admin = $derived(canManageConnections(board.me));
  const privatePresence = $derived(!!board.me?.access && !board.me.access.all);
  let stats = $state<Stats | null>(null);

  onMount(() => {
    void agents.load();
    void loadStats();
    void loadPlan();
  });

  async function loadStats() {
    try {
      stats = await get<Stats>('/stats');
    } catch {
      stats = null;
    }
  }

  // "Next up": what each agent is about to run (GET /agents/plan), re-read after any event
  // that can move a task's gate or an Ask, debounced like the audit log's live refresh.
  let plan = $state<AgentPlan[] | null>(null);
  let planGeneration = 0;
  async function loadPlan() {
    const generation = ++planGeneration;
    try {
      const out = await get<PlanOut>('/agents/plan');
      if (generation === planGeneration) plan = out.agents;
    } catch {
      if (generation === planGeneration) plan = plan ?? [];
    }
  }
  let planTimer: ReturnType<typeof setTimeout> | undefined;
  const offPlan = board.onEvent((ev) => {
    if (!movesPlan(ev.type)) return;
    clearTimeout(planTimer);
    planTimer = setTimeout(() => void loadPlan(), 400);
  });
  onDestroy(() => {
    offPlan();
    clearTimeout(planTimer);
  });
  const upcoming = $derived((plan ?? []).filter((a) => a.next.length || a.leases.length));

  const busy = $derived(agents.agents.flatMap((a) => a.working));
  const online = $derived(privatePresence ? [] : agents.agents.filter((a) => isOnline(a, board.now)));
  const blocked = $derived(busy.filter((w) => w.state === 'blocked').length);
  const review = $derived(busy.filter((w) => w.state === 'review').length);
  // One number for "how fast is Slate for agents": the median of the busiest routes, weighted.
  const median = $derived.by(() => {
    if (!stats?.routes.length) return null;
    const total = stats.routes.reduce((n, r) => n + r.count, 0);
    return stats.routes.reduce((ms, r) => ms + (r.p50_ms * r.count) / total, 0);
  });
  const feed = $derived(only ? agents.feed.filter((e) => e.actor_id === only) : agents.feed);

  const ctx = {
    name: (id: string) => board.accountName(id) ?? agents.entry(id)?.account.display_name,
    status: (id: string) => {
      for (const l of Object.values(board.lists)) {
        const s = l.statuses.find((x) => x.id === id);
        if (s) return s.name;
      }
      return undefined;
    },
    title: (id: string) => board.items[id]?.title,
  };

  function presence(a: AgentEntry): { tone: string; text: string } {
    if (privatePresence) return { tone: 'unknown', text: 'Activity in your shared lists; private presence unknown' };
    if (a.live_streams > 0) return { tone: 'on', text: 'Live now' };
    const last = [a.last_seen_at, a.last_event_at].filter(Boolean).sort().at(-1);
    if (!last) return { tone: 'off', text: 'Never seen' };
    const via = a.last_seen_via && a.last_seen_via !== 'stream' ? ` via ${a.last_seen_via.toUpperCase()}` : '';
    return { tone: isOnline(a, board.now) ? 'on' : 'off', text: `Seen ${ago(last, board.now)}${via}` };
  }

  function who(id: string): string {
    return ctx.name(id) ?? 'An agent';
  }

  function itemTitle(ev: SlateEvent): string | null {
    if (!ev.item_id) return null;
    const after = ev.after as { title?: string } | null;
    return board.items[ev.item_id]?.title ?? (typeof after?.title === 'string' ? after.title : null);
  }
</script>

<section class="agents-page" aria-label="Agents">
  <header class="ag-head">
    <div>
      <h1>Agents</h1>
      <p class="muted">Who is around, what each one is on, and everything they did, live.</p>
    </div>
    {#if admin}
      <button class="primary connect" onclick={() => (connect = { preset: null })}>
        <Icon name="plus" size={14} /> Add my agent
      </button>
    {/if}
    <div class="ag-stats" aria-label="Summary">
      <div class="stat"><b>{privatePresence ? "—" : online.length}</b><span>{privatePresence ? "presence not shared" : "recent activity"}</span></div>
      <div class="stat"><b>{busy.length}</b><span>in progress</span></div>
      <div class="stat" class:warn={blocked > 0}><b>{blocked}</b><span>blocked</span></div>
      <div class="stat" class:note={review > 0}><b>{review}</b><span>to review</span></div>
      {#if median != null}
        <div class="stat" title="Median server time per request since Slate started (GET /api/v1/stats)">
          <b>{median.toFixed(1)}<small> ms</small></b><span>API median</span>
        </div>
      {/if}
    </div>
  </header>

  {#if agents.error && !agents.loaded}
    <p class="muted pad">Could not load agents: {agents.error}</p>
  {:else if !agents.loaded}
    <p class="muted pad">Loading…</p>
  {:else}
    <div class="ag-body">
      <div class="roster">
        {#if !agents.agents.length}
          <div class="empty">
            <p><b>No agent accounts yet.</b></p>
            <p class="muted">
              {#if admin}
                <b>Add my agent</b> guides you through connecting a computer, adding Slate to an agent app, or passing tasks by hand.
              {:else}
                An admin connects agents from this page.
              {/if}
              Agents report progress with <code>POST /items/&lbrace;id&rbrace;/report</code> or the MCP
              <code>slate_item report</code> action; it shows here and on the card.
            </p>
            {#if admin}
              <button class="primary" onclick={() => (connect = { preset: null })}>Add my agent</button>
            {/if}
          </div>
        {/if}
        {#each agents.agents as a (a.account.id)}
          {@const p = presence(a)}
          {@const open = managing === a.account.id}
          <article class="agent" class:on={p.tone === 'on'} class:picked={only === a.account.id} class:wide={open}>
            <header>
              <span class="avatar" aria-hidden="true"><Icon name="robot" size={18} /><span class="presence {p.tone}"></span></span>
              <span class="names">
                <b>{a.account.display_name}</b>
                <span class="muted">@{a.account.handle}</span>
              </span>
              <button
                class="ghost filter"
                aria-pressed={only === a.account.id}
                title="Show only this agent in the feed"
                onclick={() => (only = only === a.account.id ? null : a.account.id)}
                >{only === a.account.id ? 'All' : 'Feed'}</button
              >
              {#if admin || board.me?.account.id === a.account.id}
                <button
                  class="ghost filter"
                  aria-expanded={open}
                  title="Usage and tokens"
                  onclick={() => (managing = open ? null : a.account.id)}>{open ? 'Close' : 'Manage'}</button
                >
              {/if}
            </header>
            <p class="seen muted">{p.text}{#if a.live_streams > 1} · {a.live_streams} streams{/if}</p>
            <div class="chips">
              <span class="chip" title="Open items assigned">{a.open_assigned} assigned</span>
              <span class="chip" title="Changes in the last 24 hours">{a.events_24h} change{a.events_24h === 1 ? '' : 's'} / 24 h</span>
              {#if a.throttled}
                <span class="chip warn" title="Requests refused by the rate limit since Slate started"
                  >{a.throttled} throttled</span
                >
              {/if}
            </div>
            {#if a.working.length}
              <ul class="now">
                {#each a.working as w (w.item_id)}
                  <li class="state-{w.state}">
                    <button class="link" onclick={() => board.open(w.item_id)}>
                      <span class="dot" aria-hidden="true"></span>
                      <span class="what">{STATE_LABEL[w.state]}{#if shownProgress(w.progress) != null}&nbsp;{shownProgress(w.progress)}%{/if}</span>
                      <span class="title">{w.title}</span>
                    </button>
                    {#if w.note}<span class="note muted">{w.note}</span>{/if}
                    {#if shownProgress(w.progress) != null}<span class="bar"><span style:width="{shownProgress(w.progress)}%"></span></span>{/if}
                  </li>
                {/each}
              </ul>
            {:else}
              <p class="idle muted">Not on anything right now.</p>
            {/if}
            {#if a.account.kind === 'agent'}
              <AgentConnections entry={a} {admin} onconnect={() => (connect = { preset: a.account.id })} />
              {#if !privatePresence}<AgentWake account={a.account} wake={a.wake ?? 'push'} {admin} onchange={() => void agents.load()} />{/if}
            {/if}
            <div class="agent-capacity">
              <span class="muted small" title="How much this agent can take on in a day">
                <Icon name="users" size={13} /> Capacity: {capacityText(a.account)}
              </span>
              {#if admin || board.me?.account.id === a.account.id}
                <button
                  class="link small"
                  aria-expanded={sizing === a.account.id}
                  aria-label="Set capacity for {a.account.display_name}"
                  onclick={() => (sizing = sizing === a.account.id ? null : a.account.id)}>{sizing === a.account.id ? 'Close' : 'Set'}</button
                >
              {/if}
            </div>
            {#if sizing === a.account.id}
              <CapacityEditor account={a.account} onclose={() => (sizing = null)} />
            {/if}
            {#if open}
              <AgentManage
                account={a.account}
                {admin}
                onnewtoken={() => (connect = { preset: a.account.id })}
                ondisabled={() => {
                  managing = null;
                  void agents.load();
                }}
              />
            {/if}
          </article>
        {/each}
        {#if agents.people.length}
          <p class="people muted">
            People: {#each agents.people as a, i (a.account.id)}{i ? ', ' : ''}{a.account.display_name}
              <span class="presence-inline {presence(a).tone}" title={presence(a).text}></span>{/each}
          </p>
        {/if}
      </div>

      <div class="side">
      <section class="next-up" aria-label="Next up" data-next-up>
        <h2>Next up <span class="muted">· the next 24 h</span></h2>
        {#if plan === null}
          <p class="muted">Loading…</p>
        {:else if !upcoming.length}
          <p class="muted">Nothing is due to run for any agent in the next day.</p>
        {:else}
          {#each upcoming as a (a.id)}
            <div class="plan-agent" data-plan-agent={a.handle}>
              <b>{a.display_name}</b>
              <ul>
                {#each a.leases as l (l.item_id)}
                  <li class="holding">
                    <span class="when">holding</span>
                    <button class="link item" onclick={() => board.open(l.item_id)}>{l.title ?? l.item_id}</button>
                    <span class="why muted">{heldFor(l.age_seconds)}</span>
                  </li>
                {/each}
                {#each a.next as n (n.item_id)}
                  <li class="reason-{n.reason}" data-plan-reason={n.reason}>
                    <time class="when" datetime={n.at ?? undefined}>{planWhen(n.at, board.now)}</time>
                    <button class="link item" onclick={() => board.open(n.item_id)}>{n.title}</button>
                    <span class="why muted">{REASON_LABEL[n.reason]}{n.list ? ` · ${n.list}` : ''}</span>
                  </li>
                {/each}
              </ul>
            </div>
          {/each}
        {/if}
      </section>
      <div class="feed" aria-label="Agent activity" aria-live="polite">
        <h2>
          Live activity
          {#if only}<span class="muted">· {who(only)}</span>
            <button class="ghost" onclick={() => (only = null)}>Show all</button>{/if}
        </h2>
        {#if !feed.length}
          <p class="muted">Nothing yet. Changes agents make appear here the moment they happen.</p>
        {/if}
        <ol>
          {#each feed as ev (ev.id)}
            {@const t = itemTitle(ev)}
            <li class="ev type-{ev.type.replace('.', '-')}">
              <span class="when muted" title={fullStamp(ev.at)}>{ago(ev.at, board.now)}</span>
              <span class="text">
                <b>{who(ev.actor_id)}</b>
                {verb(ev, { ...ctx, now: board.now })}
                {#if t && ev.item_id}
                  <button class="link item" onclick={() => board.open(ev.item_id!)}>{t}</button>
                {/if}
                {#if ev.source === 'mcp'}<span class="src">MCP</span>{/if}
                {#if ev.session_ref}<span class="src" title="session_ref">{ev.session_ref}</span>{/if}
              </span>
            </li>
          {/each}
        </ol>
      </div>
      </div>
    </div>
  {/if}
</section>

{#if connect}
  <ConnectAgent
    agents={agents.agents.map((a) => a.account)}
    preset={connect.preset}
    onclose={() => {
      connect = null;
      void agents.load();
    }}
    oncreated={() => void agents.load()}
  />
{/if}

<style>
  /* the same page frame as My Work and Inbox (work.css .work-page) */
  .agents-page {
    padding: 20px 24px 48px;
    max-width: 1180px;
    margin: 0 auto;
  }
  .ag-head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 18px;
  }
  h1 {
    margin: 0 0 4px;
    font-size: 22px;
  }
  h1 + p {
    margin: 0;
  }
  .ag-stats {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .stat {
    display: flex;
    flex-direction: column;
    min-width: 76px;
    padding: 8px 12px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    background: var(--panel);
  }
  .stat b {
    font-size: 20px;
    font-variant-numeric: tabular-nums;
  }
  .stat small {
    font-size: 12px;
    font-weight: 400;
    color: var(--muted);
  }
  .stat span {
    font-size: 12px;
    color: var(--muted);
  }
  .stat.warn b {
    color: var(--danger);
  }
  .stat.note b {
    color: var(--prio-high);
  }
  .ag-body {
    display: grid;
    grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr);
    gap: 20px;
    align-items: start;
  }
  .roster {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
  }
  .agent,
  .empty {
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--card);
    padding: 12px 14px;
    min-width: 0;
  }
  .empty {
    grid-column: 1 / -1;
  }
  .agent.on {
    border-color: color-mix(in srgb, var(--kind-done) 45%, var(--line));
  }
  .agent.wide {
    grid-column: 1 / -1;
  }
  .connect {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 7px 14px;
  }
  .empty .primary {
    margin-top: 4px;
    padding: 6px 14px;
  }
  .agent.picked {
    outline: 2px solid var(--accent);
  }
  .agent-capacity {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .agent-capacity :global(svg) {
    vertical-align: -2px;
  }
  .agent header {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .avatar {
    position: relative;
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    border-radius: 50%;
    background: var(--accent-soft);
    color: var(--accent-ink);
    flex: none;
  }
  .presence {
    position: absolute;
    right: -1px;
    bottom: -1px;
    width: 11px;
    height: 11px;
    border-radius: 50%;
    border: 2px solid var(--card);
    background: var(--kind-cancelled);
  }
  .presence.on,
  .presence-inline.on {
    background: var(--kind-done);
  }
  .presence-inline {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--kind-cancelled);
    margin-left: 3px;
  }
  .names {
    display: flex;
    flex-direction: column;
    min-width: 0;
    flex: 1;
  }
  .names b,
  .names span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .filter {
    font-size: 12px;
  }
  .seen {
    margin: 8px 0 6px;
    font-size: 12px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .chip {
    font-size: 12px;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--chip);
  }
  .chip.warn {
    background: color-mix(in srgb, var(--danger) 18%, transparent);
    color: var(--danger);
  }
  .now {
    list-style: none;
    margin: 10px 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .now li {
    --tone: var(--accent);
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 6px 8px;
    border-radius: 8px;
    background: color-mix(in srgb, var(--tone) 10%, transparent);
  }
  .now li.state-blocked {
    --tone: var(--danger);
  }
  .now li.state-review {
    --tone: var(--prio-high);
  }
  .now .link {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    text-align: left;
    background: none;
    border: 0;
    padding: 0;
    color: var(--text);
    cursor: pointer;
    font: inherit;
  }
  .now .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--tone);
    flex: none;
  }
  .now .state-working .dot {
    animation: pulse 1.6s ease-in-out infinite;
  }
  @keyframes pulse {
    50% {
      opacity: 0.35;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .now .state-working .dot {
      animation: none;
    }
  }
  .now .what {
    font-size: 12px;
    font-weight: 600;
    color: var(--tone);
    white-space: nowrap;
  }
  .now .title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .now .note {
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  .bar {
    display: block;
    height: 3px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--tone) 20%, transparent);
    overflow: hidden;
  }
  .bar > span {
    display: block;
    height: 100%;
    background: var(--tone);
  }
  .idle {
    margin: 10px 0 0;
    font-size: 12px;
  }
  .people {
    grid-column: 1 / -1;
    font-size: 12px;
    margin: 4px 2px 0;
  }
  .side {
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-width: 0;
    position: sticky;
    top: 12px;
    max-height: calc(100dvh - 140px);
  }
  .next-up,
  .feed {
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--panel);
    padding: 12px 14px;
    min-width: 0;
  }
  .feed {
    flex: 1;
    min-height: 0;
    overflow: auto;
  }
  .next-up {
    flex: none;
    max-height: 40%;
    overflow: auto;
  }
  .next-up h2 {
    font-size: 14px;
    margin: 0 0 6px;
  }
  .next-up p {
    margin: 0;
    font-size: 13px;
  }
  .plan-agent {
    padding: 6px 0;
    border-top: 1px solid var(--line);
    font-size: 13px;
  }
  .plan-agent:first-of-type {
    border-top: 0;
  }
  .plan-agent ul {
    list-style: none;
    margin: 4px 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .plan-agent li {
    display: grid;
    grid-template-columns: 74px minmax(0, 1fr);
    gap: 2px 8px;
    align-items: baseline;
  }
  .plan-agent .when {
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .plan-agent .reason-run_requested .when,
  .plan-agent .holding .when {
    color: var(--accent);
    font-weight: 600;
  }
  .plan-agent .reason-ask .when {
    color: var(--prio-high);
  }
  .plan-agent .item {
    background: none;
    border: 0;
    padding: 0;
    color: var(--text);
    cursor: pointer;
    font: inherit;
    text-align: left;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .plan-agent .why {
    grid-column: 2;
    font-size: 12px;
  }
  .feed h2 {
    font-size: 14px;
    margin: 0 0 8px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .feed ol {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .ev {
    display: grid;
    grid-template-columns: 74px minmax(0, 1fr);
    gap: 8px;
    padding: 6px 0;
    border-top: 1px solid var(--line);
    font-size: 13px;
    animation: arrive 0.6s ease-out;
  }
  @keyframes arrive {
    from {
      background: color-mix(in srgb, var(--accent) 16%, transparent);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .ev {
      animation: none;
    }
  }
  .ev:first-child {
    border-top: 0;
  }
  .ev .when {
    font-size: 12px;
    white-space: nowrap;
  }
  .ev .text {
    overflow-wrap: anywhere;
  }
  .ev .item {
    background: none;
    border: 0;
    padding: 0;
    color: var(--accent);
    cursor: pointer;
    font: inherit;
    text-align: left;
  }
  .ev .src {
    font-size: 11px;
    margin-left: 4px;
    padding: 0 5px;
    border-radius: 4px;
    background: var(--chip);
    color: var(--muted);
  }
  .type-item-reported {
    font-weight: 500;
  }
  @media (max-width: 900px) {
    .agents-page {
      padding: 14px 12px 32px;
    }
    .ag-body {
      grid-template-columns: minmax(0, 1fr);
    }
    .side {
      position: static;
      max-height: none;
    }
    .next-up {
      max-height: none;
    }
  }
</style>
