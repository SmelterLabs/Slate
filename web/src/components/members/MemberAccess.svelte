<script lang="ts">
  // One account's reach, section by section and list by list (Members page → Access): the
  // level it has and why, with a picker per row for a direct share. A guest starts with
  // nothing; a member with everything that is not private; owners and admins see everything.
  import { onDestroy } from 'svelte';
  import { get, put } from '../../lib/api';
  import { LEVEL_LABEL, LEVELS, ROLE_HINT, ROLE_LABEL, reachLabel } from '../../lib/permissions';
  import { board } from '../../lib/store.svelte';
  import type { Level, Member, Share } from '../../lib/types';
  import Icon from '../Icon.svelte';
  import { trapFocus } from '../../lib/focustrap';

  let { member, onclose }: { member: Member; onclose: () => void } = $props();

  interface Effective {
    all: boolean;
    sections: Record<string, Level>;
    lists: Record<string, Level>;
  }

  let shares = $state<Share[]>([]);
  let eff = $state<Effective | null>(null);
  let busy = $state(false);
  const full = $derived(member.role === 'owner' || member.role === 'admin');

  async function load() {
    try {
      const [s, e] = await Promise.all([
        get<Share[]>(`/shares?account_id=${member.account.id}`),
        get<Effective>(`/accounts/${member.account.id}/access`),
      ]);
      shares = s;
      eff = e;
    } catch (err) {
      board.flash(err instanceof Error ? err.message : String(err));
    }
  }
  $effect(() => {
    void load();
  });
  const off = board.onEvent((ev) => {
    if (ev.type.startsWith('share.') || ev.field === 'private' || ev.type === 'account.role_changed') void load();
  });
  onDestroy(off);

  const direct = (kind: 'section' | 'list', id: string) =>
    shares.find((s) => s.scope_kind === kind && s.scope_id === id)?.level ?? '';

  function has(kind: 'section' | 'list', id: string): Level | null {
    if (!eff) return null;
    if (eff.all) return 'edit';
    return (kind === 'list' ? eff.lists[id] : eff.sections[id]) ?? null;
  }

  async function set(kind: 'section' | 'list', id: string, level: string) {
    busy = true;
    try {
      await put('/shares', {
        account_id: member.account.id,
        scope_kind: kind,
        scope_id: id,
        level: level || null,
      });
      await load();
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }
</script>

<button class="drawer-scrim" aria-label="Close" tabindex="-1" onclick={onclose}></button>
<div class="settings member-access" role="dialog" aria-label="Access for {member.account.display_name}" aria-modal="true" use:trapFocus={{ onescape: onclose }}>
  <div class="drawer-top">
    <h2>{member.account.display_name}<span class="muted"> · {ROLE_LABEL[member.role]}</span></h2>
    <button class="icon" aria-label="Close" onclick={onclose}>✕</button>
  </div>
  <p class="muted small">
    {ROLE_HINT[member.role]}
    {#if eff}<strong class="reach">Reaches: {reachLabel(eff)}</strong>{/if}
  </p>
  {#if full}
    <p class="ma-full"><Icon name="shield" size={15} /> {ROLE_LABEL[member.role]}s see and change everything; there is nothing to share.</p>
  {:else}
    <div class="ma-tree" role="list" aria-label="Sections and lists">
      {#each board.sections as sec (sec.id)}
        {@const sLevel = has('section', sec.id)}
        <div class="ma-row ma-section" role="listitem" data-scope={sec.name}>
          <span class="ma-name"
            >{#if sec.private}<Icon name="lock" size={12} />{/if}<strong>{sec.name}</strong>
            <span class="ma-has" class:none={!sLevel}>{sLevel ? LEVEL_LABEL[sLevel] : 'No access'}</span></span
          >
          <select aria-label="Share {sec.name}" value={direct('section', sec.id)} disabled={busy} onchange={(e) => set('section', sec.id, e.currentTarget.value)}>
            <option value="">{member.role === 'member' && !sec.private ? 'Default (edit)' : 'Not shared'}</option>
            {#each LEVELS as l (l)}<option value={l}>{LEVEL_LABEL[l]}</option>{/each}
          </select>
        </div>
        {#each board.listsOf(sec.id) as lst (lst.id)}
          {@const lLevel = has('list', lst.id)}
          <div class="ma-row ma-list" role="listitem" data-scope={lst.name}>
            <span class="ma-name"
              >{#if lst.private}<Icon name="lock" size={12} />{:else}<Icon name="list" size={12} />{/if}{lst.name}
              <span class="ma-has" class:none={!lLevel}>{lLevel ? LEVEL_LABEL[lLevel] : 'No access'}</span></span
            >
            <select aria-label="Share {lst.name}" value={direct('list', lst.id)} disabled={busy} onchange={(e) => set('list', lst.id, e.currentTarget.value)}>
              <option value="">{lst.private ? 'Not shared' : 'From the section'}</option>
              {#each LEVELS as l (l)}<option value={l}>{LEVEL_LABEL[l]}</option>{/each}
            </select>
          </div>
        {/each}
      {/each}
    </div>
  {/if}
</div>
