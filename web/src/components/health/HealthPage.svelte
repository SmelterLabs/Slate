<script lang="ts">
  // Health (`#/health`, owners and admins): is Slate working, and what broke? One verdict, then
  // every check (problems first), the background jobs with their last success, agent calls and
  // errors per token over the last hour, response times, failed automation runs, queues and
  // storage. Everything comes from GET /health; the server decides each status.
  //
  // Live without polling (AGENTS.md rule 6): a health.* event (a poller started failing, or
  // works again) reloads at once; any other event reloads at most every THROTTLE_MS, since
  // activity is what moves the numbers. Refresh reloads by hand.
  import { onDestroy } from 'svelte';
  import { get } from '../../lib/api';
  import { ago } from '../../lib/format';
  import {
    STATUS_LABEL,
    VERDICT,
    byUrgency,
    bytes,
    caller,
    isHealthEvent,
    ms,
    percent,
    uptime,
    type HealthReport,
  } from '../../lib/health';
  import { board } from '../../lib/store.svelte';
  import EmptyState from '../EmptyState.svelte';
  import Icon from '../Icon.svelte';

  const THROTTLE_MS = 15_000;
  const LAG_LABEL: Record<string, string> = {
    automations: 'Automations engine',
    push: 'Push notifications',
  };

  let report = $state<HealthReport | null>(null);
  let error = $state<string | null>(null);
  let loading = $state(false);
  let last = 0;

  async function load() {
    loading = true;
    last = Date.now();
    try {
      report = await get<HealthReport>('/health');
      error = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    if (board.canManage) void load();
  });

  let timer: ReturnType<typeof setTimeout> | undefined;
  const off = board.onEvent((ev) => {
    if (!board.canManage) return;
    clearTimeout(timer);
    const wait = isHealthEvent(ev.type) ? 300 : Math.max(1000, THROTTLE_MS - (Date.now() - last));
    timer = setTimeout(() => void load(), wait);
  });
  onDestroy(() => {
    off();
    clearTimeout(timer);
  });

  const checks = $derived(report ? byUrgency(report.checks.filter((c) => c.status !== 'off')) : []);
  const offChecks = $derived(report ? report.checks.filter((c) => c.status === 'off') : []);
  const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  const full = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium' });
  const when = (iso: string | null) => (iso ? ago(iso, board.now) : 'never');
</script>

