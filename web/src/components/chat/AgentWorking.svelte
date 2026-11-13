<script lang="ts">
  // The quiet line under a channel's (or a thread's) last message while an agent's answer is
  // still to come: "My Agent will answer…" between the mention and the runner's take, then
  // "My Agent is working…" until the turn ends. Fed by the chat store's `turns`. A turn whose
  // lease ran out reads "My Agent stopped answering", with Clear for a person: the runner lost
  // the run, and Clear confirms it is over (a mention that waited goes back to the agent).
  import type { AgentTurn } from '../../lib/chat';
  import { chat } from '../../lib/chat.svelte';
  import { board } from '../../lib/store.svelte';
  import Avatars from '../Avatars.svelte';

  let { turns, channel }: { turns: AgentTurn[]; channel: string } = $props();

  const shown = $derived(
    turns.flatMap((t) => {
      const who = board.accounts[t.agent_id];
      return who ? [{ t, who }] : [];
    }),
  );
  const person = $derived(board.me?.account.kind === 'person');
</script>

{#if shown.length}
  <div class="chat-working" role="status" aria-live="polite">
    {#each shown as { t, who } (t.agent_id)}
      <div class="chat-working-row" data-agent-turn={t.state} data-agent={t.agent_id}>
        <Avatars people={[who]} />
        {#if t.state === 'expired'}
          <span>{who.display_name} stopped answering</span>
          {#if person}
            <button
              class="link small"
              data-clear-turn
              title="{who.display_name}'s run ended without an answer: Clear ends it. Mentioning {who.display_name} again asks once more."
              onclick={() => void chat.clearTurn(channel, t.thread_id, t.agent_id)}>Clear</button
            >
          {/if}
        {:else}
          <span>{who.display_name} {t.state === 'working' ? 'is working' : 'will answer'}<span class="chat-working-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></span>
        {/if}
      </div>
    {/each}
  </div>
{/if}
