import { describe, expect, it } from 'vitest';
import type { Channel } from './chat';
import type { InboxEntry } from './inbox.svelte';
import { waitingCounts } from './waiting';

function entry(reason: InboxEntry['reason'], type = 'item.commented', read = false): InboxEntry {
  return {
    event: { id: Math.random().toString(36).slice(2), type, item_id: 'i1' } as InboxEntry['event'],
    reason,
    read,
    item_title: 'T',
    list_id: 'l',
  };
}

function channel(unread: number, mentions: number): Channel {
  return { key: 'general', kind: 'general', name: 'General', unread, mentions } as Channel;
}

describe('waitingCounts', () => {
  it('counts unread mentions as tags and comments as messages', () => {
    const c = waitingCounts([entry('mention'), entry('comment'), entry('assigned'), entry('mention', 'item.mentioned', true)], []);
    expect(c).toEqual({ tags: 1, messages: 1, other: 1 });
  });

  it('counts a chat mention once, as a tag, and plain chat as messages', () => {
    const c = waitingCounts([entry('mention', 'message.posted'), entry('reply', 'message.posted')], [channel(4, 1)]);
    // 4 unread in the channel, one of them the mention: 3 messages (the reply among them).
    expect(c).toEqual({ tags: 1, messages: 3, other: 0 });
  });
});
