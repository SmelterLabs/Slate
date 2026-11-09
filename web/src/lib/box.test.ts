import { describe, expect, it } from 'vitest';
import { buildBoxes, doneShare, reassigned } from './box';
import type { Account, Item } from './types';

const alex = { id: 'a1', handle: 'alex', display_name: 'Alex', kind: 'person' } as Account;
const agent = { id: 'a2', handle: 'agent', display_name: 'Agent', kind: 'agent' } as Account;
const kind = (k: string) => ({ kind: k }) as Item['status'];

const item = (id: string, extra: Partial<Item> = {}) =>
  ({
    id,
    kind: 'task',
    list_id: 'l1',
    status: kind('open'),
    due_at: null,
    position: 0,
    archived_at: null,
    trashed_at: null,
    completed_at: null,
    estimate_minutes: null,
    assignees: [alex],
    title: id,
    ...extra,
  }) as unknown as Item;

// Wednesday 2026-10-21, noon in Chicago.
const now = new Date('2026-10-21T17:00:00Z');
const opts = { accounts: [alex, agent], capacities: {}, now, today: '2026-10-21' };

describe('buildBoxes', () => {
  const items = [
    item('late', { due_at: '2026-10-20T22:00:00Z', estimate_minutes: 60 }),
    item('doing', { status: kind('active'), assignees: [alex, agent], estimate_minutes: 30, position: 5 }),
    item('orphan', { assignees: [] }),
    item('dropped', { status: kind('cancelled'), assignees: [agent] }),
    item('gone', { trashed_at: '2026-10-20T00:00:00Z' }),
  ];
  const scoped = [
    ...items,
    item('shipped', { status: kind('done'), assignees: [agent], completed_at: '2026-10-20T10:00:00Z' }),
    item('old', { status: kind('done'), assignees: [agent], completed_at: '2026-10-01T10:00:00Z' }),
  ];

  it('counts per assignee, with a box for nobody', () => {
    const [mine, agent, nobody] = buildBoxes({ ...opts, items, scoped });
    expect(mine.items.map((i) => i.id)).toEqual(['doing', 'late']);
    expect([mine.open, mine.active, mine.overdue, mine.minutes]).toEqual([1, 1, 1, 90]);
    expect(mine.capacityMinutes).toBe(5 * 480);
    expect([agent.open, agent.active, agent.done]).toEqual([0, 1, 1]);
    expect(agent.capacityMinutes).toBe(7 * 480);
    expect(nobody.account).toBeNull();
    expect(nobody.items.map((i) => i.id)).toEqual(['orphan']);
    expect(nobody.capacityMinutes).toBeNull();
  });

  it('flags a box over this week', () => {
    const caps = { a2: { account_id: 'a2', custom: true, hours_per_day: 0.05, tasks_per_day: null, work_days: [0, 1, 2, 3, 4] } };
    const boxes = buildBoxes({ ...opts, capacities: caps, items, scoped });
    expect(boxes[1].over).toBe(true);
    expect(boxes[0].over).toBe(false);
  });

  it('shares and reassigns', () => {
    expect(doneShare({ open: 1, active: 1, done: 2 })).toBe(0.5);
    expect(doneShare({ open: 0, active: 0, done: 0 })).toBe(0);
    const both = item('x', { assignees: [alex, agent] });
    expect(reassigned(both, 'a1', agent)).toEqual(['agent']);
    expect(reassigned(item('y'), 'a1', null)).toEqual([]);
    expect(reassigned(item('z', { assignees: [] }), 'none', agent)).toEqual(['agent']);
  });
});
