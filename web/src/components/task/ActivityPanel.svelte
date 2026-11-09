<script lang="ts">
  import type { Snippet } from 'svelte';
  // The right-hand column: comments as cards, history as small gray lines between them, all in
  // time order (ClickUp's activity). Live: the detail refreshes on every event for the item,
  // and this reloads the feed. Comments react, edit (your own) and reply (an @mention).
  import { tick, untrack } from 'svelte';
  import {
    QUICK_REACTIONS,
    entriesFrom,
    highlightMentions,
    stamp,
    statusColor,
    timeline,
  } from '../../lib/activity';
  import { automationOf, automations } from '../../lib/automations.svelte';
  import { collapsedChat, defaultAsk, firstLine, isChat, pendingAgent, shownProgress, updatedAgo } from '../../lib/conversation';
  import { draftKey, readDraft } from '../../lib/drafts';
  import { hasDraft, saveDraft } from '../../lib/drafts.svelte';
  import { verb } from '../../lib/history';
  import { renderMarkdown } from '../../lib/markdown';
  import { liveRefs } from '../../lib/refs.svelte';
  import { ApiError } from '../../lib/api';
  import { canRevert, revertEvent, revertQuestion } from '../../lib/revert';
  import { board } from '../../lib/store.svelte';
  import { task } from '../../lib/task.svelte';
  import type { Comment, ItemDetail, SlateEvent } from '../../lib/types';
  import Avatars from '../Avatars.svelte';
  import Composer from './Composer.svelte';
  import Icon from './Icon.svelte';
  import WatchersPicker from './WatchersPicker.svelte';

  // `actions`: how the next turn starts (Run now, Hand off), shown with the composer and its Ask
  // picker so the three sit together.
  let { item, onattach, actions }: { item: ItemDetail; onattach: () => void; actions?: Snippet } = $props();

  let feed = $state<HTMLElement>();
  let composer = $state<ReturnType<typeof Composer>>();
  let picking = $state<string | null>(null);
  let editing = $state<string | null>(null);
  let editText = $state('');

  // An edit left open when the card closed comes back open, with what was typed.
  $effect(() => {
    if (editing && item.comments.some((c) => c.id === editing)) return;
    const mine = item.comments.find((c) => c.author_id === task.me && hasDraft(draftKey.editComment(c.id)));
    if (mine) untrack(() => startEdit(mine));
  });

  function startEdit(c: Comment) {
    editing = c.id;
    editText = readDraft(draftKey.editComment(c.id)) || c.body_md;
  }

  function setEditText(c: Comment, v: string) {
    editText = v;
    saveDraft(draftKey.editComment(c.id), v === c.body_md ? '' : v);
  }

  function stopEdit(c: Comment) {
    editing = null;
    saveDraft(draftKey.editComment(c.id), '');
  }

  // Reload the feed whenever the detail refreshes (every event on the item lands here).
  $effect(() => {
    const id = item.id;
    void item.updated_at;
    void item.events.length;
    void item.events.at(-1)?.id;
    void task.loadActivity(id);
  });

  const entries = $derived(task.activity ?? entriesFrom(item.comments, item.events));
  const rows = $derived(
    timeline(entries).filter((r) => task.filter === 'all' || (task.filter === 'comments') === (r.kind === 'comment')),
  );
  const commentCount = $derived(entries.filter((e) => e.kind === 'comment').length);

  // The card is the conversation: an agent's comments and the comments addressed to it read as
  // a chat; exchanges older than the newest collapse to their first line (click to expand).
  const isAgent = (id: string) => board.accounts[id]?.kind === 'agent';
  const collapsed = $derived(collapsedChat(rows, isAgent));
  let opened = $state<Record<string, boolean>>({});

  // The live bubble: while an agent works, its latest note and how long ago it reported.
  const working = $derived(item.agent?.state === 'working' ? item.agent : null);
  let clock = $state(new Date());
  $effect(() => {
    if (!working) return;
    clock = new Date();
    const t = setInterval(() => (clock = new Date()), 1000);
    return () => clearInterval(t);
  });

  // The dots at the foot of the thread: an agent has the card's question, sent or started.
  const allComments = $derived(entries.flatMap((e) => (e.kind === 'comment' && e.comment ? [e.comment] : [])));
  const pending = $derived(pendingAgent(item, allComments, isAgent,
    [...item.events, ...entries.flatMap((e) => e.kind === 'event' && e.event ? [e.event] : [])]));
  // Who the Ask picker starts on (the card's agent, else the one you asked last).
  const askDefault = $derived(defaultAsk(item, allComments, board.pickable, lastAsk(), board.projectAgents(item.list_id)));

  function lastAsk(): string | null {
    try {
      return localStorage.getItem('slate.lastAsk');
    } catch {
      return null;
    }
  }

  // Stay pinned to the bottom when new rows arrive, unless the reader scrolled up.
  let pinned = true;
  $effect(() => {
    void rows.length;
    void pending;
    if (pinned) void tick().then(() => feed && (feed.scrollTop = feed.scrollHeight));
  });

  // The banner's Answer: open the comment that asks, scroll it into view, light it, focus the box.
  let lit = $state<string | null>(null);
  $effect(() => {
    const req = task.revealing;
    if (!req) return;
    untrack(() => {
      if (req.commentId) opened[req.commentId] = true;
      void tick().then(() => {
        const el = req.commentId ? feed?.querySelector<HTMLElement>(`[data-comment="${req.commentId}"]`) : null;
        if (el) {
          el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          lit = req.commentId;
          setTimeout(() => lit === req.commentId && (lit = null), 2400);
        }
        composer?.focus();
      });
    });
  });

  const ctx = {
    name: (id: string) => board.accountName(id),
    status: (id: string) => statusName(id),
    title: (id: string) => board.items[id]?.title,
  };

  function statusName(id: string): string | undefined {
    for (const l of Object.values(board.lists)) {
      const s = l.statuses.find((x) => x.id === id);
      if (s) return s.name;
    }
    return undefined;
  }

  function statusOf(id: unknown) {
    for (const l of Object.values(board.lists)) {
      const s = l.statuses.find((x) => x.id === id);
      if (s) return s;
    }
    return undefined;
  }

  function person(id: string) {
    const a = board.accounts[id];
    return a ? [a] : [];
  }

  function body(c: Comment): string {
    return highlightMentions(
      renderMarkdown(c.body_md, { refs: liveRefs() }),
      (h) => Object.values(board.accounts).some((a) => a.handle === h),
      board.me?.account.handle,
    );
  }

  function reactors(ids: string[]): string {
    return ids.map((id) => (id === task.me ? 'You' : (board.accountName(id) ?? 'Someone'))).join(', ');
  }

  const SOURCES: Record<string, string> = { api: 'via API', mcp: 'via MCP', system: 'automatically' };

  // The board itself writes through the API, so "via API" only says something about agents.
  function showSource(ev: SlateEvent): boolean {
    if (ev.source === 'mcp' || ev.source === 'system') return true;
    return ev.source === 'api' && board.accounts[ev.actor_id]?.kind === 'agent';
  }

  function sourceLabel(ev: SlateEvent): string {
    const rule = automationOf(ev);
    if (rule) automations.ensure();
    if (rule) return `by automation${automations.name(rule) ? ` “${automations.name(rule)}”` : ''}`;
    return SOURCES[ev.source];
  }

  function eventText(ev: SlateEvent, times: number): string {
    const who = ev.actor_id === task.me ? 'You' : (board.accountName(ev.actor_id) ?? 'Someone');
    const what = verb(ev, { ...ctx, now: board.now });
    return `${who} ${what}${times > 1 && ev.field === 'body_md' ? ` (${times} saves)` : ''}`;
  }

  async function saveEdit(c: Comment) {
    const t = editText.trim();
    if (t && t !== c.body_md && !(await task.editComment(c.id, t))) return;
    if (t && t !== c.body_md) c.body_md = t;
    stopEdit(c);
  }

  // Revert from the log: a change someone else made goes back through POST /events/{id}/revert
  // (a new change as you). The row stays disabled with the server's reason after a 409.
  let reverting = $state<string | null>(null);
  let revertBlocked = $state<Record<string, string>>({});
  async function revert(ev: SlateEvent) {
    const who = board.accountName(ev.actor_id) ?? 'someone';
    if (!window.confirm(revertQuestion(ev, who))) return;
    reverting = ev.id;
    try {
      await revertEvent(ev.id);
      board.flash('Put back. The history keeps both changes.');
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (e instanceof ApiError && e.status === 409) revertBlocked[ev.id] = msg;
      board.flash(msg);
    } finally {
      reverting = null;
    }
  }
