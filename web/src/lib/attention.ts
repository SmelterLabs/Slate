// "Needs you": the open cards that cannot move until the viewer acts. Pure; the store feeds it
// plain items. Deliberately narrow so the count stays worth trusting:
//   waiting  - someone (usually an agent reporting blocked) is waiting on me
//   asked    - an open Ask (assigned comment) is addressed to me
//   blocked  - an agent on my card reported blocked without naming anyone
//   review   - an agent on my card reported "Ready for review"
// Not counted: agents still working, dependency blocks (they clear themselves), overdue dates
// (the Overdue column and Today count already say so), and anything waiting on someone else.

import { isOpen } from './format';
import type { Account, Item } from './types';
import { AGENT_BLOCKED, READY_FOR_REVIEW, waitingOn } from './words';

export type Need = 'waiting' | 'asked' | 'blocked' | 'review';

/** Most urgent first: this is also the sort order. The words are lib/words.ts's; `waiting`
 * names the viewer ("Waiting on Alex") through `needLabel`. */
export const NEEDS: { key: Need; label: string; short: string }[] = [
  { key: 'waiting', label: 'Waiting on you', short: 'Waiting' },
  { key: 'blocked', label: AGENT_BLOCKED, short: 'Blocked' },
  { key: 'asked', label: 'Asked of you', short: 'Asked' },
  { key: 'review', label: READY_FOR_REVIEW, short: 'Review' },
];

/** The group's words for the viewer: "Waiting on Alex", "Agent blocked", ... */
export function needLabel(need: Need, myName: string | null | undefined): string {
  return need === 'waiting' ? waitingOn(myName) : NEEDS.find((n) => n.key === need)!.label;
}

export interface Attention {
  item: Item;
  need: Need;
}

const RANK: Record<Need, number> = { waiting: 0, blocked: 1, asked: 2, review: 3 };

/** Why this item needs `me`, or null. */
export function needOf(item: Item, me: string, accounts: Record<string, Account>): Need | null {
  if (item.archived_at || item.trashed_at || !isOpen(item)) return null;
  if (item.waiting_on_account_id === me) return 'waiting';
  // On my card: I filed it, I'm on it, or an agent filed it for nobody in particular.
  const mine =
    item.created_by === me ||
    item.assignees.some((a) => a.id === me) ||
    (item.created_by ? accounts[item.created_by]?.kind : undefined) !== 'person';
  const flag = item.agent;
  if (flag?.state === 'blocked' && !item.waiting_on_account_id && mine) return 'blocked';
  if (item.open_asks?.includes(me)) return 'asked';
  if (flag?.state === 'review' && mine) return 'review';
  return null;
}

/** Everything that needs `me`, most urgent first, then oldest flag first. */
export function attention(items: Item[], me: string, accounts: Record<string, Account>): Attention[] {
  if (!me) return [];
  const out: Attention[] = [];
  for (const item of items) {
    const need = needOf(item, me, accounts);
    if (need) out.push({ item, need });
  }
  const at = (a: Attention) => a.item.agent?.at ?? a.item.updated_at;
  return out.sort((a, b) => RANK[a.need] - RANK[b.need] || at(a).localeCompare(at(b)));
}

export function countBy(list: Attention[]): Record<Need, number> {
  const n: Record<Need, number> = { waiting: 0, blocked: 0, asked: 0, review: 0 };
  for (const a of list) n[a.need]++;
  return n;
}


/** Per list: how many cards need the viewer and the most urgent reason (the sidebar marker). */
export function byList(list: Attention[]): Map<string, { n: number; top: Need }> {
  const out = new Map<string, { n: number; top: Need }>();
  for (const a of list) {
    const cur = out.get(a.item.list_id);
    if (cur) cur.n++;
    else out.set(a.item.list_id, { n: 1, top: a.need }); // `list` is most urgent first
  }
  return out;
}
