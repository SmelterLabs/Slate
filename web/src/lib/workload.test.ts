import { describe, expect, it } from 'vitest';
import type { Account, Capacity } from './types';
import { capacityOf } from './workload';

const alex = { id: 'a1', handle: 'alex', display_name: 'Alex', kind: 'person' } as Account;
const agent = { id: 'a2', handle: 'my-agent', display_name: 'My agent', kind: 'agent' } as Account;

describe('capacity defaults', () => {
  it('defaults people to weekdays and agents to every day', () => {
    expect(capacityOf(alex, {}).work_days).toEqual([0, 1, 2, 3, 4]);
    expect(capacityOf(agent, {}).work_days).toHaveLength(7);
    expect(capacityOf(alex, {}).hours_per_day).toBe(8);
  });

  it('uses a saved capacity when there is one', () => {
    const saved = { account_id: 'a1', custom: true, hours_per_day: 4, tasks_per_day: 2, work_days: [0] } as Capacity;
    expect(capacityOf(alex, { a1: saved })).toBe(saved);
  });
});
