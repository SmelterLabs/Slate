import { describe, expect, it, vi } from 'vitest';

vi.mock('./store.svelte', () => ({ board: {} }));
vi.mock('./api', () => ({ get: vi.fn(), post: vi.fn() }));

import { ev } from './fixtures';
import { describeEntry, groupEntries, linkEscalations, type InboxEntry } from './inbox.svelte';

const entry = (id: string, item: string | null, read = false): InboxEntry => ({
  event: ev({ id, item_id: item, at: `2026-10-23T1${id}:00:00Z` }),
  reason: 'comment',
  read,
  item_title: item ? `Item ${item}` : null,
  list_id: 'L1',
});

describe('groupEntries', () => {
  it('groups by item in first-seen (newest) order and counts unread', () => {
    const g = groupEntries([entry('3', 'a'), entry('2', 'b', true), entry('1', 'a', true)]);
    expect(g.map((x) => [x.itemId, x.entries.length, x.unread])).toEqual([
      ['a', 2, 1],
      ['b', 1, 0],
    ]);
    expect(g[0].latest).toBe('2026-10-23T13:00:00Z');
  });

  it('keeps item-less entries apart', () => {
    const g = groupEntries([entry('1', null), entry('2', null)]);
    expect(g).toHaveLength(2);
    expect(g[0].title).toBe('A deleted item');
  });

  it('shows an escalation as the original entry and keeps its card destination', () => {
    const original = entry('1', 'a');
    const escalated: InboxEntry = {
      ...entry('2', null),
      event: ev({ id: '2', item_id: null, type: 'inbox.escalated', after: { event_id: '1', reason: 'comment', minutes: 120 } }),
    };
    const [group] = groupEntries(linkEscalations([escalated, original]));
    expect(group.itemId).toBe('a');
    expect(group.title).toBe('Item a');
    expect(group.entries.map((e) => e.event.id)).toEqual(['2', '1']);
    const ctx = { name: () => 'Alex', title: () => undefined, status: () => undefined, now: new Date('2026-10-23T12:30:00Z') };
    expect(describeEntry(group.entries[0], ctx)).toBe(`Still waiting: ${describeEntry(group.entries[1], ctx)}`);
  });
});