<section class="work-page health-page" aria-label="Health">
  <header class="work-head">
    <div>
      <h1>Health</h1>
      <p class="muted">Background jobs, agent errors and slow calls, failed automations and storage, so you notice breakage before an agent does.</p>
    </div>
    <div class="inline">
      <button class="secondary" onclick={() => void load()} disabled={loading || !board.canManage}>
        <Icon name="refresh" size={14} /> Refresh
      </button>
    </div>
  </header>

  {#if !board.canManage}
    <EmptyState icon="lock" title="Owners and admins only" hint="Ask an owner or admin how Slate is doing, or read /health for the short answer." />
  {:else if error && !report}
    <EmptyState icon="alert" title="Could not read Slate's health" hint={error} />
  {:else if !report}
    <p class="muted pad-row">Loading…</p>
  {:else}
    <div class="health-verdict" data-status={report.status} role="status">
      <span class="health-dot big" data-status={report.status} aria-hidden="true"></span>
      <div>
        <strong>{VERDICT[report.status]}</strong>
        <span class="muted small">
          As of {time.format(new Date(report.at))} · up {uptime(report.uptime_s)} · version {report.version}
          {#if error}· last refresh failed: {error}{/if}
        </span>
      </div>
    </div>

    <div class="health-block">
      <h2>Checks</h2>
      <ul class="health-checks">
        {#each checks as c (c.key)}
          <li data-status={c.status} data-key={c.key}>
            <span class="health-dot" data-status={c.status} title={STATUS_LABEL[c.status]}></span>
            <span class="health-label">{c.label}</span>
            <span class="health-detail muted">{c.detail}</span>
          </li>
        {/each}
      </ul>
      {#if offChecks.length}
        <p class="muted small health-off">Turned off or not set up: {offChecks.map((c) => c.label).join(', ')}.</p>
      {/if}
    </div>

    <div class="health-block">
      <h2>Background jobs</h2>
      <p class="muted small">
        A job that fails {report.alert_after} runs in a row sends owners and admins a notification, and another when it works again.
        {#if report.scheduler.enabled}The scheduler last ticked {when(report.scheduler.last_tick_at)}.{/if}
      </p>
      <div class="health-grid">
        {#each report.pollers as p (p.key)}
          <div class="health-card" data-status={p.status} data-poller={p.key}>
            <div class="health-card-head">
              <span class="health-dot" data-status={p.status}></span>
              <strong>{p.label}</strong>
              <span class="health-badge" data-status={p.status}>{STATUS_LABEL[p.status]}</span>
            </div>
            <dl>
              <dt>Last worked</dt>
              <dd title={p.last_ok_at ? full.format(new Date(p.last_ok_at)) : ''}>{when(p.last_ok_at)}</dd>
              <dt>Last run</dt>
              <dd>{when(p.last_run_at)}{#if p.last_ms !== null} · {ms(p.last_ms)}{/if}</dd>
              <dt>Failures</dt>
              <dd>{p.consecutive_failures ? `${p.consecutive_failures} in a row` : 'none in a row'}{#if p.failures} · {p.failures} since restart{/if}</dd>
            </dl>
            {#if p.status === 'off'}<p class="muted small">{p.detail}</p>
            {:else if p.status !== 'ok' && p.detail}<p class="health-error">{p.detail}</p>{/if}
          </div>
        {/each}
      </div>
      <ul class="health-checks compact">
        {#each report.engines as e (e.key)}
          <li data-status={e.status}>
            <span class="health-dot" data-status={e.status}></span>
            <span class="health-label">{e.label}</span>
            <span class="health-detail muted">{e.detail}</span>
          </li>
        {/each}
      </ul>
    </div>

    <div class="health-block">
      <h2>Agents and tokens <span class="muted small">last {report.window_minutes} min</span></h2>
      {#if !report.agents.tokens.length}
        <p class="muted">No calls in the last hour.</p>
      {:else}
        <div class="health-table" role="table" aria-label="Calls per token">
          <div class="health-tr head" role="row">
            <span role="columnheader">Who</span>
            <span role="columnheader">Calls</span>
            <span role="columnheader">Errors</span>
            <span role="columnheader">Slow</span>
            <span role="columnheader">Avg / worst</span>
          </div>
          {#each report.agents.tokens as t (t.token_id)}
            <div class="health-tr" role="row" class:bad={t.server_errors > 0 || t.error_rate >= 0.2} data-token={t.token_id}>
              <span role="cell" class="who">
                {#if t.kind === 'agent'}<Icon name="robot" size={13} />{:else}<Icon name="user" size={13} />{/if}
                <span>{caller(t)}</span>
                <span class="muted small">{ago(t.last_at, board.now)}</span>
              </span>
              <span role="cell"><span class="health-k">Calls</span>{t.calls}</span>
              <span role="cell">
                <span class="health-k">Errors</span>
                {percent(t.error_rate)}
                {#if t.client_errors || t.server_errors}<span class="muted small">({t.client_errors} 4xx{#if t.server_errors}, {t.server_errors} 5xx{/if})</span>{/if}
              </span>
              <span role="cell"><span class="health-k">Slow</span>{t.slow}</span>
              <span role="cell"><span class="health-k">Avg / worst</span>{ms(t.avg_ms)} / {ms(t.max_ms)}</span>
            </div>
          {/each}
        </div>
      {/if}
      <div class="health-two">
        <div>
          <h3>Recent errors</h3>
          {#if !report.agents.recent_errors.length}
            <p class="muted small">None in the last hour.</p>
          {:else}
            <ul class="health-calls">
              {#each report.agents.recent_errors.slice(0, 10) as c, i (i)}
                <li>
                  <span class="health-code" class:server={c.status >= 500}>{c.status}</span>
                  <span class="mono">{c.route}</span>
                  <span class="muted small">{caller(c)} · {c.door === 'mcp' ? 'MCP' : 'API'} · {ago(c.at, board.now)}</span>
                  {#if c.error}<span class="health-error small">{c.error}</span>{/if}
                </li>
              {/each}
            </ul>
          {/if}
        </div>
        <div>
          <h3>Slow calls <span class="muted small">{ms(report.slow_ms)} or more</span></h3>
          {#if !report.agents.slow_calls.length}
            <p class="muted small">None in the last hour.</p>
          {:else}
            <ul class="health-calls">
              {#each report.agents.slow_calls.slice(0, 10) as c, i (i)}
                <li>
                  <span class="health-code slow">{ms(c.ms)}</span>
                  <span class="mono">{c.route}</span>
                  <span class="muted small">{caller(c)} · {ago(c.at, board.now)}</span>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      </div>
    </div>

    <div class="health-block">
      <h2>Response time <span class="muted small">last {report.latency.samples} requests</span></h2>
      <div class="health-tiles">
        <div class="health-tile"><span class="muted small">Median</span><strong>{ms(report.latency.p50_ms)}</strong></div>
        <div class="health-tile"><span class="muted small">p95</span><strong>{ms(report.latency.p95_ms)}</strong></div>
        <div class="health-tile"><span class="muted small">p99</span><strong>{ms(report.latency.p99_ms)}</strong></div>
        <div class="health-tile"><span class="muted small">Worst</span><strong>{ms(report.latency.max_ms)}</strong></div>
      </div>
      {#if report.latency.slowest.length}
        <h3>Slowest routes</h3>
        <ul class="health-calls">
          {#each report.latency.slowest as r (r.method + r.route)}
            <li>
              <span class="health-code slow">{ms(r.p95_ms)}</span>
              <span class="mono">{r.method} {r.route}</span>
              <span class="muted small">p95 · median {ms(r.p50_ms)} · {r.count} calls</span>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="health-block">
      <h2>Automations <span class="muted small">last 24 h</span></h2>
      {#if !report.automations.recent.length}
        <p class="muted">No failed runs.</p>
      {:else}
        <p class="muted small">{report.automations.failed_24h} failed, {report.automations.partial_24h} partly failed.</p>
        <ul class="health-calls">
          {#each report.automations.recent as run (run.id)}
            <li>
              <span class="health-code" class:server={run.status === 'failed'}>{run.status === 'failed' ? 'Failed' : 'Partly'}</span>
              <span>{run.name}</span>
              {#if run.item_id}
                <button class="link small" onclick={() => run.item_id && void board.open(run.item_id)}>{run.item_title ?? 'a task'}</button>
              {/if}
              <span class="muted small">{ago(run.at, board.now)}</span>
              {#if run.detail}<span class="health-error small">{run.detail}</span>{/if}
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <div class="health-block">
      <h2>Queues and storage</h2>
      <div class="health-tiles">
        <div class="health-tile"><span class="muted small">Webhooks waiting</span><strong>{report.queues.webhooks_pending}</strong></div>
        <div class="health-tile"><span class="muted small">Webhooks failed today</span><strong>{report.queues.webhooks_failed_24h}</strong></div>
        {#each Object.entries(report.queues.engine_lag) as [k, v] (k)}
          <div class="health-tile"><span class="muted small">{LAG_LABEL[k] ?? k} behind</span><strong>{v === null ? '—' : `${v} events`}</strong></div>
        {/each}
        <div class="health-tile"><span class="muted small">Database</span><strong>{bytes(report.db.bytes)}</strong><span class="muted small">+ {bytes(report.db.wal_bytes)} WAL</span></div>
        <div class="health-tile"><span class="muted small">Free disk</span><strong>{bytes(report.db.disk_free_bytes)}</strong></div>
        <div class="health-tile"><span class="muted small">Files stored</span><strong>{bytes(report.db.blob_bytes)}</strong><span class="muted small">attachments, docs, revisions</span></div>
        <div class="health-tile"><span class="muted small">Tasks · events</span><strong>{report.db.items.toLocaleString()} · {report.db.events.toLocaleString()}</strong></div>
      </div>
      <p class="muted small">Counts start over when Slate restarts. Agents can read the same report at <span class="mono">GET /api/v1/health</span> (MCP <span class="mono">slate_health</span>), and the short verdict at <span class="mono">/health</span>.</p>
    </div>
  {/if}
</section>
