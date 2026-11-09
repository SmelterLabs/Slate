<script lang="ts">
  import { board } from '../lib/store.svelte';

  import { api, ApiError } from '../lib/api';
  import { COOKIE_SESSION, invitationRecovery, inviteSecretFrom } from '../lib/collaboration';
  let { invitation = null }: { invitation?: string | null } = $props();
  let token = $state('');
  async function join() {
    if (busy) return;
    busy = true;
    error = '';
    errorStatus = null;
    try {
      const result = await api<{ section_id: string }>('POST', '/invitations/accept', { secret: invitation }, null);
      localStorage.setItem('slate.token', COOKIE_SESSION); // marker only; credential is HttpOnly
      location.hash = `#/section/${result.section_id}`;
      location.reload();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      errorStatus = e instanceof ApiError ? e.status : 0;
    }
    finally { busy = false; }
  }
  let error = $state('');
  let errorStatus = $state<number | null>(null);
  let busy = $state(false);

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    error = '';
    busy = true;
    const pastedLink = inviteSecretFrom(token) !== null;
    try {
      await board.signIn(token.trim());
    } catch (e) {
      // A pasted invitation link fails for the invitation's reasons (used, expired, cancelled),
      // not because the token is wrong.
      if (pastedLink) {
        const status = e instanceof ApiError ? e.status : 0;
        error = `${e instanceof Error ? e.message : String(e)} ${invitationRecovery(status)}`;
      } else error = 'Slate did not accept that token. Check it and paste it again.';
    } finally {
      busy = false;
    }
  }
</script>

<div class="token-screen">
  {#if invitation !== null}
    <div class="token-card">
      <span class="logo" aria-hidden="true">S</span>
      <h1>Join your project</h1>
      <p>Your invitation gives you guest access to edit one private project section: its work, ideas, docs and discussion.</p>
      <p class="muted">Only accept a link from someone you trust. This signs this browser in for 30 days. It does not verify an email address.</p>
      {#if board.token}<p>This replaces the account signed in on this browser.</p>{/if}
      <p class="muted">Invitation links are single-use and expire after seven days. Open this one in Safari or Chrome, not inside a messaging app; each device (phone, laptop, installed app) needs its own link.</p>
      {#if invitation === ''}<p role="alert">This invitation link is incomplete. Ask the person who invited you for a fresh invitation link.</p>{/if}
      {#if error}
        <p role="alert" class="error">{error}</p>
        <p>{invitationRecovery(errorStatus)}</p>
      {/if}
      <button class="primary" disabled={busy || !invitation} onclick={join}>{busy ? 'Joining…' : 'Accept invitation & sign in'}</button>
      <a href="#/home" onclick={() => location.reload()}>Cancel</a>
    </div>
  {:else}
  <form class="token-card" onsubmit={submit}>
    <span class="logo" aria-hidden="true">S</span>
    <h1>Slate</h1>
    <p class="muted">Project guest? Open the invitation from your project owner, or paste the whole invitation link here (the installed app has no address bar). If signed out or expired, ask them for a fresh link.</p>
    <p class="muted">Paste your Slate token once. This browser keeps it until you sign out.</p>
    <label for="token">Token</label>
    <input
      id="token"
      type="password"
      autocomplete="off"
      placeholder="slate_… or an invitation link"
      bind:value={token}
      required
    />
    {#if error}<p class="error">{error}</p>{/if}
    <button class="primary" type="submit" disabled={busy || !token.trim()}>
      {busy ? 'Checking…' : 'Sign in'}
    </button>
  </form>
  {/if}
</div>
