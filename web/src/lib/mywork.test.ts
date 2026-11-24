import { describe, expect, it } from 'vitest';
import { item } from './fixtures';
import { isOverdue } from './format';
import { bucketOf, myWork } from './mywork';

// Thursday 2026-10-22, 15:00 in Chicago.
const NOW = new Date('2026-10-22T20:00:00Z');
const ME = { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' as const };
const OTHER = { id: 'sc', handle: 'agent', display_name: 'Agent', kind: 'agent' as const };
const OPEN = { id: 's1', name: 'To do', kind: 'open' as const, color: null };
const DONE = { id: 's3', name: 'Done', kind: 'done' as const, color: null };

describe('bucketOf', () => {
  it.each([
    [null, 'none'],
    ['2026-10-22T19:00:00Z', 'overdue'],
    ['2026-10-22T22:00:00Z', 'today'],
    ['2026-10-23T22:00:00Z', 'tomorrow'],
    ['2026-10-29T22:00:00Z', 'week'],
    ['2026-10-30T22:00:00Z', 'later'],
  ])('%s -> %s', (due, want) => {
    expect(bucketOf(item({ due_at: due }), NOW)).toBe(want);
  });

  it('keeps date-only cards in today after 5 pm until midnight', () => {
    const evening = new Date('2026-10-23T01:00:00Z'); // Thu 20:00 CDT
    const midnight = new Date('2026-10-23T05:00:00Z'); // Fri 00:00 CDT
    const dateOnly = item({ due_at: '2026-10-22T22:00:00Z', due_date_only: true });
    expect(bucketOf(dateOnly, evening)).toBe('today');
    expect(bucketOf(dateOnly, midnight)).toBe('overdue');
  });
});

describe('myWork', () => {
  it('sorts my open work into buckets and collects the rest', () => {
    const items = [
      item({ id: 'a', assignees: [ME], status: OPEN, due_at: '2026-10-22T22:00:00Z' }),
      item({ id: 'b', assignees: [ME], status: OPEN, due_at: '2026-10-21T22:00:00Z' }),
      item({ id: 'c', assignees: [ME], status: OPEN }),
      item({ id: 'd', assignees: [OTHER], status: OPEN, created_by: 'me' }),
      item({ id: 'e', assignees: [ME], status: DONE, completed_at: '2026-10-21T10:00:00Z' }),
      item({ id: 'f', assignees: [ME], status: DONE, completed_at: '2026-10-01T10:00:00Z' }),
      item({ id: 'g', assignees: [OTHER], status: OPEN, waiting_on_account_id: 'me' }),
      item({ id: 'h', assignees: [ME], status: OPEN, archived_at: '2026-10-20T00:00:00Z' }),
    ];
    const w = myWork(items, 'me', NOW);
    expect(w.buckets.today.map((i) => i.id)).toEqual(['a']);
    expect(w.buckets.overdue.map((i) => i.id)).toEqual(['b']);
    expect(w.buckets.none.map((i) => i.id)).toEqual(['c']);
    expect(w.delegated.map((i) => i.id)).toEqual(['d']);
    expect(w.doneRecently.map((i) => i.id)).toEqual(['e']);
    expect(w.waitingOnMe.map((i) => i.id)).toEqual(['g']);
    expect(w.openCount).toBe(3);
  });
  it('reference list cards are not my work', () => {
    const items = [
      item({ id: 'a', list_id: 'L2', assignees: [ME], status: OPEN, due_at: '2026-10-22T22:00:00Z' }),
      item({ id: 'g', list_id: 'L2', assignees: [OTHER], status: OPEN, waiting_on_account_id: 'me' }),
      item({ id: 'n', assignees: [ME], status: OPEN }),
    ];
    const w = myWork(items, 'me', NOW, new Set(['L2']));
    expect(w.buckets.today).toEqual([]);
    expect(w.waitingOnMe).toEqual([]);
    expect(w.buckets.none.map((i) => i.id)).toEqual(['n']);
    expect(w.openCount).toBe(1);
  });

  it('excludes later, parked, and reference-list cards from My Work and never marks parked cards overdue', () => {
    const parked = item({
      id: 'p',
      assignees: [ME],
      status: OPEN,
      due_at: '2026-10-01T22:00:00Z',
      parked_until: '2026-11-15',
      parked_note: 'Vendor reply',
    });
    const later = item({ id: 'l', assignees: [ME], status: OPEN, due_at: '2026-12-01T22:00:00Z', later: true });
    const refCard = item({ id: 'r', list_id: 'L_REF', assignees: [ME], status: OPEN, due_at: '2026-10-22T22:00:00Z' });
    const normal = item({ id: 'n', assignees: [ME], status: OPEN, due_at: '2026-10-22T22:00:00Z' });

    expect(isOverdue(parked, NOW)).toBe(false);
    const w = myWork([parked, later, refCard, normal], 'me', NOW, new Set(['L_REF']));
    expect(w.buckets.today.map((i) => i.id)).toEqual(['n']);
    expect(w.buckets.overdue).toEqual([]);
    expect(w.buckets.later).toEqual([]);
    expect(w.openCount).toBe(1);
  });
});
