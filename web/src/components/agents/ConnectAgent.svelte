<script lang="ts">
  // Explicit local, app/API or manual setup. New identities begin guest/manual;
  // only the local executor's successful test can establish readiness.
  import { onMount, tick, untrack } from 'svelte';
  import { get, patch, post, put } from '../../lib/api';
  import { handleFrom, HANDLE_RE, SCOPES, snippets, type CreatedToken, type Scope, type SnippetKey } from '../../lib/agentkit';
  import { board } from '../../lib/store.svelte';
  import type { Account } from '../../lib/types';
  import Icon from '../Icon.svelte';
  import PairingSetup from './PairingSetup.svelte';
  import { canManageConnections, type Pairing } from '../../lib/connections';
  import { listLevel } from '../../lib/permissions';

  let {
    agents,
    preset = null,
    onclose,
    oncreated,
  }: {
    agents: Account[];
    preset?: string | null;
    onclose: () => void;
    oncreated: () => void;
  } = $props();

  // The dialog opens on a preset and then belongs to the person: read the props once.
  const start = untrack(() => ({ preset, first: agents[0]?.id ?? '' }));
  let mode = $state<'new' | 'existing'>(start.preset ? 'existing' : 'new');
  let method = $state<'local' | 'mcp' | 'manual' | null>(null);
  let createdAccount = $state<Account | null>(null);
  let pairing = $state<Pairing | null>(null);
  let manualDone = $state(false);
  let dialog: HTMLDivElement;
  onMount(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.querySelector<HTMLInputElement>('input')?.focus();
    return () => previous?.focus();
  });
  let name = $state('');
  let handle = $state('');
  let handleEdited = $state(false);
  let pick = $state<string>(start.preset ?? start.first);
  let scope = $state<Scope>('write');
  let label = $state('');
  // Reach: the agent's role, and optionally a token narrowed to some lists (service/access.py).
  let agentRole = $state<'member' | 'guest'>('guest');
  let reach = $state<'all' | 'some'>('some');
  let reachLists = $state<string[]>([]);
  let reachLevel = $state<'view' | 'comment' | 'edit'>('edit');
  const allLists = $derived(board.sections.flatMap((sec) => board.listsOf(sec.id).filter((l) => listLevel(board.me?.access, l.id) === 'edit').map((l) => ({ id: l.id, name: `${sec.name} › ${l.name}` }))));
  let busy = $state(false);
  let error = $state<string | null>(null);
  let made = $state<{ token: CreatedToken; handle: string } | null>(null);
  let tab = $state<SnippetKey>('claude');
  let copied = $state<string | null>(null);

  $effect(() => {
    // A submitted form may have scrolled to its footer. Each result starts at its heading.
    if (pairing?.connection.id || made || manualDone) {
      void tick().then(() => {
        dialog.scrollTop = 0;
        dialog.querySelector<HTMLHeadingElement>('h2')?.focus({ preventScroll: true });
      });
    }
  });

  const suggested = $derived(handleEdited ? handle : handleFrom(name));
  const handleOk = $derived(HANDLE_RE.test(suggested));
  const canSubmit = $derived(
    !busy && method !== null && canManageConnections(board.me) &&
      (mode === 'existing' ? !!pick : name.trim() !== '' && handleOk) &&
      ((method === 'mcp' && reach === 'all' && (mode === 'existing' || agentRole === 'member')) || reachLists.length > 0),
  );
  const kit = $derived(made ? snippets(location.origin, made.token.token, made.handle) : []);
  const shown = $derived(kit.find((s) => s.key === tab) ?? kit[0]);

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    busy = true;
    error = null;
    try {
      let account: Account;
      if (createdAccount) {
        account = createdAccount;
      } else if (mode === 'new') {
        account = await post<Account>('/accounts', {
          handle: suggested,
          display_name: name.trim(),
          kind: 'agent',
          role: method === 'mcp' ? agentRole : 'guest',
          wake: 'manual',
        });
        // Keep the account after a later failure: retry must not create a duplicate.
        createdAccount = account;
        board.accounts[account.id] = account;
      } else {
        account = agents.find((a) => a.id === pick)!;
      }
      // Only NEW accounts get shares here. Existing agents must already have access.
      if (createdAccount && (method !== 'mcp' || agentRole === 'guest')) {
        for (const id of reachLists) {
          await put('/shares', { account_id: account.id, scope_kind: 'list', scope_id: id, level: method === 'mcp' ? reachLevel : 'edit' });
        }
      }
      if (method === 'local') {
        pairing = await post<Pairing>('/agent-connections/pairings', {
          account_id: account.id, label: label.trim() || 'My computer', list_ids: reachLists,
        });
        oncreated();
        return;
      }
      if (method === 'manual') {
        if (mode === 'existing') {
          await patch(`/accounts/${account.id}`, { wake: 'manual' });
          const accounts = await get<Account[]>('/accounts');
          if (accounts.find((a) => a.id === account.id)?.wake !== 'manual') {
            throw new Error('The delivery change could not be confirmed. Try again.');
          }
        }
        manualDone = true;
        oncreated();
        return;
      }
      const limited = (reach === 'some' || (mode === 'new' && agentRole === 'guest')) && reachLists.length;
      const token = await post<CreatedToken>(`/accounts/${account.id}/tokens`, {
        scopes: [scope],
        label: label.trim() || null,
        ...(limited ? { limit: { list_ids: reachLists, level: reachLevel } } : {}),
      });
      made = { token, handle: account.handle };
      oncreated();
    } catch (err) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      busy = false;
    }
  }

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
      copied = what;
      setTimeout(() => (copied = copied === what ? null : copied), 1600);
    } catch {
      board.flash('Copy failed: select the text and copy it by hand');
    }
  }
