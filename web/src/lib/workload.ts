// Capacity, as the Box view and the capacity editor count it: how much one person or agent can
// take on in a day, in minutes or tasks, on the weekdays they work. Same rules as the server
// (service/workload.py), which answers agents. Pure; Vitest covers it.

import type { Account, Capacity } from './types';
import { addDays } from './views';

export type WorkloadMetric = 'estimate' | 'tasks';

export const DEFAULT_CAPACITY: Record<'person' | 'agent', Omit<Capacity, 'account_id'>> = {
  person: { custom: false, hours_per_day: 8, tasks_per_day: null, work_days: [0, 1, 2, 3, 4] },
  agent: { custom: false, hours_per_day: 8, tasks_per_day: null, work_days: [0, 1, 2, 3, 4, 5, 6] },
};

/** Monday-based weekday (0 = Monday), as the server and the capacity settings count. */
export function isoWeekday(ymd: string): number {
  return (new Date(`${ymd}T12:00:00Z`).getUTCDay() + 6) % 7;
}

export function mondayOf(ymd: string): string {
  return addDays(ymd, -isoWeekday(ymd));
}

export function capacityOf(account: Account, caps: Record<string, Capacity>): Capacity {
  return caps[account.id] ?? { account_id: account.id, ...DEFAULT_CAPACITY[account.kind] };
}

/** What an account can take on in one day, in the metric's unit (null = no limit). */
export function dayCapacity(cap: Capacity, ymd: string, metric: WorkloadMetric): number | null {
  const works = cap.work_days.includes(isoWeekday(ymd));
  if (metric === 'estimate') return cap.hours_per_day === null ? null : works ? cap.hours_per_day * 60 : 0;
  return cap.tasks_per_day === null ? null : works ? cap.tasks_per_day : 0;
}

/** "3h 30m", "2 tasks". */
export function fmtLoad(n: number, metric: WorkloadMetric): string {
  if (metric === 'estimate') {
    const m = Math.round(n);
    const h = Math.floor(m / 60);
    const r = m % 60;
    return h && r ? `${h}h ${r}m` : h ? `${h}h` : `${r}m`;
  }
  const v = Math.round(n * 10) / 10;
  return `${v} task${v === 1 ? '' : 's'}`;
}
