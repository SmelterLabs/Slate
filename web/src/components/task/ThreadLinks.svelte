<script lang="ts">
  // The agents' own sessions for this card (`agent_threads`), as links: "Open My Agent's session ↗".
  // The badge's Open link only lives while the agent reports working, blocked or review; a done
  // run clears it, yet Slate still holds the thread, and a reply on the card reopens that same
  // session. So the task window offers the stored link whenever the card has one, hiding only a
  // link the badge is already showing. Same scheme rule as the badge (`safeAgentUrl`).
  import { agents } from '../../lib/agents.svelte';
  import { safeAgentUrl } from '../../lib/conversation';
  import { board } from '../../lib/store.svelte';
  import type { AgentFlag, AgentThread } from '../../lib/types';
  import Icon from './Icon.svelte';

  let { threads = [], flag = null }: { threads?: AgentThread[]; flag?: AgentFlag | null } = $props();

  const shownOnBadge = $derived(flag ? safeAgentUrl(flag.url, board.me?.link_schemes) : null);
  const links = $derived(
    threads.flatMap((t) => {
      const url = safeAgentUrl(t.url, board.me?.link_schemes);
      if (!url || url === shownOnBadge) return [];
      const who =
        board.accounts[t.account_id]?.display_name ?? agents.entry(t.account_id)?.account.display_name ?? 'the agent';
      return [{ key: t.account_id, url, who, newTab: /^https?:/i.test(url) }];
    }),
  );
</script>

{#if links.length}
  <div class="thread-links" data-thread-links>
    {#each links as l (l.key)}
      <a
        class="thread-link"
        href={l.url}
        target={l.newTab ? '_blank' : null}
        rel="noopener noreferrer"
        title="Open {l.who}'s session for this card in its own app"
        onclick={(e) => e.stopPropagation()}><Icon name="robot" size={12} /> Open {l.who}'s session ↗</a
      >
    {/each}
  </div>
{/if}

<style>
  .thread-links {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 12px;
    margin: -4px 0 12px;
  }
  .thread-link {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
    font-weight: 600;
    color: var(--accent);
    text-decoration: none;
  }
  .thread-link:hover {
    text-decoration: underline;
  }
</style>