</script>

<svelte:window
  onkeydown={(e) => {
    if (e.key === 'Escape') onclose();
    if (e.key === 'Tab' && dialog) {
      const controls = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href]')).filter((el) => el.getClientRects().length);
      const first = controls[0], last = controls.at(-1);
      if (!controls.includes(document.activeElement as HTMLElement)) { e.preventDefault(); (e.shiftKey ? last : first)?.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }
  }}
/>

<button class="drawer-scrim" aria-label="Close" tabindex="-1" onclick={onclose}></button>
<div bind:this={dialog} class="settings connect-agent" role="dialog" aria-label="Add my agent" aria-modal="true">
  <div class="drawer-top">
    <h2 tabindex="-1">{made ? `Connect @${made.handle}` : 'Add my agent'}</h2>
    <button class="icon" aria-label="Close" onclick={onclose}><Icon name="x" size={16} /></button>
  </div>

  {#if !canManageConnections(board.me)}
    <p role="status">An account manager must connect agents. Your access does not allow this.</p>
  {:else if pairing}
    <PairingSetup initial={pairing} onnew={async () => {
      const next = await post<Pairing>('/agent-connections/pairings', {
        account_id: pairing!.connection.account_id, label: label.trim() || 'My computer', list_ids: reachLists,
      });
      pairing = next;
      return next;
    }} />
    <div class="inline end"><button class="primary" onclick={onclose}>Done</button></div>
  {:else if manualDone}
    <section class="ca-done" aria-label="Manual agent added">
      <p role="status">{mode === 'new' ? 'Agent account added.' : 'Agent set to manual delivery.'} No local executor was connected and no token was created.</p>
      <p>Assign a task, then use Copy brief on its card and pass it to the agent yourself. Creating an account does not install or start an agent.</p>
      <button class="primary" onclick={onclose}>Done</button>
    </section>
  {:else if !made}
    <form class="ca-form" aria-label="Agent setup" onsubmit={submit}>
      <fieldset class="scopes" disabled={!!createdAccount || busy}>
        <legend>How would you like to use your agent?</legend>
        <label class="scope" class:on={method === 'local'}><input type="radio" bind:group={method} value="local" aria-label="Let Slate start my agent" /><span><b>Let Slate start my agent</b><br /><span class="muted">Connect an agent already installed on your computer.</span></span></label>
        <label class="scope" class:on={method === 'mcp'}><input type="radio" bind:group={method} value="mcp" aria-label="Let my agent use Slate" /><span><b>Let my agent use Slate</b><br /><span class="muted">Add Slate to its app with an API token or MCP connector.</span></span></label>
        <label class="scope" class:on={method === 'manual'}><input type="radio" bind:group={method} value="manual" aria-label="I will pass tasks to it myself" /><span><b>I will pass tasks to it myself</b><br /><span class="muted">Copy task briefs by hand. No token or local connection.</span></span></label>
      </fieldset>
      {#if method}
      <fieldset class="scopes" disabled={!!createdAccount || busy}>
      {#if agents.length}
        <div class="ca-seg" role="radiogroup" aria-label="Account">
          <label><input type="radio" bind:group={mode} value="new" /> New agent</label>
          <label><input type="radio" bind:group={mode} value="existing" /> Existing agent</label>
        </div>
      {/if}

      {#if mode === 'new'}
        <label class="field">
          <span>Name</span>
          <!-- svelte-ignore a11y_autofocus -->
          <input bind:value={name} placeholder="Research bot" maxlength="100" autofocus />
        </label>
        <label class="field">
          <span>Handle <small class="muted">used for assigning and @mentions</small></span>
          <input
            value={suggested}
            oninput={(e) => {
              handle = e.currentTarget.value;
              handleEdited = true;
            }}
            placeholder="research-bot"
            maxlength="32"
            aria-invalid={suggested !== '' && !handleOk}
          />
          {#if suggested !== '' && !handleOk}
            <small class="bad">2–32 characters: a lowercase letter, then letters, digits, - or _.</small>
          {/if}
        </label>
      {:else}
        <label class="field">
          <span>Agent</span>
          <select bind:value={pick}>
            {#each agents as a (a.id)}<option value={a.id}>{a.display_name} (@{a.handle})</option>{/each}
          </select>
        </label>
      {/if}

      </fieldset>
      {#if createdAccount}<p class="muted">@{createdAccount.handle} has been added. Retry finishes this setup without creating another account.</p>{/if}
      {#if method === 'mcp'}
      <fieldset class="scopes" disabled={busy}>
        <legend>What it may do</legend>
        {#each SCOPES as s (s.key)}
          <label class="scope" class:on={scope === s.key}>
            <input type="radio" bind:group={scope} value={s.key} />
            <span><b>{s.label}</b> <span class="muted">{s.text}</span></span>
          </label>
        {/each}
      </fieldset>

      {/if}
      <fieldset class="scopes reach" disabled={busy}>
        <legend>What it can reach</legend>
        {#if method === 'mcp' && mode === 'new' && !createdAccount}
          <label class="field">
            <span>Role</span>
            <select aria-label="Agent role" bind:value={agentRole}>
              <option value="member">Member: every list that is not private</option>
              <option value="guest">Guest: only the lists picked below</option>
            </select>
          </label>
        {/if}
        {#if method === 'mcp'}
        <label class="scope" class:on={reach === 'all'}>
          <input type="radio" bind:group={reach} value="all" />
          <span><b>Everything its role allows</b> <span class="muted">the account's own access</span></span>
        </label>
        <label class="scope" class:on={reach === 'some'}>
          <input type="radio" bind:group={reach} value="some" />
          <span><b>Only some lists</b> <span class="muted">this token cannot see or touch anything else</span></span>
        </label>
        {/if}
        {#if method !== 'mcp' || reach === 'some' || (mode === 'new' && agentRole === 'guest')}
          <p class="muted small">Choose only the lists this agent needs. {method !== 'mcp' || agentRole === 'guest' ? 'New guests cannot reach other lists.' : 'The token will be limited, but a member account can access other non-private lists.'} Existing agents must already have access to the selected lists.</p>
          <div class="reach-lists" role="group" aria-label="Selected lists">
            {#each allLists as l (l.id)}
              <label class="reach-list"><input type="checkbox" bind:group={reachLists} value={l.id} /> {l.name}</label>
            {/each}
          </div>
          {#if !allLists.length}<p class="muted">No editable lists available. Create a list or ask for edit access first.</p>{/if}
          {#if method === 'mcp'}
          <label class="field">
            <span>At most</span>
            <select aria-label="Token level" bind:value={reachLevel}>
              <option value="view">View</option>
              <option value="comment">Comment</option>
              <option value="edit">Edit</option>
            </select>
          </label>
          {/if}
        {/if}
      </fieldset>

      {#if method !== 'manual'}<label class="field">
        <span>{method === 'local' ? 'Computer label' : 'Token label'} <small class="muted">which computer or run uses it</small></span>
        <input bind:value={label} placeholder="My laptop" maxlength="80" disabled={busy} />
      </label>{/if}

      {#if error}<p class="bad" role="alert">{error}</p>{/if}
      <div class="inline end">
        <button type="button" class="secondary" onclick={onclose}>Cancel</button>
        <button class="primary" disabled={!canSubmit}>{busy ? 'Creating…' : method === 'local' ? 'Create connection code' : method === 'manual' ? 'Add agent' : 'Create token'}</button>
      </div>
      {/if}
    </form>
  {:else}
    <section class="ca-done" aria-label="Token">
      <p role="status">Token created. This lets the agent use Slate; it does not set up or test a local executor.</p>
      <p class="once"><Icon name="alert" size={14} /> Copy the token now. Slate stores only a hash and will not show it again.</p>
      <div class="secret">
        <code data-testid="new-token">{made.token.token}</code>
        <button class="secondary" onclick={() => void copy(made!.token.token, 'token')}>
          <Icon name={copied === 'token' ? 'check' : 'copy'} size={14} />
          {copied === 'token' ? 'Copied' : 'Copy'}
        </button>
      </div>

      <div class="tabs" role="tablist" aria-label="Setup">
        {#each kit as s (s.key)}
          <button role="tab" aria-selected={tab === s.key} class:on={tab === s.key} onclick={() => (tab = s.key)}
            >{s.label}</button
          >
        {/each}
      </div>
      {#if shown}
        <div class="snippet" role="tabpanel" aria-label={shown.label}>
          <div class="snippet-head">
            <p class="muted">{shown.hint}</p>
            <button class="secondary" onclick={() => void copy(shown.code, shown.key)}>
              <Icon name={copied === shown.key ? 'check' : 'copy'} size={14} />
              {copied === shown.key ? 'Copied' : 'Copy'}
            </button>
          </div>
          <pre><code>{shown.code}</code></pre>
        </div>
      {/if}
      <p class="muted small">
        Next: the agent checks in with <code>GET /api/v1/checkin</code> (MCP <code>slate_checkin</code>), claims an
        item, reports while it works and reports done. The loop is in <code>docs/agents/quickstart.md</code>.
      </p>
      <div class="inline end"><button class="primary" onclick={onclose}>Done</button></div>
    </section>
  {/if}
</div>

<style>
  .reach-lists {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 4px 12px;
    max-height: 160px;
    overflow-y: auto;
    padding: 6px 8px;
    border: 1px solid var(--line);
    border-radius: 8px;
  }
  .reach-list {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: var(--fs-sm);
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .secondary {
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
  .connect-agent {
    width: min(640px, calc(100vw - 16px));
    overscroll-behavior: contain;
  }
  .drawer-top {
    position: sticky;
    top: -14px;
    z-index: 1;
    background: var(--panel);
    padding-block: 6px;
  }
  .ca-form,
  .ca-done {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .ca-seg {
    display: flex;
    gap: 16px;
  }
  .ca-seg label {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .scopes {
    border: 0;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 6px;
  }
  .scopes legend {
    font-size: 13px;
    margin-bottom: 4px;
  }
  .scope {
    display: flex;
    gap: 8px;
    align-items: flex-start;
    padding: 8px 10px;
    border: 1px solid var(--line);
    border-radius: var(--radius);
    cursor: pointer;
  }
  .scope.on {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .scope input {
    margin-top: 3px;
  }
  .bad {
    color: var(--danger);
    margin: 0;
  }
  .end {
    justify-content: flex-end;
  }
  .once {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 0;
    padding: 8px 10px;
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--prio-high) 14%, transparent);
  }
  .secret {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .secret code {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
    padding: 8px 10px;
    border-radius: var(--radius);
    background: var(--chip);
    font-size: 13px;
    user-select: all;
  }
  .tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    border-bottom: 1px solid var(--line);
  }
  .tabs button {
    background: none;
    border: 0;
    border-bottom: 2px solid transparent;
    padding: 6px 10px;
    color: var(--muted);
    cursor: pointer;
    font: inherit;
  }
  .tabs button.on {
    color: var(--text);
    border-bottom-color: var(--accent);
  }
  .snippet-head {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin-bottom: 6px;
  }
  .snippet-head p {
    flex: 1;
    margin: 0;
    font-size: 12px;
  }
  .snippet pre {
    margin: 0;
    padding: 10px 12px;
    border-radius: var(--radius);
    background: var(--chip);
    overflow-x: auto;
    font-size: 12px;
    line-height: 1.5;
  }
  .small {
    font-size: 12px;
    margin: 0;
  }
</style>
