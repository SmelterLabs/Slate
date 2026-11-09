<script lang="ts">
  import { onDestroy } from 'svelte';
  import { api, get, post } from '../lib/api';
  import { shortDate } from '../lib/format';
  import { board } from '../lib/store.svelte';
  let { sectionId, sectionName, isPrivate }: { sectionId: string; sectionName: string; isPrivate: boolean } = $props();
  type Invite = {
    id: string;
    account_id: string;
    expires_at: string;
    accepted_at: string | null;
    revoked_at: string | null;
    /** When the browser session it issued ends (null before acceptance). */
    session_expires_at?: string | null;
    session_revoked_at?: string | null;
  };
  let invitations = $state<Invite[]>([]);
  let handle = $state('');
  let name = $state('');
  let link = $state('');
  /** Who the shown link is for: the new guest, or an existing row's guest (another device). */
  let linkFor = $state('');
  let busy = $state(false);
  let error = $state('');
  let copied = $state(false);
  let generation = 0;
  async function load() {
    const gen = ++generation;
    try { const rows = await get<Invite[]>(`/invitations?section_id=${sectionId}`); if (gen === generation) invitations = rows; }
    catch (e) { if (gen === generation) error = String(e); }
  }
  $effect(() => { void sectionId; link = ''; void load(); });
  const off = board.onEvent(ev => { if (ev.type.startsWith('invitation.') || ev.type === 'session.extended' || ev.type === 'token.revoked') void load(); });
  onDestroy(() => { generation++; off(); });
  function show(secret: string, who: string) {
    link = `${location.origin}${location.pathname}#/invite?secret=${encodeURIComponent(secret)}`;
    linkFor = who;
    copied = false;
  }
  async function issue() {
    if (busy) return;
    busy = true; error = ''; link = '';
    try {
      const result = await post<{ secret: string }>('/invitations', { section_id: sectionId, handle, display_name: name, role: 'guest', level: 'edit' });
      show(result.secret, name);
      await load();
    } catch (e) { error = String(e); }
    finally { busy = false; }
  }
  async function revoke(id: string) {
    if (busy) return;
    busy = true;
    try { await api('DELETE', `/invitations/${id}`); link = ''; await load(); }
    catch (e) { error = String(e); }
    finally { busy = false; }
  }
  async function extend(id: string) {
    if (busy) return;
    busy = true; error = '';
    try {
      const row = await post<Invite>(`/invitations/${id}/extend`);
      board.flash(`Access now ends ${row.session_expires_at ? shortDate(row.session_expires_at) : 'later'}`);
      await load();
    } catch (e) { error = String(e); }
    finally { busy = false; }
  }
  async function reissue(invitation: Invite) {
    if (busy) return;
    busy = true; error = ''; link = '';
    try {
      const result = await post<{ secret: string }>(`/invitations/${invitation.id}/reissue`);
      show(result.secret, guestName(invitation));
      await load();
    } catch (e) { error = String(e); }
    finally { busy = false; }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(link); copied = true; }
    catch { error = 'Clipboard unavailable. Select and copy the link below.'; }
  }
  const guestName = (invitation: Invite) => board.accounts[invitation.account_id]?.display_name ?? invitation.account_id;
  function status(invitation: Invite): string {
    if (invitation.revoked_at) return 'Cancelled';
    if (invitation.accepted_at) {
      if (invitation.session_revoked_at) return 'Signed out';
      if (invitation.session_expires_at && new Date(invitation.session_expires_at) < new Date()) return 'Access ended';
      return invitation.session_expires_at ? `Access ends ${shortDate(invitation.session_expires_at)}` : 'Accepted';
    }
    return new Date(invitation.expires_at) < new Date() ? 'Expired' : 'Pending';
  }
  const live = (invitation: Invite) => !!invitation.accepted_at && !invitation.revoked_at && !invitation.session_revoked_at;
</script>
<section class="invite-panel" aria-label="Guest invitations">
  <h3>Invite a project guest</h3>
  <p><strong>Section: {sectionName}</strong> · Guest · Edit</p>
  <p class="muted small">The guest can work inside this private section (and anything else you share with them later), not manage members or other sections. Anyone holding the link can accept it; this does not verify an email identity.</p>
  {#if !isPrivate}<p>Make this section private above before inviting.</p>{/if}
  <form onsubmit={(e) => { e.preventDefault(); void issue(); }}>
    <label>Guest name<input aria-label="Guest name" bind:value={name} required maxlength="100" /></label>
    <label>Guest handle<input aria-label="Guest handle" bind:value={handle} required pattern="[a-z][a-z0-9_-]*" maxlength="40" placeholder="guest" /></label>
    <button class="primary" disabled={busy || !isPrivate}>Create invitation link</button>
  </form>
  {#if error}<p role="alert" class="error">{error}</p>{/if}
  {#if link}
    <p class="small">Link for {linkFor}: single use · expires in 7 days · shown only here. Copy and send it privately yourself.</p>
    <input aria-label="Invitation link" readonly value={link} onclick={(e) => e.currentTarget.select()} />
    <button class="chip-btn" onclick={copy}>{copied ? 'Copied' : 'Copy invitation link'}</button>
    <p class="muted small">Tell them to open it in Safari or Chrome, not inside the messaging app, and that each device (phone, laptop, installed app) needs its own link: use <b>Copy a link for another device</b> below.</p>
  {/if}
  <p class="muted small">Accepted links sign in that browser for 30 days; <b>Extend 30 days</b> pushes the end out from today, and both of you are told three days before it ends. A signed-out guest needs a fresh link. Cancel revokes that link and its session. To end all project access, also remove the person's share above.</p>
  {#each invitations as invitation (invitation.id)}
    <div class="invite-row" data-testid="invite-row">
      <span class="small">{guestName(invitation)} · {status(invitation)}</span>
      <span class="invite-actions">
        {#if live(invitation)}<button class="chip-btn" disabled={busy} onclick={() => extend(invitation.id)}>Extend 30 days</button>{/if}
        {#if !invitation.revoked_at}<button class="chip-btn" disabled={busy} onclick={() => reissue(invitation)}>Copy a link for another device</button>{/if}
        {#if !invitation.revoked_at}<button class="chip-btn" disabled={busy} onclick={() => revoke(invitation.id)}>Cancel invitation / session</button>{/if}
      </span>
    </div>
  {/each}
</section>
<style>
  .invite-panel { border-top: 1px solid var(--line); padding-top: 16px; margin-top: 16px; }
  form { display: grid; gap: 8px; }
  label { display: grid; gap: 4px; }
  input { width: 100%; box-sizing: border-box; }
  .invite-row { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; padding: 8px 0; }
  .invite-actions { display: flex; flex-wrap: wrap; gap: 6px; }
</style>
