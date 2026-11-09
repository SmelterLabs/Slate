import { describe, expect, it } from 'vitest';
import { ev, item } from './fixtures';
import { blockedTitle, openBlockers } from './format';
import { verb, type HistoryContext } from './history';
import { myWork } from './mywork';
import { applyEvent, type BoardData } from './reducer';

const OPEN = { id: 's1', name: 'To do', kind: 'open' as const, color: null };
const DONE = { id: 's3', name: 'Done', kind: 'done' as const, color: null };
const ME = { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' as const };

describe('blocked', () => {
  it('names only the blockers that are still open', () => {
    const a = item({ id: 'a', title: 'Design', status: OPEN });
    const b = item({ id: 'b', title: 'Spec', status: DONE });
    const c = item({ id: 'c', blocked_by: ['a', 'b', 'gone'], blocked: true });
    const open = openBlockers(c, { a, b, c });
    expect(open.map((x) => x.id)).toEqual(['a']);
    expect(blockedTitle(open)).toBe('Depends on: Design');
    expect(blockedTitle([])).toBe('Depends on an open task');
  });

  it('item.blocked and item.unblocked flip the row with no refetch', () => {
    const d: BoardData = { items: { I1: item({ id: 'I1', blocked: true }) } } as unknown as BoardData;
    const fx = applyEvent(d, ev({ type: 'item.unblocked', after: { cleared_by: ['x'], open: true } }));
    expect(d.items.I1.blocked).toBe(false);
    expect(fx.refetch).toEqual([]);
    applyEvent(d, ev({ type: 'item.blocked', after: { blocked_by: ['x'] } }));
    expect(d.items.I1.blocked).toBe(true);
  });

  it('history says what holds it up and what freed it', () => {
    const ctx: HistoryContext = { name: (id) => id, status: (s) => s, title: (id) => ({ x: 'Design' })[id] };
    expect(verb(ev({ type: 'item.blocked', after: { blocked_by: ['x'] } }), ctx)).toBe(
      'made this depend on “Design”',
    );
    expect(verb(ev({ type: 'item.unblocked', after: { cleared_by: ['x'] } }), ctx)).toBe(
      'freed this: it no longer depends on “Design”',
    );
    expect(verb(ev({ type: 'item.unblocked', after: { cleared_by: [] } }), ctx)).toBe(
      'freed this: it no longer depends on an open task',
    );
  });

  it('My Work lists my open blocked tasks', () => {
    const items = [
      item({ id: 'a', assignees: [ME], status: OPEN, blocked: true }),
      item({ id: 'b', assignees: [ME], status: OPEN }),
      item({ id: 'c', assignees: [ME], status: DONE, blocked: true }),
    ];
    const w = myWork(items, 'me', new Date('2026-10-22T20:00:00Z'));
    expect(w.blocked.map((i) => i.id)).toEqual(['a']);
    expect(w.buckets.none.map((i) => i.id)).toEqual(['a', 'b']);
  });
});
