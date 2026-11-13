// Capacity, as the capacity editor shows it: how much one person or agent can take on in a day,
// in hours or tasks, on the weekdays they work. The defaults match the server
// (service/workload.py), which answers agents. Pure; Vitest covers it.

import type { Account, Capacity } from './types';

const DEFAULT_CAPACITY: Record<'person' | 'agent', Omit<Capacity, 'account_id'>> = {
  person: { custom: false, hours_per_day: 8, tasks_per_day: null, work_days: [0, 1, 2, 3, 4] },
  agent: { custom: false, hours_per_day: 8, tasks_per_day: null, work_days: [0, 1, 2, 3, 4, 5, 6] },
};

export function capacityOf(account: Account, caps: Record<string, Capacity>): Capacity {
  return caps[account.id] ?? { account_id: account.id, ...DEFAULT_CAPACITY[account.kind] };
}
