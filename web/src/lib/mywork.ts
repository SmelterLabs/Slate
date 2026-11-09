// My Work (the Home page): the caller's own open work in due-date buckets, plus what they are
// waiting on someone for and what they finished lately. Pure; the page feeds it plain items.

import { workspaceDay } from './quickadd';
import { isOpen } from './format';
import type { Item } from './types';

export type Bucket = 'overdue' | 'today' | 'tomorrow' | 'week' | 'later' | 'none';

export const BUCKETS: { key: Bucket; label: string }[] = [
  { key: 'overdue', label: 'Overdue' },
  { key: 'today', label: 'Today' },
  { key: 'tomorrow', label: 'Tomorrow' },
  { key: 'week', label: 'Next 7 days' },
  { key: 'later', label: 'Later' },
  { key: 'none', label: 'No due date' },
];

export interface MyWork {
  buckets: Record<Bucket, Item[]>;
  /** Items someone else marked waiting on me. */
  waitingOnMe: Item[];
  /** Open items with an Ask (assigned comment) addressed to me: "Asked of you". */
  askedOfMe: Item[];
  /** My open items that wait on an open task (a dependency): not startable yet. They stay in
   * their due buckets too, badged. */
  blocked: Item[];
  /** Done in the last 7 days. */
  doneRecently: Item[];
  /** Open items I created that are assigned only to others (delegated). */
  delegated: Item[];
  openCount: number;
}

function addDays(ymd: string, n: number): string {
  const d = new Date(`${ymd}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function bucketOf(item: Item, now: Date): Bucket {
  if (!item.due_at) return 'none';
  const due = new Date(item.due_at);
  if (due < now) return 'overdue';
  const today = workspaceDay(now);
  const day = workspaceDay(due);
  if (day === today) return 'today';
  if (day === addDays(today, 1)) return 'tomorrow';
  if (day <= addDays(today, 7)) return 'week';
  return 'later';
}

const byDue = (a: Item, b: Item) =>
  (a.due_at ?? '9999').localeCompare(b.due_at ?? '9999') || a.title.localeCompare(b.title);

export function myWork(items: Item[], meId: string, now: Date): MyWork {
  const buckets = Object.fromEntries(BUCKETS.map((b) => [b.key, [] as Item[]])) as Record<Bucket, Item[]>;
  const waitingOnMe: Item[] = [];
  const blocked: Item[] = [];
  const doneRecently: Item[] = [];
  const delegated: Item[] = [];
  const askedOfMe: Item[] = [];
  const weekAgo = now.getTime() - 7 * 86_400_000;
  let openCount = 0;
  for (const it of items) {
    if (it.archived_at) continue;
    const mine = it.assignees.some((a) => a.id === meId);
    if (isOpen(it)) {
      if (mine) {
        buckets[bucketOf(it, now)].push(it);
        openCount++;
        if (it.blocked) blocked.push(it);
      } else if (it.created_by === meId && it.assignees.length) {
        delegated.push(it);
      }
      if (it.waiting_on_account_id === meId) waitingOnMe.push(it);
      if (it.open_asks?.includes(meId)) askedOfMe.push(it);
    } else if (mine && it.completed_at && new Date(it.completed_at).getTime() >= weekAgo) {
      doneRecently.push(it);
    }
  }
  for (const b of BUCKETS) buckets[b.key].sort(byDue);
  waitingOnMe.sort(byDue);
  blocked.sort(byDue);
  delegated.sort(byDue);
  doneRecently.sort((a, b) => (b.completed_at ?? '').localeCompare(a.completed_at ?? ''));
  askedOfMe.sort(byDue);
  return {
    buckets,
    waitingOnMe,
    askedOfMe,
    blocked,
    doneRecently: doneRecently.slice(0, 20),
    delegated,
    openCount,
  };
}
