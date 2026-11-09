<script lang="ts">
  // The chat panel on the right of any page (lib/chatpanel.ts): a channel picker, the open
  // channel (its thread over it, as on a phone), a link to the full Chat page, and Close.
  import { badge, channelIcon, chatHref } from '../../lib/chat';
  import { chat } from '../../lib/chat.svelte';
  import { panelChannelFor } from '../../lib/chatpanel';
  import { chatPanel } from '../../lib/chatpanel.svelte';
  import Dropdown from '../Dropdown.svelte';
  import Icon from '../Icon.svelte';
  import ChannelView from './ChannelView.svelte';

  let picking = $state(false);
  // A remembered channel this account cannot see (a guest's browser starts on General; a list
  // was unshared or archived) would load as "channel not found": move to one it can.
  $effect(() => {
    if (!chat.loaded) return;
    const to = panelChannelFor(chatPanel.channel, chat.channels);
    if (to && to !== chatPanel.channel) chatPanel.pick(to);
  });
  const current = $derived(chat.channel(chatPanel.channel));
  // The panel's own badge counts the other channels: the open one is read as you look.
  const elsewhere = $derived(chat.channels.filter((c) => c.key !== chatPanel.channel));
  const mentions = $derived(elsewhere.reduce((n, c) => n + c.mentions, 0));
  const unread = $derived(elsewhere.reduce((n, c) => n + c.unread, 0));

  function pick(key: string) {
    picking = false;
    chatPanel.pick(key);
  }

  // The grip on the left edge: drag it (pointer capture, so a fast drag past the edge still
  // follows), arrow keys move it 24px, double-click puts the width back.
  let dragging = $state(false);
  function grab(e: PointerEvent) {
    if (e.button !== 0) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragging = true;
  }
  function drag(e: PointerEvent) {
    if (!dragging) return;
    chatPanel.resize(window.innerWidth - e.clientX);
  }
  function release(e: PointerEvent) {
    if (!dragging) return;
    dragging = false;
    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
  }
  function gripKey(e: KeyboardEvent) {
    if (e.key === 'ArrowLeft') chatPanel.resize(chatPanel.width + 24);
    else if (e.key === 'ArrowRight') chatPanel.resize(chatPanel.width - 24);
    else if (e.key === 'Home') chatPanel.resetWidth();
    else return;
    e.preventDefault();
  }
</script>

<aside class="chat-panel" class:dragging class:fullscreen={chatPanel.fullscreen} aria-label="Chat panel" data-chat-panel={chatPanel.channel} style="--chat-panel-w: {chatPanel.width}px">
  {#if !chatPanel.fullscreen}
    <button
      type="button"
      class="chat-panel-grip"
      aria-label="Resize chat panel"
      title="Drag to resize. Double-click to reset."
      onpointerdown={grab}
      onpointermove={drag}
      onpointerup={release}
      onpointercancel={release}
      ondblclick={() => chatPanel.resetWidth()}
      onkeydown={gripKey}
    ></button>
  {/if}
  <header class="chat-panel-head">
    <Dropdown title="Switch chat" cls="chat-panel-pick" bind:open={picking}>
      {#snippet label()}
        <Icon name={channelIcon(current ?? { key: chatPanel.channel })} size={14} />
        <span class="chat-panel-name">{current?.name ?? 'Chat'}</span>
        <Icon name="down" size={12} />
        {#if mentions}<span class="count hot" aria-label="{mentions} mentions elsewhere">@{badge(mentions)}</span
          >{:else if unread}<span class="count" aria-label="{unread} unread elsewhere">{badge(unread)}</span>{/if}
      {/snippet}
      <nav class="menu chat-panel-list" aria-label="Chats">
        {#each chat.channels as c (c.key)}
          <button class:active={c.key === chatPanel.channel} class:unread={c.unread > 0} class:indent={c.kind === 'list'} onclick={() => pick(c.key)}>
            <Icon name={channelIcon(c)} size={13} />
            <span class="grow">{c.name}</span>
            {#if c.mentions}<span class="count hot">@{badge(c.mentions)}</span>{:else if c.unread}<span class="count">{badge(c.unread)}</span>{/if}
          </button>
        {/each}
      </nav>
    </Dropdown>
    <button
      class="icon"
      aria-label={chatPanel.expanded ? 'Compact chat' : 'Expand chat width'}
      title={chatPanel.expanded ? 'Compact chat' : 'Expand chat width'}
      onclick={() => chatPanel.toggleWidth()}
    ><Icon name={chatPanel.expanded ? 'chevronRight' : 'chevronLeft'} size={18} /></button>
    <button
      class="icon"
      aria-label={chatPanel.fullscreen ? 'Exit full screen' : 'Full screen'}
      title={chatPanel.fullscreen ? 'Exit full screen' : 'Full screen'}
      aria-pressed={chatPanel.fullscreen}
      onclick={() => chatPanel.toggleFullscreen()}
    ><Icon name={chatPanel.fullscreen ? 'collapse' : 'expand'} size={16} /></button>
    <a class="icon" href={chatHref(chatPanel.channel, chatPanel.thread)} title="Open as a page" aria-label="Open chat as a page" onclick={() => chatPanel.expand(location.hash)}
      ><Icon name="open" size={16} /></a
    >
    <button class="icon" aria-label="Close chat" title="Close chat" onclick={() => chatPanel.hide()}><Icon name="x" size={18} /></button>
  </header>
  {#key chatPanel.channel}
    <ChannelView channelKey={chatPanel.channel} threadId={chatPanel.thread} compact onthread={(id) => chatPanel.setThread(id)} />
  {/key}
</aside>
