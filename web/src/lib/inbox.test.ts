import { describe, expect, it, vi } from 'vitest';

vi.mock('./store.svelte', () => ({ board: {} }));
vi.mock('./api', () => ({ get: vi.fn(), post: vi.fn() }));

import { ev } from './fixtures';
import { groupEntries, type InboxEntry } from './inbox.svelte';

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
});
