<script lang="ts">
  // The Chat page (`#/chat[/<channel>]`): every channel on the left (General, each agent's own
  // chat, then each section and its lists, with unread and mention badges), the open channel on the right. On a phone
  // the same page shows one pane at a time: the channel list, then the channel with a back
  // button. Every action is in both.
  import { badge, channelIcon, chatHref, type Channel } from '../../lib/chat';
  import { chat } from '../../lib/chat.svelte';
  import Icon from '../Icon.svelte';
  import ChannelView from './ChannelView.svelte';

  let { channelKey = null, threadId = null }: { channelKey?: string | null; threadId?: string | null } = $props();

  let q = $state('');
  const shown = $derived(
    chat.channels.filter((c) => !q.trim() || c.name.toLowerCase().includes(q.trim().toLowerCase())),
  );
  // With nothing picked, a wide screen opens the first channel it can see (General, unless
  // this is a guest, who has no General); a phone shows the list.
  const wide = typeof matchMedia === 'function' ? matchMedia('(min-width: 761px)').matches : true;
  const open = $derived(channelKey ?? (wide ? (chat.channels[0]?.key ?? null) : null));

  function indent(c: Channel): boolean {
    return c.kind === 'list';
  }
</script>

<section class="chat-page" class:picked={!!channelKey} aria-label="Chat">
  <aside class="chat-channels" aria-label="Chats">
    <header class="chat-channels-head">
      <h1>Chat</h1>
      <button class="link small" disabled={!chat.unread} onclick={() => void chat.readAll()}>Mark all read</button>
    </header>
    <input class="chat-filter" type="search" placeholder="Find a chat" aria-label="Find a chat" bind:value={q} />
    <nav class="chat-channel-list">
      {#each shown as c (c.key)}
        <a
          class="chat-channel"
          class:active={c.key === open}
          class:unread={c.unread > 0}
          class:indent={indent(c) && !q.trim()}
          class:section={c.kind === 'section'}
          class:agent={c.kind === 'agent'}
          href={chatHref(c.key)}
          data-channel={c.key}
        >
          <Icon name={channelIcon(c)} size={14} />
          <span class="grow">{c.name}</span>
          {#if c.mentions}
            <span class="count hot" aria-label="{c.mentions} mentions">@{badge(c.mentions)}</span>
          {:else if c.unread}
            <span class="count" aria-label="{c.unread} unread">{badge(c.unread)}</span>
          {/if}
        </a>
      {/each}
      {#if !shown.length}<p class="muted small pad-row">No chat matches.</p>{/if}
    </nav>
  </aside>
  {#if open}
    {#key open}
      <ChannelView channelKey={open} {threadId} onback={() => (location.hash = '#/chat')} />
    {/key}
  {/if}
</section>
