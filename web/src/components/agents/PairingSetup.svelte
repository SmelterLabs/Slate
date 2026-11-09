<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { connections } from '../../lib/connection-data.svelte';
  import { connectionCommand, connectionStatus, pairingExpired, type Pairing } from '../../lib/connections';
  import { board } from '../../lib/store.svelte';

  let { initial, onnew }: { initial: Pairing; onnew: () => Promise<Pairing> } = $props();
  let now = $state(new Date());
  let show = $state(false);
  let copied = $state(false);
  let busy = $state(false);
  let error = $state<string | null>(null);
  const data = $derived(connections.byAccount[initial.connection.account_id]);
  const row = $derived(data?.rows.find((c) => c.id === initial.connection.id) ?? initial.connection);
  const status = $derived(connectionStatus({ ...row, expires_at: initial.expires_at }, now));
  const expired = $derived(pairingExpired(initial.expires_at, now));
  const waiting = $derived(row.state === 'waiting' && !expired);
  const command = connectionCommand(location.origin);

  $effect(() => {
    const id = initial.connection.account_id;
    return untrack(() => connections.watch(id));
  });
  onMount(() => {
    // Display-only clock: never performs a request.
    const clock = setInterval(() => (now = new Date()), 1000);
    return () => clearInterval(clock);
  });
  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); copied = true; }
    catch { board.flash('Copy failed. Select the text and copy it by hand.'); }
  }
  async function renew() {
    busy = true; error = null;
    try { await onnew(); show = false; copied = false; }
    catch (e) { error = e instanceof Error ? e.message : 'Could not create a code. Try again.'; }
    finally { busy = false; }
  }
</script>

<section class="pairing" aria-label="Connect your computer">
  <div role="status" aria-live="polite">
    <h3>{data?.error ? 'Status unknown' : status.label}</h3>
    <p>{data?.error ?? status.detail}</p>
  </div>
  {#if waiting}
    <ol>
      <li>
        <b>On the computer where your agent will run</b>
        <p>Install the connection client by following <span class="mono">clients/python/README.md</span> in the Slate repository, using the release that matches your server.</p>
        <p>This connects an existing agent. It does not install a model, and provider keys stay on your computer—not in Slate.</p>
      </li>
      <li>
        <b>Open a terminal on that computer and run</b>
        <pre><code>{command}</code></pre>
        <button class="secondary" onclick={() => void copy(command)}>Copy command</button>
      </li>
      <li>
        <b>Enter this code when the terminal asks</b>
        <p>Keep it private. It can be used once, and only for the lists you selected.</p>
        <label class="field"><span>Connection code</span><input aria-label="Connection code" type={show ? 'text' : 'password'} value={initial.code} readonly autocomplete="off" spellcheck="false" /></label>
        <div class="inline">
          <button class="secondary" aria-pressed={show} onclick={() => (show = !show)}>{show ? 'Hide code' : 'Show code'}</button>
          <button class="secondary" onclick={() => void copy(initial.code)}>Copy code</button>
        </div>
        <p class="muted">Expires at {new Date(initial.expires_at).toLocaleTimeString()} ({Math.max(0, Math.ceil((Date.parse(initial.expires_at) - now.getTime()) / 1000))} seconds left).</p>
        <p class="muted">Code refused or already used? Check that you copied the whole code and used this Slate address. If it still fails, create a new code below. Never paste provider keys here.</p>
      </li>
      <li><b>Finish the terminal setup and run its agent test</b><p>Choose your runtime, profile and project folder there. A token alone is not a successful agent test. Keep the connection running to receive tasks.</p></li>
    </ol>
  {:else if row.state !== 'waiting' && row.state !== 'revoked'}
    <p>Continue on the agent’s computer. Run its connection test and keep the connection running. After a successful test, choose “A local connection” under “How tasks reach it” on the agent card to allow Slate to send tasks.</p>
  {/if}
  {#if copied}<p role="status">Copied</p>{/if}
  {#if error}<p class="bad" role="alert">{error}</p>{/if}
  {#if waiting || expired || row.state === 'revoked'}
    <button class="secondary" disabled={busy} onclick={() => void renew()}>{busy ? 'Creating…' : 'Create a new code'}</button>
    <p class="muted">A new code starts a separate connection. Disconnect any older connection you no longer need from the agent card.</p>
  {/if}
  <p class="muted">You can close this window. Connection status stays on the agent card; the code will not be shown again.</p>
</section>

<style>
  .pairing { display: grid; gap: 10px; min-width: 0; }
  h3, p { margin: 0 0 8px; }
  ol { margin: 0; padding-left: 22px; }
  li { margin-bottom: 16px; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--chip); padding: 10px; border-radius: var(--radius); }
  input { width: 100%; min-width: 0; }
  .bad { color: var(--danger); }
  .muted { font-size: var(--fs-sm); }
  .secondary { font: inherit; color: var(--text); background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; cursor: pointer; }
</style>
