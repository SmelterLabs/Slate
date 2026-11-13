<script lang="ts">
  import { fullStamp } from '../../lib/format';
  import { untrack } from 'svelte';
  import { connections } from '../../lib/connection-data.svelte';
  import { connectionStatus } from '../../lib/connections';
  import { board } from '../../lib/store.svelte';
  import type { AgentEntry } from '../../lib/types';

  let { entry, admin, onconnect }: { entry: AgentEntry; admin: boolean; onconnect: () => void } = $props();
  let open = $state(false);
  let confirm = $state<string | null>(null);
  let busy = $state(false);
  let error = $state<string | null>(null);
  const allowed = $derived(admin || board.me?.account.id === entry.account.id);
  const data = $derived(connections.byAccount[entry.account.id]);
  const rows = $derived(data?.rows ?? []);
  const latest = $derived(rows.filter((c) => c.state !== 'revoked').at(-1) ?? rows.at(-1));
  const summary = $derived(latest ? connectionStatus(latest, board.now).label :
    entry.last_seen_at || entry.last_event_at || entry.live_streams ? 'Activity seen; setup not verified' :
    entry.wake === 'manual' ? 'Manual or app access; no local connection' : 'No local connection configured');

  $effect(() => {
    const id = entry.account.id;
    if (allowed) return untrack(() => connections.watch(id));
  });
  async function revoke(id: string) {
    if (!admin) return;
    busy = true; error = null;
    try { await connections.revoke(entry.account.id, id); confirm = null; }
    catch (e) { error = e instanceof Error ? e.message : 'Could not disconnect. Try again.'; }
    finally { busy = false; }
  }
</script>

<section class="connections" aria-label="Connections for {entry.account.display_name}">
  {#if !allowed}
    <p class="muted">Connection status is not shared with your access. It is unknown, not offline.</p>
  {:else}
    <p role="status" aria-live="polite">{!data?.loaded ? 'Checking connection…' : data.error ? 'Status unknown' : summary}</p>
    <button class="link small" aria-expanded={open} onclick={() => (open = !open)}>Connections</button>
    {#if open}
      {#if data?.error}
        <p role="alert">{data.error}</p>
        <button class="secondary" onclick={() => { error = null; confirm = null; void connections.load(entry.account.id); }}>Check again</button>
      {:else if data?.loaded && !rows.length}
        <p class="muted">An account, token or recent activity does not prove that an agent can run tasks. App/MCP access and scheduled checks do not need a local connection.</p>
      {/if}
      <ul>
        {#each rows as row (row.id)}
          {@const status = connectionStatus(row, board.now)}
          <li>
            <b>{row.label}</b> · <span>{status.label}</span>
            <p class="muted">{status.detail}</p>
            {#if row.last_seen_at}<p class="muted">Last check: {fullStamp(row.last_seen_at)}</p>{/if}
            {#if row.verified_at}<p class="muted">Last successful agent test: {fullStamp(row.verified_at)}</p>{/if}
            {#if admin && row.state !== 'revoked'}
              {#if confirm === row.id}
                <p>Revoke this connection’s Slate access? This does not stop its process or revoke other connections.</p>
                <button class="secondary" disabled={busy} onclick={() => void revoke(row.id)}>{busy ? 'Disconnecting…' : 'Revoke access'}</button>
                <button class="link" disabled={busy} onclick={() => (confirm = null)}>Cancel</button>
              {:else}
                <button class="secondary" onclick={() => (confirm = row.id)}>Disconnect</button>
              {/if}
            {/if}
          </li>
        {/each}
      </ul>
      {#if error}<p role="alert">{error}</p>{/if}
      {#if admin}<button class="secondary" onclick={onconnect}>Set up a connection</button>{/if}
    {/if}
  {/if}
</section>

<style>
  .connections { border-top: 1px solid var(--line); margin-top: 10px; padding-top: 8px; font-size: var(--fs-sm); overflow-wrap: anywhere; }
  p { margin: 4px 0 8px; }
  ul { list-style: none; margin: 8px 0; padding: 0; }
  li { border-top: 1px solid var(--line); padding: 8px 0; }
  .secondary { font: inherit; color: var(--text); background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; cursor: pointer; }
</style>
