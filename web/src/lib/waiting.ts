// What the top-right bell (WaitingBell.svelte) counts: unread @mentions/Asks and unread
// messages (replies and comments in the Inbox, plus plain chat still unread). A chat mention
// is counted once, as a tag: the channel's own mention count is taken out of its unread.

import type { Channel } from './chat';
import type { InboxEntry } from './inbox.svelte';

export interface Waiting {
  tags: number;
  messages: number;
  /** Other unread Inbox entries (assigned, due, waiting...). */
  other: number;
}

export function waitingCounts(entries: InboxEntry[], channels: Channel[]): Waiting {
  let tags = 0;
  let messages = 0;
  let other = 0;
  for (const e of entries) {
    if (e.read) continue;
    if (e.reason === 'mention') tags++;
    else if (e.reason === 'reply' || e.reason === 'comment') {
      // A chat reply is already in its channel's unread count below.
      if (!e.event.type.startsWith('message.')) messages++;
    } else other++;
  }
  for (const c of channels) messages += Math.max(0, c.unread - c.mentions);
  return { tags, messages, other };
}
