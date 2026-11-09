<script lang="ts">
  // The coloured robot at the top of the app: talk to an agent outside any card. Its menu lists
  // each agent's own chat (`agent:<id>`, service/chat.py); picking one opens it in the chat panel
  // on a wide screen, or the Chat page on a phone. Anything you post there reaches the agent
  // without an @mention, and the agent answers in the same chat. The badge counts unread
  // messages in agents' chats (an agent's answer you have not read yet).
  import { agentChats, badge, chatHref } from '../lib/chat';
  import { chat } from '../lib/chat.svelte';
  import { chatPanel } from '../lib/chatpanel.svelte';
  import Dropdown from './Dropdown.svelte';
  import Icon from './Icon.svelte';

  let { cls = '' }: { cls?: string } = $props();

  let open = $state(false);
  const agents = $derived(agentChats(chat.channels));

  function pick(e: MouseEvent, key: string) {
    open = false;
    // A modifier or middle click keeps the link's own behaviour (a new tab on the Chat page).
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    // A phone, or the Chat page itself (which never shows the panel): the link opens the page.
    if (!chatPanel.fits || /^#\/chat(\/|$|\?)/.test(location.hash)) return;
    e.preventDefault();
    chatPanel.show(key);
  }
</script>

{#if agents.chats.length}
  <Dropdown title={agents.unread ? `Chat with an agent, ${agents.unread} unread` : 'Chat with an agent'} cls="agent-chat {cls}" align="right" float bind:open>
    {#snippet label()}
      <span class="agent-chat-mark" aria-hidden="true"><Icon name="robot" size={19} /></span>
      <span>Agents</span>
      {#if agents.unread}<span class="agent-chat-badge" aria-hidden="true">{badge(agents.unread)}</span>{/if}
    {/snippet}
    <nav class="menu agent-chat-menu" aria-label="Agents to chat with">
      <p class="agent-chat-hint muted small">Ask a question or ask for something in Slate. No card needed.</p>
      {#each agents.chats as c (c.key)}
        <a
          href={chatHref(c.key)}
          class:unread={c.unread > 0}
          class:active={chatPanel.open && chatPanel.channel === c.key}
          data-agent-chat={c.account_id}
          onclick={(e) => pick(e, c.key)}
        >
          <span class="avatar agent" aria-hidden="true"><Icon name="robot" size={13} /></span>
          <span class="grow">{c.name}</span>
          {#if c.unread}<span class="count" aria-label="{c.unread} unread">{badge(c.unread)}</span>{/if}
        </a>
      {/each}
    </nav>
  </Dropdown>
{/if}
