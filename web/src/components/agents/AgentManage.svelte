<script lang="ts">
  // One agent's usage and tokens, inside its Agents-page card: events per day for two weeks
  // (by source), its busiest event types, each live token with last use and request counts,
  // and the admin actions: new token, revoke, disable. Data: GET /accounts/{id}/usage.
  import { onMount } from 'svelte';
  import { api, get, patch } from '../../lib/api';
  import { bars, shortDay, scopeText, limitText, sourceShare, type Usage } from '../../lib/agentkit';
  import { ago } from '../../lib/format';
  import { board } from '../../lib/store.svelte';
  import type { Account } from '../../lib/types';

  let {
    account,
    admin,
    onnewtoken,
    ondisabled,
  }: { account: Account; admin: boolean; onnewtoken: () => void; ondisabled: () => void } = $props();

  let usage = $state<Usage | null>(null);
  let error = $state<string | null>(null);
  let confirm = $state<string | null>(null); // a token id, or 'disable'

  export async function reload() {
    try {
      usage = await get<Usage>(`/accounts/${account.id}/usage?days=14`);
      error = null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  onMount(() => void reload());

  const chart = $derived(usage ? bars(usage.days) : []);
  const share = $derived(usage ? sourceShare(usage.days) : []);

  async function revoke(id: string) {
    confirm = null;
    try {
      await api('DELETE', `/tokens/${id}`);
      board.flash('Token revoked');
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
    await reload();
  }

  async function disable() {
    confirm = null;
    try {
      await patch(`/accounts/${account.id}`, { disabled: true });
      board.flash(`${account.display_name} is disabled; its tokens stopped working`);
      ondisabled();
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }
</script>

<div class="manage" aria-label="Usage and tokens for {account.display_name}">
  {#if error}
    <p class="muted">Could not load usage: {error}</p>
  {:else if !usage}
    <p class="muted">Loading usage…</p>
  {:else}
    <section class="use">
      <div class="nums">
        <span><b>{usage.events_in_window}</b> change{usage.events_in_window === 1 ? '' : 's'} in 14 days</span>
        <span><b>{usage.events_all_time}</b> all time</span>
        {#each share as [src, part] (src)}
          <span class="chip src">{src.toUpperCase()} {Math.round(part * 100)}%</span>
        {/each}
        {#if usage.throttled}<span class="chip warn">{usage.throttled} throttled</span>{/if}
      </div>
      <div class="chart" role="img" aria-label="Changes per day over the last 14 days">
        {#each chart as b (b.day)}
          <span class="bar" title="{shortDay(b.day)}: {b.events} change{b.events === 1 ? '' : 's'}">
            <span style:height="{Math.max(b.h * 100, b.events ? 6 : 2)}%" class:zero={!b.events}></span>
          </span>
        {/each}
      </div>
      <div class="axis muted"><span>{shortDay(usage.days[0].day)}</span><span>today</span></div>
      {#if usage.top_types.length}
        <p class="types muted">
          Mostly: {#each usage.top_types.slice(0, 4) as t, i (t.type)}{i ? ', ' : ''}<code>{t.type}</code>
            ×{t.count}{/each}
        </p>
      {/if}
    </section>

    <section class="tokens" aria-label="Tokens">
      <h4>Tokens <span class="muted">· limits {usage.rate_limit.reads ? `${usage.rate_limit.reads} reads / ${usage.rate_limit.writes} writes a minute` : 'off'}</span></h4>
      {#if !usage.tokens.length}
        <p class="muted">No live tokens: this agent cannot call Slate.</p>
      {/if}
      <ul>
        {#each usage.tokens as t (t.id)}
          <li class="token">
            <span class="grow">
              <b>{t.label ?? 'Unlabelled'}</b>
              <span class="chip">{scopeText(t.scopes)}</span>
              {#if limitText(t.limit)}<span class="chip" title="This token is narrowed below its account">{limitText(t.limit)}</span>{/if}
              <span class="muted small"
                >{t.last_used_at ? `used ${ago(t.last_used_at, board.now)}` : 'never used'} · made {ago(t.created_at, board.now)}{#if t.requests && t.requests.read + t.requests.write}
                  · {t.requests.read} reads, {t.requests.write} writes since restart{/if}</span
              >
            </span>
            {#if admin || board.me?.account.id === account.id}
              {#if confirm === t.id}
                <button class="danger" onclick={() => void revoke(t.id)}>Revoke now</button>
                <button class="link" onclick={() => (confirm = null)}>Keep</button>
              {:else}
                <button class="link" onclick={() => (confirm = t.id)}>Revoke</button>
              {/if}
            {/if}
          </li>
        {/each}
      </ul>
      {#if admin}
        <div class="inline">
          <button class="secondary" onclick={onnewtoken}>New token</button>
          {#if board.me?.account.id !== account.id}
            {#if confirm === 'disable'}
              <button class="danger" onclick={() => void disable()}>Disable {account.display_name}</button>
              <button class="link" onclick={() => (confirm = null)}>Cancel</button>
            {:else}
              <button class="link danger-link" onclick={() => (confirm = 'disable')}>Disable agent…</button>
            {/if}
          {/if}
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .secondary,
  .danger {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 5px 12px;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--panel);
    color: var(--text);
    font: inherit;
    cursor: pointer;
  }
  .secondary:hover {
    background: var(--card-hover);
  }
  .danger {
    background: var(--danger);
    border-color: var(--danger);
    color: #fff;
  }
  .manage {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 16px;
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--line);
    font-size: 13px;
  }
  .nums {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 12px;
    align-items: center;
  }
  .chip {
    font-size: 11px;
    padding: 1px 7px;
    border-radius: 999px;
    background: var(--chip);
  }
  .chip.warn {
    background: color-mix(in srgb, var(--danger) 18%, transparent);
    color: var(--danger);
  }
  .chart {
    display: grid;
    grid-template-columns: repeat(14, 1fr);
    gap: 3px;
    height: 56px;
    margin-top: 10px;
    align-items: end;
  }
  .bar {
    height: 100%;
    display: flex;
    align-items: flex-end;
  }
  .bar > span {
    width: 100%;
    border-radius: 3px 3px 0 0;
    background: var(--accent);
  }
  .bar > span.zero {
    background: var(--line);
  }
  .axis {
    display: flex;
    justify-content: space-between;
    font-size: 11px;
    margin-top: 2px;
  }
  .types {
    margin: 8px 0 0;
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  h4 {
    margin: 0 0 6px;
    font-size: 13px;
  }
  .tokens ul {
    list-style: none;
    margin: 0 0 8px;
    padding: 0;
    display: grid;
    gap: 6px;
  }
  .token {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 6px 8px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }
  .token .grow {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 6px;
    align-items: center;
    min-width: 0;
    flex: 1;
  }
  .small {
    font-size: 12px;
    width: 100%;
  }
  .danger-link {
    color: var(--danger);
  }
  @media (max-width: 700px) {
    .manage {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
