<script lang="ts">
  // One channel: its messages (newest at the bottom, earlier ones on demand), the composer, and
  // an open thread beside it (over it on a phone, and in the side panel). Looking at a channel
  // reads it, and keeps reading what arrives while it is open. `embedded` is the list or section
  // page's Chat tab; `compact` is the side panel, where `onthread` owns the thread instead of the
  // URL.
  import { tick, untrack } from 'svelte';
  import { channelIcon, isAgentChat, rows, turnsIn, type ChatMessage } from '../../lib/chat';
  import { chat } from '../../lib/chat.svelte';
  import { chatPanel } from '../../lib/chatpanel.svelte';

  import { board } from '../../lib/store.svelte';
  import EmptyState from '../EmptyState.svelte';
  import AgentWorking from './AgentWorking.svelte';
  import Icon from '../Icon.svelte';
  import ChatComposer from './ChatComposer.svelte';
  import MakeTask from './MakeTask.svelte';
  import MessageItem from './MessageItem.svelte';

  let {
    channelKey,
    threadId = null,
    embedded = false,
    compact = false,
    onback = null,
    onthread = null,
  }: {
    channelKey: string;
    threadId?: string | null;
    embedded?: boolean;
    compact?: boolean;
    onback?: (() => void) | null;
    onthread?: ((id: string | null) => void) | null;
  } = $props();

  const channel = $derived(chat.channel(channelKey));
  const name = $derived(
    channel?.name ??
      (channelKey === 'general'
        ? 'General'
        : isAgentChat(channelKey)
          ? (board.accounts[channelKey.slice(6)]?.display_name ?? '')
        : (board.lists[channelKey.slice(5)]?.name ?? board.sections.find((s) => `section:${s.id}` === channelKey)?.name ?? '')),
  );
  const messages = $derived(chat.pages[channelKey]);
  const shown = $derived(rows(messages ?? [], board.now));
  let rootId = $state<string | null>(null);
  const thread = $derived(rootId ? chat.threads[rootId] : undefined);
  // An agent's answer still to come: the channel's own under its messages, a thread's in it.
  const working = $derived(turnsIn(chat.turns[channelKey], null));
  const threadWorking = $derived(rootId ? turnsIn(chat.turns[channelKey], rootId) : []);
  let making = $state<ChatMessage | null>(null);
  let scroller = $state<HTMLElement>();
  let threadScroller = $state<HTMLElement>();
  let nearBottom = true;
  let threadNearBottom = true;
  let threadJump: string | null = null;

  const boardHref = $derived(
    channel?.kind === 'list' || channelKey.startsWith('list:')
      ? `#/list/${channelKey.slice(5)}`
      : channelKey.startsWith('section:')
        ? `#/section/${channelKey.slice(8)}`
        : null,
  );
  const agentChat = $derived(isAgentChat(channelKey));
  const chatAgent = $derived(agentChat ? board.accounts[channelKey.slice(6)] : undefined);
  // `#Todo` for a channel; an agent's chat is named like a person: `My Agent`.
  const label = $derived(agentChat ? name : `#${name}`);
  const icon = $derived(channelIcon(channel ?? { key: channelKey }));
  const about = $derived(
    agentChat
      ? channel?.mention_required
        ? `Shared project chat. Mention @${chatAgent?.handle ?? name} to ask ${name} to answer. Plain messages, replies and mentions to other people do not start the agent. Everyone who works with ${name} can read this chat.`
        : `Talk to ${name} about anything, no card needed: ask a question or ask for something in Slate. Every message reaches ${name}, and the answer comes here. Everyone who works with ${name} can read it: members, and the guests of its projects.`
      : channelKey === 'general'
      ? 'Everyone, every list: you and the agents.'
      : channelKey.startsWith('list:')
        ? 'Talk about this list’s work. Mention @someone to reach them; type # to link a task.'
        : 'Talk about everything in this section.',
  );

  $effect(() => {
    const key = channelKey;
    // Loading is not reading; each message separately observes actual viewport exposure.
    return untrack(() => {
      nearBottom = true;
      void chat.load(key).then(() => toBottom(true));
    });
  });

  $effect(() => {
    const id = threadId;
    rootId = null;
    if (!id) return;
    const jump = threadJump;
    threadJump = null;
    threadNearBottom = !jump;
    void chat.loadThread(id).then(async (root) => {
      if (threadId !== id) return;
      rootId = root;
      await tick();
      const target = jump && threadScroller?.querySelector(`[data-message="${jump}"]`);
      if (target) target.scrollIntoView({ block: 'center' });
      else if (threadScroller) threadScroller.scrollTop = threadScroller.scrollHeight;
    });
  });

  // New messages keep the view at the bottom, unless you scrolled up to read.
  $effect(() => {
    void messages?.length;
    void working.length;
    if (nearBottom) void toBottom();
  });
  $effect(() => {
    void thread?.length;
    void threadWorking.length;
    void tick().then(() => {
      if (threadScroller && threadNearBottom) threadScroller.scrollTop = threadScroller.scrollHeight;
    });
  });

  async function toBottom(force = false) {
    await tick();
    if (scroller && (force || nearBottom)) scroller.scrollTop = scroller.scrollHeight;
  }

  function onScroll() {
    if (!scroller) return;
    nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  }

  async function older() {
    if (!scroller) return;
    const from = scroller.scrollHeight;
    await chat.loadOlder(channelKey);
    await tick();
    scroller.scrollTop = scroller.scrollHeight - from;
  }

  async function firstUnread() {
    const id = channel?.first_unread_id;
    if (!id) return;
    nearBottom = false;
    if (channel?.first_unread_thread_id) {
      threadJump = id;
      threadNearBottom = false;
      if (rootId === channel.first_unread_thread_id) {
        threadScroller?.querySelector(`[data-message="${id}"]`)?.scrollIntoView({ block: 'center' });
        threadJump = null;
        return;
      }
      setThread(channel.first_unread_thread_id);
      return;
    }
    while (!chat.pages[channelKey]?.some(m => m.id === id) && chat.older[channelKey]) {
      const length = chat.pages[channelKey]?.length;
      await chat.loadOlder(channelKey);
      if (chat.pages[channelKey]?.length === length) break;
    }
    await tick();
    scroller?.querySelector(`[data-message="${id}"]`)?.scrollIntoView({ block: 'center' });
  }

  /** Open or close a thread through the URL, so a reload or a shared link keeps it (the side
   * panel keeps it itself: `onthread`). */
  function setThread(id: string | null) {
    if (onthread) {
      onthread(id);
      return;
    }
    const [path, query = ''] = location.hash.split('?');
    const q = new URLSearchParams(query);
    if (id) q.set('thread', id);
    else q.delete('thread');
    const s = q.toString();
    location.hash = s ? `${path}?${s}` : path;
  }