</script>

<svelte:document onmousedown={(e) => picking && !(e.target as HTMLElement).closest('.react-pop, .react-add') && (picking = null)} />

<aside class="activity" aria-label="Activity">
  <header>
    <h2>Activity</h2>
    <div class="seg" role="tablist" aria-label="Show">
      <button role="tab" aria-selected={task.filter === 'all'} onclick={() => (task.filter = 'all')}>All</button>
      <button role="tab" aria-selected={task.filter === 'comments'} onclick={() => (task.filter = 'comments')}
        >Comments{commentCount ? ` ${commentCount}` : ''}</button
      >
      <button role="tab" aria-selected={task.filter === 'events'} onclick={() => (task.filter = 'events')}>History</button>
    </div>
    <WatchersPicker itemId={item.id} watchers={item.watchers ?? []} />
  </header>

  {#if working}
    <div class="live" role="status" aria-live="polite" data-live-bubble>
      <span class="live-dot" aria-hidden="true"></span>
      <div class="live-body">
        <div class="live-head">
          <strong>{board.accountName(working.by) ?? 'An agent'}</strong> is working{#if shownProgress(working.progress) != null}
            · {shownProgress(working.progress)}%{/if}
          <time datetime={working.at} data-live-age>{updatedAgo(working.at, clock)}</time>
        </div>
        {#if working.note}<div class="live-note">{working.note}</div>{/if}
      </div>
    </div>
  {/if}

  <div
    class="feed"
    bind:this={feed}
    onscroll={() => feed && (pinned = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 40)}
  >
    {#each rows as r (r.key)}
      {#if r.kind === 'comment' && collapsed.has(r.comment.id) && !opened[r.comment.id]}
        {@const c = r.comment}
        <button
          class="chat-collapsed"
          class:from-agent={isAgent(c.author_id)}
          data-chat-collapsed
          data-comment={c.id}
          title="Show the whole message"
          onclick={() => (opened[c.id] = true)}
        >
          <strong>{board.accountName(c.author_id) ?? 'Someone'}</strong>
          <span class="chat-line">{firstLine(c.body_md)}</span>
          <time datetime={c.created_at}>{stamp(c.created_at, board.now)}</time>
        </button>
      {:else if r.kind === 'comment'}
        {@const c = r.comment}
        <article
          class="comment"
          data-comment={c.id}
          class:lit={lit === c.id}
          class:mine={c.author_id === task.me}
          class:mentions-me={c.mentions?.includes(board.me?.account.handle ?? '')}
          class:chat={isChat(c, isAgent)}
          class:from-agent={isAgent(c.author_id)}
          data-chat={isChat(c, isAgent) ? (isAgent(c.author_id) ? 'agent' : 'person') : undefined}
        >
          <div class="who">
            <Avatars people={person(c.author_id)} />
            <strong>{board.accountName(c.author_id) ?? 'Someone'}</strong>
            <time class="muted" datetime={c.created_at} title={new Date(c.created_at).toLocaleString()}>{stamp(c.created_at, board.now)}</time>
            {#if c.edited_at}<span class="muted" title="Edited {new Date(c.edited_at).toLocaleString()}">(edited)</span>{/if}
            {#if c.assignee_id}
              <span class="ask-pill" class:resolved={!!c.resolved_at} data-ask={c.resolved_at ? 'resolved' : 'open'}>
                <Avatars people={person(c.assignee_id)} />
                Asked {board.accountName(c.assignee_id) ?? 'someone'} · {c.resolved_at ? 'Resolved' : 'Open'}
              </span>
            {/if}
          </div>
          {#if editing === c.id}
            <textarea
              class="edit"
              aria-label="Edit comment"
              rows="3"
              bind:value={() => editText, (v) => setEditText(c, v)}
              onkeydown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void saveEdit(c);
                }
                if (e.key === 'Escape') {
                  e.stopPropagation();
                  stopEdit(c);
                }
              }}
            ></textarea>
            <div class="inline small">
              <button class="primary" onclick={() => saveEdit(c)}>Save</button>
              <button class="link" onclick={() => stopEdit(c)}>Cancel</button>
            </div>
          {:else}
            <div class="md text">{@html body(c)}</div>
          {/if}
          <div class="foot">
            {#each c.reactions ?? [] as rx (rx.emoji)}
              <button
                class="rx"
                class:on={rx.account_ids.includes(task.me)}
                title={reactors(rx.account_ids)}
                aria-label="{rx.emoji} {rx.account_ids.length}, {reactors(rx.account_ids)}"
                onclick={() => task.react(c, rx.emoji)}>{rx.emoji} {rx.account_ids.length}</button
              >
            {/each}
            <span class="react-wrap">
              <button class="act react-add" title="React" aria-label="Add reaction" onclick={() => (picking = picking === c.id ? null : c.id)}
                ><Icon name="smile" size={14} /></button
              >
              {#if picking === c.id}
                <span class="react-pop" role="menu">
                  {#each QUICK_REACTIONS as e (e)}
                    <button role="menuitem" aria-label="React {e}" onclick={() => {
                      picking = null;
                      void task.react(c, e);
                    }}>{e}</button>
                  {/each}
                </span>
              {/if}
            </span>
            <button class="act" onclick={() => composer?.mentionPerson(board.accounts[c.author_id]?.handle ?? '')}>Reply</button>
            {#if c.assignee_id && !c.resolved_at && (c.assignee_id === task.me || c.author_id === task.me)}
              <button class="act" onclick={() => task.resolveComment(c.id)}>Resolve</button>
            {/if}
            {#if c.author_id === task.me && editing !== c.id}
              <button class="act" onclick={() => startEdit(c)}>Edit</button>
            {/if}
          </div>
        </article>
      {:else}
        <div class="ev" title="{new Date(r.at).toLocaleString()}{r.event.session_ref ? ` · session ${r.event.session_ref}` : ''}">
          <span class="line">
            {#if r.event.type === 'item.status_changed'}
              {@const from = statusOf(r.event.before)}
              {@const to = statusOf(r.event.after)}
              {r.event.actor_id === task.me ? 'You' : (board.accountName(r.event.actor_id) ?? 'Someone')} changed status
              {#if from}from <span class="st" style="--c: {statusColor(from)}">{from.name}</span>{/if}
              to <span class="st" style="--c: {statusColor(to)}">{to?.name ?? '?'}</span>
            {:else}
              {eventText(r.event, r.times)}
            {/if}
            {#if showSource(r.event)}<span class="src">{sourceLabel(r.event)}</span>{/if}
          </span>
          <time datetime={r.at}>{stamp(r.at, board.now)}</time>
          {#if canRevert(r.event, task.me)}
            <button
              class="revert"
              data-revert={r.event.id}
              disabled={reverting === r.event.id || !!revertBlocked[r.event.id]}
              title={revertBlocked[r.event.id] ?? 'Put this change back (a new change, as you)'}
              onclick={() => revert(r.event)}>{revertBlocked[r.event.id] ? 'Changed since' : 'Revert'}</button
            >
          {/if}
        </div>
      {/if}
    {:else}
      <p class="empty muted">No {task.filter === 'comments' ? 'comments' : 'activity'} yet.</p>
    {/each}
    {#if pending && task.filter !== 'events'}
      <div class="typing" role="status" aria-live="polite" data-agent-pending={pending.picked ? 'working' : 'sent'}>
        <span class="dots" aria-hidden="true"><span></span><span></span><span></span></span>
        <span>
          <strong>{board.accountName(pending.id) ?? 'The agent'}</strong>
          is typing…
        </span>
      </div>
    {/if}
  </div>

  <div class="compose">
    {#if actions}{@render actions()}{/if}
    <Composer bind:this={composer} itemId={item.id} {askDefault} {onattach} />
  </div>
</aside>

<style>
  .live {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    margin: 10px 16px 0;
    padding: 8px 12px;
    border-radius: 12px;
    border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    font-size: 13px;
  }
  .live-dot {
    flex: none;
    width: 8px;
    height: 8px;
    margin-top: 5px;
    border-radius: 50%;
    background: var(--accent);
    animation: live-pulse 1.6s ease-in-out infinite;
  }
  @keyframes live-pulse {
    0%,
    100% {
      box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 70%, transparent);
    }
    50% {
      box-shadow: 0 0 0 5px color-mix(in srgb, var(--accent) 0%, transparent);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .live-dot {
      animation: none;
    }
  }
  .live-body {
    min-width: 0;
    flex: 1;
  }
  .live-head time {
    margin-left: 6px;
    color: var(--muted);
    font-size: 11.5px;
  }
  .live-note {
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .typing {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-right: 24px;
    padding: 8px 12px;
    border-left: 3px solid var(--accent);
    border-radius: 8px;
    background: color-mix(in srgb, var(--accent) 8%, transparent);
    font-size: 13px;
    color: var(--muted);
  }
  .typing strong {
    color: var(--text);
  }
  .dots {
    display: inline-flex;
    gap: 3px;
  }
  .dots span {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
    animation: typing-bounce 1.2s ease-in-out infinite;
  }
  .dots span:nth-child(2) {
    animation-delay: 0.15s;
  }
  .dots span:nth-child(3) {
    animation-delay: 0.3s;
  }
  @keyframes typing-bounce {
    0%,
    60%,
    100% {
      transform: translateY(0);
      opacity: 0.45;
    }
    30% {
      transform: translateY(-4px);
      opacity: 1;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .dots span {
      animation: none;
    }
  }
  .comment.lit {
    box-shadow: 0 0 0 2px var(--danger);
    transition: box-shadow 0.3s;
  }
  .comment.chat.from-agent {
    border-left: 3px solid var(--accent);
    margin-right: 24px;
  }
  .comment.chat:not(.from-agent) {
    margin-left: 24px;
    background: color-mix(in srgb, var(--accent) 6%, var(--panel));
  }
  .chat-collapsed {
    display: flex;
    gap: 6px;
    align-items: baseline;
    width: 100%;
    text-align: left;
    border: 1px dashed var(--line);
    background: none;
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 12.5px;
    color: var(--muted);
    cursor: pointer;
  }
  .chat-collapsed:hover {
    background: var(--card-hover);
    color: var(--text);
  }
  .chat-collapsed strong {
    flex: none;
    color: var(--text);
    font-weight: 600;
  }
  .chat-collapsed .chat-line {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chat-collapsed time {
    flex: none;
    font-size: 11px;
  }
  .chat-collapsed:not(.from-agent) {
    margin-left: 24px;
    width: calc(100% - 24px);
  }
  .ask-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-left: 6px;
    padding: 1px 8px 1px 2px;
    border-radius: 999px;
    font-size: 12px;
    border: 1px solid color-mix(in srgb, var(--prio-high) 45%, transparent);
    background: color-mix(in srgb, var(--prio-high) 12%, transparent);
  }
  .ask-pill.resolved {
    border-color: var(--line);
    background: none;
    color: var(--muted);
  }
  .activity {
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: color-mix(in srgb, var(--bg) 60%, var(--panel));
    border-left: 1px solid var(--line);
  }
  header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--line);
  }
  h2 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    margin-right: auto;
  }
  .seg {
    display: inline-flex;
    background: var(--chip);
    border-radius: 7px;
    padding: 2px;
  }
  .seg button {
    border: 0;
    background: none;
    font-size: 12px;
    padding: 4px 8px;
    border-radius: 5px;
    color: var(--muted);
  }
  .seg button[aria-selected='true'] {
    background: var(--panel);
    color: var(--text);
    box-shadow: 0 1px 2px rgb(0 0 0 / 0.15);
  }
  .feed {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 12px 16px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .ev {
    display: flex;
    gap: 8px;
    align-items: baseline;
    font-size: 12px;
    color: var(--muted);
    padding: 2px 2px 2px 10px;
    border-left: 2px solid var(--line);
    line-height: 1.45;
  }
  .ev .line {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .ev time {
    flex: none;
    font-size: 11px;
    opacity: 0.85;
  }
  .ev .revert {
    flex: none;
    font: inherit;
    font-size: 11px;
    padding: 0 6px;
    border: 1px solid var(--line);
    border-radius: 4px;
    background: transparent;
    color: var(--muted);
    cursor: pointer;
  }
  .ev .revert:hover:not(:disabled) {
    color: var(--text);
    border-color: var(--accent);
  }
  .ev .revert:disabled {
    cursor: default;
    opacity: 0.6;
  }
  .src {
    margin-left: 4px;
    font-size: 10.5px;
    border: 1px solid var(--line);
    border-radius: 4px;
    padding: 0 4px;
  }
  .st {
    display: inline-block;
    font-size: 10.5px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    color: #fff;
    background: var(--c);
    border-radius: 4px;
    padding: 0 5px;
  }
  .comment {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 10px 12px 6px;
    margin: 6px 0;
    box-shadow: 0 1px 2px rgb(0 0 0 / 0.08);
  }
  .comment.mentions-me {
    border-left: 3px solid var(--accent);
  }
  .who {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
  }
  .who :global(.avatars) {
    margin-left: 0;
  }
  .who time,
  .who span {
    font-size: 11.5px;
  }
  .text {
    font-size: 14px;
    margin: 6px 0 4px;
    overflow-wrap: anywhere;
  }
  .text :global(p) {
    margin: 0 0 6px;
  }
  .text :global(.mention),
  .activity :global(.mention) {
    color: var(--accent);
    background: color-mix(in srgb, var(--accent) 14%, transparent);
    border-radius: 4px;
    padding: 0 3px;
    font-weight: 500;
  }
  .text :global(.mention.me) {
    background: color-mix(in srgb, var(--accent) 28%, transparent);
  }
  .edit {
    margin: 6px 0;
  }
  .foot {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
    min-height: 26px;
  }
  .rx {
    border: 1px solid var(--line);
    background: var(--chip);
    border-radius: 999px;
    padding: 1px 8px;
    font-size: 12px;
  }
  .rx.on {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 16%, transparent);
  }
  .act {
    border: 0;
    background: none;
    color: var(--muted);
    font-size: 12px;
    padding: 3px 6px;
    border-radius: 5px;
    display: inline-flex;
    align-items: center;
    opacity: 0;
  }
  .comment:hover .act,
  .act:focus-visible,
  .react-wrap:has(.react-pop) .act {
    opacity: 1;
  }
  .act:hover {
    background: var(--card-hover);
    color: var(--text);
  }
  .react-wrap {
    position: relative;
  }
  .react-pop {
    position: absolute;
    bottom: calc(100% + 4px);
    left: 0;
    display: flex;
    gap: 2px;
    padding: 4px;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 999px;
    box-shadow: 0 6px 20px rgb(0 0 0 / 0.25);
    z-index: 20;
  }
  .react-pop button {
    border: 0;
    background: none;
    font-size: 17px;
    padding: 2px 4px;
    border-radius: 50%;
  }
  .react-pop button:hover {
    background: var(--card-hover);
    transform: scale(1.15);
  }
  .compose {
    padding: 10px 12px 12px;
    border-top: 1px solid var(--line);
  }
  .empty {
    text-align: center;
    margin-top: 24px;
    font-size: 13px;
  }
  @media (hover: none) {
    .act {
      opacity: 1;
    }
  }
</style>
