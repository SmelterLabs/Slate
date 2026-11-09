// The Box view (layout 'box'), ClickUp-style: one box per person and agent, plus one for tasks
// nobody has, each with what is open, in progress, overdue and recently done, and the open load
// against this week's capacity. Same numbers as the server's GET /workload/boxes, which agents
// read; here they come from the tasks the view shows (its filters apply), except "done", which
// counts the scope's tasks finished in the last 7 days (a view usually hides finished work).
// Pure; Vitest covers it.

import { isOverdue } from './format';
import type { Account, Capacity, Item } from './types';
import { addDays } from './views';
import { capacityOf, dayCapacity, mondayOf } from './workload';

export const DONE_DAYS = 7;

export interface Box {
  /** null: the box for tasks with no assignee. */
  account: Account | null;
  key: string;
  /** Open tasks, in progress first, then by due date (none last), then position. */
  items: Item[];
  open: number;
  active: number;
  overdue: number;
  done: number;
  minutes: number;
  /** This week's capacity (Monday to Sunday); null = no limit or no account. */
  capacityMinutes: number | null;
  over: boolean;
}

const live = (i: Item) => i.kind === 'task' && !i.trashed_at;
const kindOf = (i: Item) => i.status?.kind ?? 'open';

function order(a: Item, b: Item): number {
  const ka = kindOf(a) === 'active' ? 0 : 1;
  const kb = kindOf(b) === 'active' ? 0 : 1;
  if (ka !== kb) return ka - kb;
  if (a.due_at !== b.due_at) return !a.due_at ? 1 : !b.due_at ? -1 : a.due_at < b.due_at ? -1 : 1;
  return a.position - b.position;
}

function weekCapacity(cap: Capacity, today: string): number | null {
  let total = 0;
  const monday = mondayOf(today);
  for (let n = 0; n < 7; n++) {
    const c = dayCapacity(cap, addDays(monday, n), 'estimate');
    if (c === null) return null;
    total += c;
  }
  return Math.round(total * 100) / 100;
}

export function buildBoxes(opts: {
  /** The tasks the view shows. */
  items: Iterable<Item>;
  /** Every task in the view's scope, for the done count. */
  scoped: Iterable<Item>;
  accounts: Account[];
  capacities: Record<string, Capacity>;
  now: Date;
  today: string;
}): Box[] {
  const { now, today } = opts;
  const since = now.getTime() - DONE_DAYS * 86_400_000;
  const boxes = new Map<string, Box>();
  const make = (account: Account | null): Box => {
    const cap = account ? capacityOf(account, opts.capacities) : null;
    return {
      account,
      key: account?.id ?? 'none',
      items: [],
      open: 0,
      active: 0,
      overdue: 0,
      done: 0,
      minutes: 0,
      capacityMinutes: cap ? weekCapacity(cap, today) : null,
      over: false,
    };
  };
  for (const a of opts.accounts) boxes.set(a.id, make(a));
  boxes.set('none', make(null));
  const owners = (i: Item) => (i.assignees.length ? i.assignees.map((a) => a.id) : ['none']);

  const seen = new Set<string>();
  for (const item of opts.items) {
    if (seen.has(item.id) || !live(item) || item.archived_at) continue;
    seen.add(item.id);
    const kind = kindOf(item);
    if (kind === 'done' || kind === 'cancelled') continue;
    for (const who of owners(item)) {
      const box = boxes.get(who);
      if (!box) continue;
      box.items.push(item);
      if (kind === 'active') box.active += 1;
      else box.open += 1;
      if (isOverdue(item, now)) box.overdue += 1;
      box.minutes += item.estimate_minutes ?? 0;
    }
  }
  for (const item of opts.scoped) {
    if (!live(item) || item.archived_at || kindOf(item) !== 'done' || !item.completed_at) continue;
    if (Date.parse(item.completed_at) < since) continue;
    for (const who of owners(item)) {
      const box = boxes.get(who);
      if (box) box.done += 1;
    }
  }
  for (const box of boxes.values()) {
    box.items.sort(order);
    box.over = box.capacityMinutes !== null && box.minutes > box.capacityMinutes;
  }
  return [...boxes.values()];
}

/** How far through their open work a box is: done / (done + open + in progress), 0..1. */
export function doneShare(box: Pick<Box, 'open' | 'active' | 'done'>): number {
  const all = box.open + box.active + box.done;
  return all ? box.done / all : 0;
}

/** The assignee handles after moving a task from one box to another. */
export function reassigned(item: Item, from: string, to: Account | null): string[] {
  const keep = item.assignees.filter((a) => a.id !== from && a.id !== to?.id).map((a) => a.handle);
  return to ? [...keep, to.handle] : keep;
}