</script>

<section class="chat-view" class:embedded class:compact class:with-thread={!!threadId} aria-label="{label} chat">
  <div class="chat-main">
    <header class="chat-head">
      {#if onback}
        <button class="icon chat-back" aria-label="All chats" onclick={onback}><Icon name="chevronLeft" size={18} /></button>
      {/if}
      <span class="chat-head-mark" class:agent={agentChat} aria-hidden="true"><Icon name={icon} size={16} /></span>
      <div class="chat-head-text">
        <h2>{name}</h2>
        <p class="muted small">{about}</p>
      </div>
      {#if boardHref && !embedded && !compact}
        <a class="chip-btn" href={boardHref} title="Open the board"><Icon name="board" size={14} />Board</a>
      {/if}
      {#if !embedded && !compact && chatPanel.fits}
        <button
          class="chip-btn"
          title="Back to the side panel"
          onclick={() => {
            location.hash = chatPanel.dock(channelKey, threadId);
          }}><Icon name="collapse" size={14} />Panel</button
        >
      {/if}
    </header>

    {#if channel?.first_unread_id}
      <button class="link small" onclick={firstUnread}>First unread · {channel.unread} unread</button>
    {/if}
    <div class="chat-scroll" bind:this={scroller} onscroll={onScroll}>
      {#if messages === undefined}
        <p class="muted pad-row">Loading…</p>
      {:else}
        {#if chat.older[channelKey]}
          <button class="link small chat-older" onclick={older}>Load earlier messages</button>
        {:else}
          <div class="chat-start">
            <span class="chat-start-mark" class:agent={agentChat}><Icon name={icon} size={22} /></span>
            <h3>{agentChat ? `This is the start of your chat with ${name}` : `This is the start of #${name}`}</h3>
            <p class="muted">{about}</p>
          </div>
        {/if}
        {#if !messages.length}
          <EmptyState
            icon="chat"
            title="No messages yet"
            hint={agentChat && !channel?.mention_required ? `Ask ${name} anything. No @mention needed.` : 'Say hello, or @mention an agent to hand it something.'}
          />
        {/if}
        {#each shown as r (r.key)}
          {#if r.kind === 'day'}
            <div class="chat-day" role="separator"><span>{r.label}</span></div>
          {:else}
            <MessageItem m={r.message} head={r.head} onthread={setThread} ontask={(m) => (making = m)} />
          {/if}
        {/each}
        <AgentWorking turns={working} channel={channelKey} />
      {/if}
    </div>

    <ChatComposer channel={channelKey} placeholder="Message {label}" autofocus={!embedded && !compact && !threadId} />
  </div>

  {#if threadId}
    <aside class="chat-thread" aria-label="Thread">
      <header class="chat-thread-head">
        <div>
          <h3>Thread</h3>
          <span class="muted small">{label}</span>
        </div>
        <button class="icon" aria-label="Close thread" onclick={() => setThread(null)}><Icon name="x" size={18} /></button>
      </header>
      <div class="chat-thread-scroll" bind:this={threadScroller} onscroll={() => {
        if (threadScroller) threadNearBottom = threadScroller.scrollHeight - threadScroller.scrollTop - threadScroller.clientHeight < 80;
      }}>
        {#if !thread}
          <p class="muted pad-row">Loading…</p>
        {:else}
          <MessageItem m={thread[0]} inThread ontask={(m) => (making = m)} />
          <div class="chat-thread-count muted small">
            {thread.length - 1 ? `${thread.length - 1} ${thread.length === 2 ? 'reply' : 'replies'}` : 'No replies yet'}
          </div>
          {#each rows(thread.slice(1), board.now) as r (r.key)}
            {#if r.kind === 'message'}
              <MessageItem m={r.message} head={r.head} inThread ontask={(m) => (making = m)} />
            {/if}
          {/each}
          <AgentWorking turns={threadWorking} channel={channelKey} />
        {/if}
      </div>
      {#if rootId}
        <ChatComposer channel={channelKey} parentId={rootId} placeholder="Reply…" autofocus />
      {/if}
    </aside>
  {/if}
</section>

{#if making}
  <MakeTask m={making} onclose={() => (making = null)} />
{/if}
