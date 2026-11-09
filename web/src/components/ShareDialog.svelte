<script lang="ts">
  // Share a section or list (owners and admins): who reaches it and why, a level per direct
  // share, add someone, and the Private switch. Opened by `board.openShare(kind, id)` from the
  // sidebar ⋯ menu or the Share button on a list or section page. Every change is one call
  // (PUT /shares, PATCH private) and lands live for everyone it concerns.
  import { onDestroy } from 'svelte';
  import { get, patch, put } from '../lib/api';
  import { LEVEL_LABEL, LEVELS, ROLE_LABEL } from '../lib/permissions';
  import { board } from '../lib/store.svelte';
  import type { AccessRow, Account, Level, Member } from '../lib/types';
  import Icon from './Icon.svelte';
  import InvitationPanel from './InvitationPanel.svelte';

  let { kind, id }: { kind: 'section' | 'list'; id: string } = $props();

  let rows = $state<AccessRow[] | null>(null);
  let members = $state<Member[]>([]);
  let adding = $state('');
  let addLevel = $state<Level>('edit');
  let busy = $state(false);

  const target = $derived(
    kind === 'list' ? board.lists[id] : board.sections.find((s) => s.id === id),
  );
  const isPrivate = $derived(!!target?.private);
  const byId = $derived(Object.fromEntries(members.map((m) => [m.account.id, m])));
  const candidates = $derived(
    members.filter(
      (m) => !m.account.disabled_at && m.role !== 'owner' && m.role !== 'admin' && !rows?.some((r) => r.account_id === m.account.id && r.via === 'share'),
    ),
  );

  async function load() {
    try {
      const [r, m] = await Promise.all([
        get<AccessRow[]>(`/${kind}s/${id}/access`),
        get<Member[]>('/members'),
      ]);
      rows = r;
      members = m;
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }
  $effect(() => {
    void load();
  });

  let timer: ReturnType<typeof setTimeout> | undefined;
  const off = board.onEvent((ev) => {
    if (ev.type.startsWith('share.') || ev.type === 'account.role_changed' || ev.field === 'private') {
      clearTimeout(timer);
      timer = setTimeout(() => void load(), 150);
    }
  });
  onDestroy(() => {
    off();
    clearTimeout(timer);
  });

  async function run(fn: () => Promise<unknown>) {
    busy = true;
    try {
      await fn();
      await load();
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }

  const setLevel = (accountId: string, level: Level | null) =>
    run(() => put('/shares', { account_id: accountId, scope_kind: kind, scope_id: id, level }));

  async function add() {
    if (!adding) return;
    const who = adding;
    adding = '';
    await setLevel(who, addLevel);
  }

  const setPrivate = (value: boolean) =>
    run(async () => {
      await patch(`/${kind}s/${id}`, { private: value });
      if (kind === 'list' && board.lists[id]) board.lists[id].private = value;
      const sec = board.sections.find((s) => s.id === id);
      if (kind === 'section' && sec) sec.private = value;
    });

  function why(r: AccessRow, a: Account | undefined): string {
    const role = byId[r.account_id]?.role;
    if (r.via === 'role') return role === 'owner' || role === 'admin' ? `${ROLE_LABEL[role]}: sees everything` : 'Member: sees what is not private';
    if (r.via === 'section') return `From the section`;
    return a?.kind === 'agent' ? 'Shared with this agent' : 'Shared directly';
  }

  function initials(name: string): string {
    return name
      .split(/\s+/)
      .map((w) => w[0])
      .join('')
      .slice(0, 2)
      .toUpperCase();
  }

  const close = () => board.closeShare();
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && close()} />

<button class="drawer-scrim" aria-label="Close" tabindex="-1" onclick={close}></button>
<div class="settings share-dialog" role="dialog" aria-label="Share {target?.name ?? kind}" aria-modal="true">
  <div class="drawer-top">
    <h2><Icon name="share" size={16} /> Share {target?.name ?? ''}</h2>
    <button class="icon" aria-label="Close" onclick={close}>✕</button>
  </div>

  <label class="share-private" class:on={isPrivate}>
    <span class="sp-icon"><Icon name={isPrivate ? 'lock' : 'users'} size={16} /></span>
    <span class="sp-text">
      <strong>{isPrivate ? 'Private' : 'Everyone in the workspace'}</strong>
      <span class="muted small"
        >{isPrivate
          ? `Only owners, admins and the people below see this ${kind}.`
          : `Every member sees this ${kind}. Guests only see what is shared with them.`}</span
      >
    </span>
    <input type="checkbox" class="switch" role="switch" aria-label="Private" checked={isPrivate} disabled={busy} onchange={(e) => setPrivate(e.currentTarget.checked)} />
  </label>

  <form
    class="share-add"
    onsubmit={(e) => {
      e.preventDefault();
      void add();
    }}
  >
    <select aria-label="Person or agent to share with" bind:value={adding}>
      <option value="">Add a person or agent…</option>
      {#each candidates as m (m.account.id)}
        <option value={m.account.id}>{m.account.display_name} (@{m.account.handle}) · {ROLE_LABEL[m.role]}</option>
      {/each}
    </select>
    <select aria-label="Level" bind:value={addLevel}>
      {#each LEVELS as l (l)}<option value={l}>{LEVEL_LABEL[l]}</option>{/each}
    </select>
    <button class="primary" type="submit" disabled={!adding || busy}>Share</button>
  </form>

  {#if kind === 'section'}<InvitationPanel sectionId={id} sectionName={target?.name ?? id} {isPrivate} />{/if}

  <div class="share-who" role="list" aria-label="Who has access">
    {#if rows === null}
      <p class="muted small">Loading…</p>
    {:else}
      {#each rows as r (r.account_id)}
        {@const m = byId[r.account_id]}
        {@const a = m?.account ?? board.accounts[r.account_id]}
        <div class="share-row" role="listitem" data-account={a?.handle}>
          <span class="avatar" class:agent={a?.kind === 'agent'} aria-hidden="true"
            >{#if a?.kind === 'agent'}<Icon name="robot" size={13} />{:else}{initials(a?.display_name ?? '?')}{/if}</span
          >
          <span class="share-name">
            <span class="t">{a?.display_name ?? r.account_id}{#if a?.id === board.me?.account.id}<span class="muted">&nbsp;(you)</span>{/if}</span>
            <span class="muted small">{why(r, a)}</span>
          </span>
          {#if r.via === 'share'}
            <select aria-label="Level for {a?.display_name}" value={r.level} disabled={busy} onchange={(e) => setLevel(r.account_id, e.currentTarget.value as Level)}>
              {#each LEVELS as l (l)}<option value={l}>{LEVEL_LABEL[l]}</option>{/each}
            </select>
            <button class="icon-x" aria-label="Stop sharing with {a?.display_name}" title="Remove" disabled={busy} onclick={() => setLevel(r.account_id, null)}>✕</button>
          {:else}
            <span class="level-chip">{LEVEL_LABEL[r.level]}</span>
            <span class="icon-x-spacer"></span>
          {/if}
        </div>
      {/each}
    {/if}
  </div>
  <p class="muted small">
    Levels: view reads it; comment also comments and chats; edit changes tasks. Agents follow the same rules, and a
    token can be narrowed further from the Agents page.
  </p>
</div>
