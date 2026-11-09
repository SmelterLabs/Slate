<script lang="ts">
  // How Slate reaches one agent (accounts.wake), on its Agents-page card: a runner (push), its
  // own app's scheduler (pull: shows the prompt to save there), or a person carrying the task
  // (manual: Copy brief on the card). Admins change it here.
  import { patch } from '../../lib/api';
  import { board } from '../../lib/store.svelte';
  import type { Account, Wake } from '../../lib/types';
  import Icon from '../Icon.svelte';

  let { account, wake, admin, onchange }: { account: Account; wake: Wake; admin: boolean; onchange: () => void } =
    $props();

  const LABEL: Record<Wake, string> = {
    push: 'A local connection (Run now sends a task)',
    pull: 'Its own app checks Slate on a schedule',
    manual: 'By hand or in its app (Copy brief)',
  };

  const PULL_PROMPT = `Check my Slate tasks. Use the Slate connector: call slate_work with action "next". If it returns a task, do it, sending progress with slate_work "heartbeat", then call slate_work "finish" with outcome done, review, or blocked with waiting_on set to who you need. Repeat until "next" returns no task, then stop.`;

  let showPrompt = $state(false);

  async function set(next: Wake) {
    try {
      await patch(`/accounts/${account.id}`, { wake: next });
      const known = board.accounts[account.id];
      if (known) known.wake = next;
      onchange();
    } catch (e) {
      board.flash(e instanceof Error ? e.message : String(e));
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(PULL_PROMPT);
      board.flash('Prompt copied');
    } catch {
      board.flash(PULL_PROMPT);
    }
  }
</script>

<div class="agent-wake" data-wake={wake}>
  <span class="muted small"><Icon name="robot" size={13} /> How tasks reach it:</span>
  {#if admin}
    <select
      aria-label="How {account.display_name} is woken"
      value={wake}
      onchange={(e) => void set(e.currentTarget.value as Wake)}
    >
      {#each Object.entries(LABEL) as [k, text] (k)}<option value={k}>{text}</option>{/each}
    </select>
  {:else}
    <span class="small">{LABEL[wake]}</span>
  {/if}
  {#if wake === 'pull'}
    <button class="link small" aria-expanded={showPrompt} onclick={() => (showPrompt = !showPrompt)}
      >{showPrompt ? 'Hide prompt' : 'Prompt for its scheduler'}</button
    >
  {/if}
</div>
{#if wake === 'push'}<p class="muted small">This is a delivery preference, not proof of readiness. Check Connections for a recent successful agent test.</p>{/if}
{#if wake === 'pull' && showPrompt}
  <div class="pull-prompt">
    <p class="muted small">
      Save this as a scheduled task in {account.display_name}'s app (for example every morning at 06:00), with
      Slate connected there. See docs/agents/pull-agents.md.
    </p>
    <pre>{PULL_PROMPT}</pre>
    <button class="secondary" onclick={() => void copy()}>Copy prompt</button>
  </div>
{/if}

<style>
  .agent-wake {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .agent-wake :global(svg) {
    vertical-align: -2px;
  }
  .pull-prompt pre {
    white-space: pre-wrap;
    font-size: 12px;
    padding: 8px;
    border-radius: 6px;
    background: var(--surface-2, color-mix(in srgb, var(--text) 5%, transparent));
  }
</style>
