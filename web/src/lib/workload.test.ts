import { describe, expect, it } from 'vitest';
import type { Account, Capacity } from './types';
import { capacityOf, dayCapacity, fmtLoad, isoWeekday, mondayOf } from './workload';

const alex = { id: 'a1', handle: 'alex', display_name: 'Alex', kind: 'person' } as Account;
const agent = { id: 'a2', handle: 'agent', display_name: 'Agent', kind: 'agent' } as Account;

describe('capacity helpers', () => {
  it('counts weeks from Monday', () => {
    expect(isoWeekday('2026-10-19')).toBe(0);
    expect(isoWeekday('2026-10-25')).toBe(6);
    expect(mondayOf('2026-10-24')).toBe('2026-10-19');
  });

  it('defaults people to weekdays and agents to every day', () => {
    expect(capacityOf(alex, {}).work_days).toEqual([0, 1, 2, 3, 4]);
    expect(capacityOf(agent, {}).work_days).toHaveLength(7);
    const cap = capacityOf(alex, {});
    expect(dayCapacity(cap, '2026-10-21', 'estimate')).toBe(480);
    expect(dayCapacity(cap, '2026-10-24', 'estimate')).toBe(0);
    expect(dayCapacity(cap, '2026-10-21', 'tasks')).toBeNull();
    const two = { ...cap, tasks_per_day: 2 } as Capacity;
    expect(dayCapacity(two, '2026-10-21', 'tasks')).toBe(2);
  });

  it('formats loads', () => {
    expect(fmtLoad(150, 'estimate')).toBe('2h 30m');
    expect(fmtLoad(60, 'estimate')).toBe('1h');
    expect(fmtLoad(2, 'tasks')).toBe('2 tasks');
  });
});
