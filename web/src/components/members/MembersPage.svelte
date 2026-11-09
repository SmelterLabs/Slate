<script lang="ts">
  // The Members page (`#/members`, owners and admins): every person and agent with their role,
  // reach and last activity. Change a role, open an account's access, disable or enable it,
  // and invite a person or guest (the sign-in token is shown once). Agents are minted on the
  // Agents page (Add my agent); they appear here too, with the same roles. "Agents may
  // not…" (AgentRules) is the workspace's agent deny-list.
  import { onDestroy } from 'svelte';
  import { get, patch, post, put } from '../../lib/api';
  import { ago } from '../../lib/format';
  import { LEVEL_LABEL, LEVELS, ROLE_HINT, ROLE_LABEL, ROLES } from '../../lib/permissions';
  import { board } from '../../lib/store.svelte';
  import type { Account, Level, Member, Role } from '../../lib/types';
  import EmptyState from '../EmptyState.svelte';
  import Icon from '../Icon.svelte';
  import AgentRules from './AgentRules.svelte';
  import MemberAccess from './MemberAccess.svelte';

  const HANDLE_PATTERN = '[a-z][a-z0-9_\\-]{1,31}';
  type Tab = 'all' | 'person' | 'agent' | 'guest';
  const TABS: { key: Tab; label: string }[] = [
    { key: 'all', label: 'Everyone' },
    { key: 'person', label: 'People' },
    { key: 'agent', label: 'Agents' },
    { key: 'guest', label: 'Guests' },
  ];

  let members = $state<Member[] | null>(null);
  let tab = $state<Tab>('all');
  let q = $state('');
  let accessFor = $state<string | null>(null);
  let inviting = $state(false);
  let busy = $state(false);
  // Invite form
  let name = $state('');
  let handle = $state('');
  let handleTouched = $state(false);
  let role = $state<Role>('member');
  let shareList = $state('');
  let shareLevel = $state<Level>('edit');
  let minted = $state<{ name: string; token: string } | null>(null);
  let copied = $state(false);

  const meId = $derived(board.me?.account.id);
  const myRole = $derived<Role>(members?.find((m) => m.account.id === meId)?.role ?? board.me?.account.role ?? 'member');
  const counts = $derived({
    all: members?.length ?? 0,
    person: members?.filter((m) => m.account.kind === 'person').length ?? 0,
    agent: members?.filter((m) => m.account.kind === 'agent').length ?? 0,
    guest: members?.filter((m) => m.role === 'guest').length ?? 0,
  });
  const shown = $derived.by(() => {
    const needle = q.trim().toLowerCase();
    return (members ?? []).filter((m) => {
      if (tab === 'guest' ? m.role !== 'guest' : tab !== 'all' && m.account.kind !== tab) return false;
      if (!needle) return true;
      return `${m.account.display_name} ${m.account.handle}`.toLowerCase().includes(needle);
    });
  });
  const selected = $derived(members?.find((m) => m.account.id === accessFor) ?? null);
  const allLists = $derived(board.sections.flatMap((s) => board.listsOf(s.id).map((l) => ({ ...l, section: s.name }))));

  async function load() {
    try {
      members = await get<Member[]>('/members');
    } catch (e) {
      members = [];
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }
  $effect(() => {
    void load();
  });
  let timer: ReturnType<typeof setTimeout> | undefined;
  const off = board.onEvent((ev) => {
    if (ev.type.startsWith('account.') || ev.type.startsWith('share.')) {
      clearTimeout(timer);
      timer = setTimeout(() => void load(), 200);
    }
  });
  onDestroy(() => {
    off();
    clearTimeout(timer);
  });

  async function run(fn: () => Promise<unknown>, done?: string) {
    busy = true;
    try {
      await fn();
      if (done) board.flash(done);
      await load();
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
      await load();
    } finally {
      busy = false;
    }
  }

  /** Which roles this caller may give this account (the server checks again). */
  function roleChoices(m: Member): Role[] {
    if (myRole === 'owner') return ROLES;
    if (m.role === 'owner' || m.role === 'admin') return [m.role];
    return ['member', 'guest'];
  }

  function changeRole(m: Member, next: Role) {
    if (next === m.role) return;
    if (next === 'owner' && !confirm(`Hand ownership to ${m.account.display_name}? You become an admin.`)) {
      void load();
      return;
    }
    void run(() => put(`/accounts/${m.account.id}/role`, { role: next }), `${m.account.display_name} is now ${ROLE_LABEL[next].toLowerCase()}`);
  }

  const setDisabled = (a: Account, disabled: boolean) =>
    run(() => patch(`/accounts/${a.id}`, { disabled }), `${a.display_name} ${disabled ? 'disabled' : 'enabled'}`);

  function slug(v: string): string {
    const s = v
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 32);
    return /^[a-z]/.test(s) ? s : s ? `u-${s}`.slice(0, 32) : '';
  }
  $effect(() => {
    if (!handleTouched) handle = slug(name);
  });

  function resetInvite() {
    name = '';
    handle = '';
    handleTouched = false;
    role = 'member';
    shareList = '';
    shareLevel = 'edit';
  }

  async function invite(e: Event) {
    e.preventDefault();
    busy = true;
    try {
      const a = await post<Account>('/accounts', { handle, display_name: name.trim(), kind: 'person', role });
      const tok = await post<{ token: string }>(`/accounts/${a.id}/tokens`, { scopes: ['write'], label: 'sign-in' });
      if (shareList) await put('/shares', { account_id: a.id, scope_kind: 'list', scope_id: shareList, level: shareLevel });
      minted = { name: a.display_name, token: tok.token };
      copied = false;
      resetInvite();
      inviting = false;
      await load();
    } catch (err) {
      board.flash(err instanceof Error ? err.message : String(err));
    } finally {
      busy = false;
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch {
      board.flash('Copy failed: select the token and copy it by hand');
    }
  }

  function initials(n: string): string {
    return n
      .split(/\s+/)
      .map((w) => w[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }

  function reach(m: Member): string {
    if (m.role === 'owner' || m.role === 'admin') return 'Everything';
    if (m.role === 'member') return m.shares ? `Everything not private, plus ${m.shares} shared` : 'Everything not private';
    return m.shares ? `${m.shares} shared` : 'Nothing shared yet';
  }
</script>

<section class="work-page members-page" aria-label="Members">
  <header class="work-head">
    <div>
      <h1>Members</h1>
      <p class="muted">People and agents, what role they hold, and what they can reach.</p>
    </div>
    <div class="inline">
      <a class="secondary btn-like" href="#/audit"><Icon name="history" size={14} /> Audit log</a>
      <a class="secondary btn-like" href="#/agents"><Icon name="robot" size={14} /> Add my agent</a>
      <button class="primary" onclick={() => (inviting = !inviting)} aria-expanded={inviting}
        ><Icon name="plus" size={14} /> Invite</button
      >
    </div>
  </header>

  {#if minted}
    <div class="minted" role="status">
      <div>
        <strong>{minted.name} can sign in with this token.</strong>
        <span class="muted small">It is shown once. Send it privately; they paste it on Slate's sign-in screen.</span>
      </div>
      <code class="token-box">{minted.token}</code>
      <span class="inline">
        <button class="secondary" onclick={() => copy(minted!.token)}>{copied ? 'Copied' : 'Copy token'}</button>
        <button class="link" onclick={() => (minted = null)}>Done</button>
      </span>
    </div>
  {/if}

  {#if inviting}
    <form class="invite" onsubmit={invite} aria-label="Invite a person">
      <label class="field">
        <span class="muted small">Name</span>
        <input aria-label="Name" bind:value={name} placeholder="Alex" required maxlength="100" />
      </label>
      <label class="field">
        <span class="muted small">Handle</span>
        <input aria-label="Handle" bind:value={handle} oninput={() => (handleTouched = true)} pattern={HANDLE_PATTERN} required />
      </label>
      <label class="field">
        <span class="muted small">Role</span>
        <select aria-label="Role" bind:value={role}>
          {#if myRole === 'owner'}<option value="admin">Admin</option>{/if}
          <option value="member">Member</option>
          <option value="guest">Guest</option>
        </select>
      </label>
      <label class="field">
        <span class="muted small">Share a list now (optional)</span>
        <span class="inline">
          <select aria-label="List to share" bind:value={shareList}>
            <option value="">None</option>
            {#each allLists as l (l.id)}<option value={l.id}>{l.section} › {l.name}</option>{/each}
          </select>
          <select aria-label="Level" bind:value={shareLevel} disabled={!shareList}>
            {#each LEVELS as l (l)}<option value={l}>{LEVEL_LABEL[l]}</option>{/each}
          </select>
        </span>
      </label>
      <p class="muted small invite-hint">{ROLE_HINT[role]}</p>
      <span class="inline invite-actions">
        <button class="primary" type="submit" disabled={busy || !name.trim() || !handle}>Create and get token</button>
        <button class="link" type="button" onclick={() => (inviting = false)}>Cancel</button>
      </span>
    </form>
  {/if}

  <AgentRules />

  <div class="inbox-tabs" role="tablist">
    {#each TABS as t (t.key)}
      <button role="tab" aria-selected={tab === t.key} class:active={tab === t.key} onclick={() => (tab = t.key)}>
        <Icon name={t.key === 'agent' ? 'robot' : t.key === 'guest' ? 'eye' : t.key === 'person' ? 'user' : 'users'} size={14} />
        {t.label}<span class="tab-count">{counts[t.key]}</span>
      </button>
    {/each}
    <input class="members-search" type="search" bind:value={q} placeholder="Find someone" aria-label="Find someone" />
  </div>

  {#if members === null}
    <p class="muted pad-row">Loading…</p>
  {:else if !shown.length}
    <EmptyState icon="users" title={q ? 'Nobody matches' : 'Nobody here yet'} hint={q ? 'Try another name.' : 'Invite a person, or connect an agent from the Agents page.'} />
  {:else}
    <div class="inbox-list member-list" role="list" aria-label="Members">
      {#each shown as m (m.account.id)}
        {@const a = m.account}
        {@const me = a.id === meId}
        <div class="member-row" role="listitem" class:off={!!a.disabled_at} data-member={a.handle}>
          <span class="avatar big" class:agent={a.kind === 'agent'} aria-hidden="true"
            >{#if a.kind === 'agent'}<Icon name="robot" size={15} />{:else}{initials(a.display_name)}{/if}</span
          >
          <span class="member-name">
            <span class="t">{a.display_name}{#if me}<span class="muted">&nbsp;(you)</span>{/if}</span>
            <span class="muted small">@{a.handle} · {a.kind === 'agent' ? 'Agent' : 'Person'}{#if a.disabled_at} · Disabled{/if}</span>
          </span>
          <span class="member-role">
            <select
              aria-label="Role for {a.display_name}"
              value={m.role}
              disabled={busy || me || roleChoices(m).length < 2}
              title={ROLE_HINT[m.role]}
              onchange={(e) => changeRole(m, e.currentTarget.value as Role)}
            >
              {#each roleChoices(m) as r (r)}<option value={r}>{ROLE_LABEL[r]}</option>{/each}
            </select>
          </span>
          <button class="member-reach" onclick={() => (accessFor = a.id)} title="See and change what {a.display_name} can reach">
            <Icon name={m.role === 'guest' ? 'eye' : m.role === 'member' ? 'users' : 'shield'} size={13} />{reach(m)}
          </button>
          <span class="member-seen muted small">{m.last_active_at ? `Active ${ago(m.last_active_at, board.now)}` : 'Never active'}</span>
          <span class="member-actions">
            <button class="secondary small" onclick={() => (accessFor = a.id)}>Access</button>
            {#if !me && m.role !== 'owner'}
              {#if a.disabled_at}
                <button class="link small" disabled={busy} onclick={() => setDisabled(a, false)}>Enable</button>
              {:else}
                <button class="link small danger" disabled={busy} onclick={() => setDisabled(a, true)}>Disable</button>
              {/if}
            {/if}
          </span>
        </div>
      {/each}
    </div>
  {/if}
</section>

{#if selected}
  <MemberAccess member={selected} onclose={() => (accessFor = null)} />
{/if}
