<script lang="ts">
  // The quiet line under a channel's (or a thread's) last message while an agent's answer is
  // still to come: "My Agent will answer…" between the mention and the runner's take, then
  // "My Agent is working…" until the turn ends. Fed by the chat store's `turns`.
  import type { AgentTurn } from '../../lib/chat';
  import { board } from '../../lib/store.svelte';
  import Avatars from '../Avatars.svelte';

  let { turns }: { turns: AgentTurn[] } = $props();

  const shown = $derived(
    turns.flatMap((t) => {
      const who = board.accounts[t.agent_id];
      return who ? [{ t, who }] : [];
    }),
  );
</script>

{#if shown.length}
  <div class="chat-working" role="status" aria-live="polite">
    {#each shown as { t, who } (t.agent_id)}
      <div class="chat-working-row" data-agent-turn={t.state} data-agent={t.agent_id}>
        <Avatars people={[who]} />
        <span>{who.display_name} {t.state === 'working' ? 'is working' : 'will answer'}<span class="chat-working-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></span>
      </div>
    {/each}
  </div>
{/if}
