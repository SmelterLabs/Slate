// Presentation evidence, not a runner queue: Slate exposes reports and presence, not receipt,
// capacity, queue position or an ETA. A passed due gate alone never means a request was sent.
import type { AgentEntry, Item } from './types';
import { workspaceDay } from './quickadd';
import { workspaceInstant, isClosed } from './views';

export const PICKUP_WAIT_MS = 2 * 60_000;
const WORK_FRESH_MS = 6 * 60 * 60_000; // same freshness window as the agent badge
export type WorkingItems = Record<string, string[]>;

/** Build once per board update, not one full-board scan per waiting card. */
export function workingItems(items: Item[], now: Date): WorkingItems {
  const out: WorkingItems = {};
  for (const i of items) {
    if (isClosed(i) || i.archived_at || i.agent?.state !== 'working') continue;
    const age = now.getTime() - Date.parse(i.agent.at);
    if (age < 0 || age >= WORK_FRESH_MS) continue;
    (out[i.agent.by] ??= []).push(i.id);
  }
  return out;
}

/** Explicit requests, or a due task with no report yet (also works on a fresh page). */
export function pickupSince(item: Item, requested: number | undefined, now: Date): number | undefined {
  if (isClosed(item) || item.archived_at || item.agent?.state === 'working') return;
  if (requested != null && Number.isFinite(requested) && (!item.agent || requested > Date.parse(item.agent.at))) return requested;
  if (item.agent || item.blocked || item.waiting_on_account_id || !item.due_at) return;
  if (item.start_at && Date.parse(item.start_at) > now.getTime()) return;
  const due = new Date(item.due_at);
  if (!Number.isFinite(due.getTime())) return;
  const at = item.due_date_only ? workspaceInstant(workspaceDay(due), 6).getTime() : due.getTime();
  if (at <= now.getTime()) {
    const created = Date.parse(item.created_at);
    return Math.max(at, Number.isFinite(created) ? created : at);
  }
}

export interface PickupStatus {
  state: 'waiting' | 'requested' | 'no-runner' | 'unclaimed';
  text: string;
  warning: boolean;
  title: string;
}

export function pickupStatus(
  item: Item, since: number, ids: string[], work: WorkingItems,
  roster: AgentEntry[], rosterAt: number | null, now: Date,
): PickupStatus {
  const entries = roster.filter((a) => ids.includes(a.account.id));
  const recentRoster = rosterAt != null && now.getTime() - rosterAt <= PICKUP_WAIT_MS;
  // `work` (the agent's other working cards) is no reason to wait: agents run several cards at
  // once, and a working badge may come from another session of the agent, not its runner.
  void work;
  // Only call a connection absent when every relevant agent was included in a fresh snapshot.
  const disconnected = recentRoster && entries.length === ids.length && ids.length > 0 && entries.every((a) => a.live_streams === 0);
  if (disconnected) return {
    state: 'no-runner', warning: true,
    text: `No live connection seen for ${entries.map((a) => a.account.display_name).join(' and ')}. Check agent status.`,
    title: 'No live stream in the latest roster check. Open Agents to check its connection; this is not proof the agent is offline.',
  };
  const late = now.getTime() - since > PICKUP_WAIT_MS;
  return {
    state: late ? 'unclaimed' : 'requested', warning: late,
    text: late ? 'Not picked up yet. Check agent status.' : 'Waiting for pickup…',
    title: 'No working report for this card. Open Agents to check the connection and current work; a live stream alone does not confirm receipt.',
  };
}
