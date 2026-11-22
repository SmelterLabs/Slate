import { beforeEach, describe, expect, it, vi } from 'vitest';

const { open, post } = vi.hoisted(() => ({ open: vi.fn(), post: vi.fn() }));
vi.mock('./store.svelte', () => ({ board: { open } }));
vi.mock('./api', () => ({ get: vi.fn(), post }));

import { ev } from './fixtures';
import { describeEntry, groupEntries, inbox, linkEscalations, type InboxEntry } from './inbox.svelte';

const entry = (id: string, item: string | null, read = false): InboxEntry => ({
  event: ev({ id, item_id: item, at: `2026-10-23T1${id}:00:00Z` }),
  reason: 'comment',
  read,
  item_title: item ? `Item ${item}` : null,
  list_id: 'L1',
});

const escalation = (item: string | null): InboxEntry => ({
  ...entry('2', item),
  event: ev({ id: '2', item_id: item, type: 'inbox.escalated', after: { event_id: '1', reason: 'waiting', minutes: 120 } }),
  reason: 'waiting',
});
const ctx = { name: () => 'Alex', title: () => undefined, status: () => undefined, now: new Date('2026-10-23T12:30:00Z') };

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
    const [group] = groupEntries(linkEscalations([escalation(null), original]));
    expect(group.itemId).toBe('a');
    expect(group.title).toBe('Item a');
    expect(group.entries.map((e) => e.event.id)).toEqual(['2', '1']);
    expect(describeEntry(group.entries[0], ctx)).toBe(`Still waiting: ${describeEntry(group.entries[1], ctx)}`);
  });

  it('uses the escalation’s own card and reason when the original is off the page', () => {
    const [group] = groupEntries(linkEscalations([escalation('a')]));
    expect(group).toMatchObject({ itemId: 'a', title: 'Item a', unread: 1 });
    expect(group.entries[0].originalEvent).toBeUndefined();
    expect(describeEntry(group.entries[0], ctx)).toBe('Still waiting: Waiting on you');
  });
});

describe('marking an escalated card group', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    inbox.entries = linkEscalations([escalation(null), entry('1', 'a'), entry('3', 'b')]);
    inbox.unread = 3;
  });

  it('reads the original and escalation together', async () => {
    await inbox.readItem('a');
    expect(post.mock.calls).toEqual([['/inbox/read', { event_ids: ['2', '1'] }]]);
    expect(inbox.entries.map((e) => e.read)).toEqual([true, true, false]);
    expect(inbox.unread).toBe(1);
  });

  it('clears the whole card group in one request even from a filtered tab', async () => {
    const [visible] = groupEntries([inbox.entries[0]]);
    await inbox.clearGroup(visible);
    expect(post.mock.calls).toEqual([['/inbox/clear', { event_ids: ['2', '1'] }]]);
    expect(inbox.entries.map((e) => e.event.id)).toEqual(['3']);
  });

  it('opens the original card and marks the group only once', async () => {
    const [group] = groupEntries(inbox.entries);
    inbox.open(group);
    // The app also marks entries when the task window opens.
    await inbox.readItem('a');
    expect(open.mock.calls).toEqual([['a']]);
    expect(post.mock.calls).toEqual([['/inbox/read', { event_ids: ['2', '1'] }]]);
  });

  it('marks ordinary cards by item id', async () => {
    await inbox.readItem('b');
    expect(post.mock.calls).toEqual([['/inbox/read', { item_id: 'b' }]]);
  });
});
