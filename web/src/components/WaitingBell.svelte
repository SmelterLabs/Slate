<script lang="ts">
  // The bell at the top right of a desktop (the favorites bar's end, beside Agents): how many
  // @mentions and messages are waiting, without opening a view that has the card. Its menu lists
  // what is unread (Inbox entries grouped by task, then chats with unread messages); a row opens
  // the place, like the Inbox. Phones have the top bar's bell instead.
  import { badge, chatHref } from '../lib/chat';
  import { chat } from '../lib/chat.svelte';
  import { groupEntries, inbox, REASON_LABEL } from '../lib/inbox.svelte';
  import { notifySettings } from '../lib/notify.svelte';
  import { waitingCounts } from '../lib/waiting';
  import Dropdown from './Dropdown.svelte';
  import Icon from './Icon.svelte';

  const SHOW = 8;
  let open = $state(false);

  const counts = $derived(waitingCounts(inbox.entries, chat.channels));
  const groups = $derived(groupEntries(inbox.entries.filter((e) => !e.read)).slice(0, SHOW));
  // Plain chat still unread (mentions and replies are already Inbox rows above).
  const chats = $derived(
    chat.channels.filter((c) => c.unread - c.mentions > 0 && !groups.some((g) => g.channel === c.key)),
  );
  const total = $derived(counts.tags + counts.messages + counts.other);
  const title = $derived(
    total
      ? [
          counts.tags && `${counts.tags} mention${counts.tags === 1 ? '' : 's'}`,
          counts.messages && `${counts.messages} message${counts.messages === 1 ? '' : 's'}`,
          counts.other && `${counts.other} other`,
        ]
          .filter(Boolean)
          .join(', ') + ' waiting'
      : 'Nothing waiting',
  );
</script>

<Dropdown {title} cls="waiting-bell{total ? ' has' : ''}" align="right" float bind:open>
  {#snippet label()}
    <Icon name="bell" size={17} />
    {#if counts.tags}<span class="wb-chip wb-tags" data-waiting-tags={counts.tags}>@{badge(counts.tags)}</span>{/if}
    {#if counts.messages}<span class="wb-chip wb-msgs" data-waiting-messages={counts.messages}
        ><Icon name="chat" size={12} />{badge(counts.messages)}</span
      >{/if}
    {#if !counts.tags && !counts.messages && counts.other}<span class="wb-chip">{badge(counts.other)}</span>{/if}
  {/snippet}
  <div class="menu wb-menu" role="dialog" aria-label="Waiting for you">
    {#if !groups.length && !chats.length}
      <p class="muted small wb-empty">Nothing waiting. You're caught up.</p>
    {/if}
    {#each groups as g (g.itemId ?? g.channel ?? g.docId ?? g.entries[0].event.id)}
      <button
        class="wb-row"
        onclick={() => {
          open = false;
          inbox.open(g);
        }}
      >
        <span class="wb-why" class:hot={g.entries.some((e) => e.reason === 'mention')}>{REASON_LABEL[g.entries[0].reason]}</span>
        <span class="wb-title">{g.title}</span>
        {#if g.unread > 1}<span class="count">{g.unread}</span>{/if}
      </button>
    {/each}
    {#each chats as c (c.key)}
      <a class="wb-row" href={chatHref(c.key, c.first_unread_thread_id ?? c.first_unread_id)} onclick={() => (open = false)}>
        <span class="wb-why">New messages</span>
        <span class="wb-title">{c.kind === 'agent' ? `Chat with ${c.name}` : `#${c.name}`}</span>
        <span class="count">{badge(c.unread - c.mentions)}</span>
      </a>
    {/each}
    <div class="wb-foot">
      <a href="#/inbox" onclick={() => (open = false)}>Open Inbox</a>
      {#if inbox.unread}<button class="link" onclick={() => void inbox.readAll()}>Mark all read</button>{/if}
      <button
        class="link"
        onclick={() => {
          open = false;
          notifySettings.open = true;
        }}>Notification settings</button
      >
    </div>
  </div>
</Dropdown>
