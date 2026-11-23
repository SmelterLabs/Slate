// Presentation evidence, not a runner queue: Slate exposes reports and presence, not receipt,
// capacity, queue position or an ETA. A passed due gate alone never means a request was sent.
import type { AgentEntry, Item } from './types';
import { fullStamp, shortDate } from './format';
import { workspaceDay } from './quickadd';
import { workspaceFormatter } from './timezone';
import { workspaceInstant, isClosed } from './views';
import { addDays } from './when';

export const PICKUP_WAIT_MS = 2 * 60_000;
/** How long a `working` report counts as fresh; the agent badge dims one older than this. */
export const WORK_FRESH_MS = 6 * 60 * 60_000;
export type WorkingItems = Record<string, string[]>;

const runTimeFmt = workspaceFormatter('en-US', { hour: 'numeric', minute: '2-digit' });

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

/** When an item's due gate opens (ms): 06:00 workspace time for date-only due dates, else due_at. */
export function dueRunAt(item: Pick<Item, 'due_at' | 'due_date_only'>): number | undefined {
  if (!item.due_at) return;
  const due = new Date(item.due_at);
  if (!Number.isFinite(due.getTime())) return;
  return item.due_date_only ? workspaceInstant(workspaceDay(due), 6).getTime() : due.getTime();
}

/** Explicit requests, or a due task with no report yet (also works on a fresh page). */
export function pickupSince(item: Item, requested: number | undefined, now: Date): number | undefined {
  if (isClosed(item) || item.archived_at || item.agent?.state === 'working') return;
  if (requested != null && Number.isFinite(requested) && (!item.agent || requested > Date.parse(item.agent.at))) return requested;
  if (item.agent || item.stalled || item.blocked || item.waiting_on_account_id || item.parked_until || !item.due_at) return;
  if (item.start_at && Date.parse(item.start_at) > now.getTime()) return;
  const at = dueRunAt(item);
  if (at != null && at <= now.getTime()) {
    const created = Date.parse(item.created_at);
    return Math.max(at, Number.isFinite(created) ? created : at);
  }
}

export interface ScheduledRun {
  at: number;
  text: string;
  title: string;
}

/** Human-readable scheduled run label: "Runs at 6:00 AM", "Runs tomorrow at 6:00 AM",
 * "Runs Nov 26 at 6:00 AM". Uses calendar arithmetic for tomorrow so DST days stay exact. */
export function scheduledRunLabel(atMs: number, now: Date): string {
  const d = new Date(atMs);
  const time = runTimeFmt.format(d);
  const day = workspaceDay(d);
  const today = workspaceDay(now);
  if (day === today) return `Runs at ${time}`;
  if (day === addDays(today, 1)) return `Runs tomorrow at ${time}`;
  return `Runs ${shortDate(d.toISOString(), now)} at ${time}`;
}

/** When an open, idle push-agent card is held by a future due gate or start date, return the
 * scheduled run instant and copy; null otherwise. Mirrors `work.eligible()`'s due and start gates. */
export function scheduledRun(
  item: Item,
  requested: number | undefined,
  now: Date,
): ScheduledRun | null {
  if (
    isClosed(item) ||
    item.archived_at ||
    item.parked_until ||
    item.agent ||
    item.stalled ||
    item.blocked ||
    item.waiting_on_account_id
  ) return null;
  if (requested != null && Number.isFinite(requested)) return null;
  const agentIds = new Set(item.assignees.filter((a) => a.kind === 'agent').map((a) => a.id));
  const asked = (item.open_asks ?? []).some((id) => agentIds.has(id));
  const nowMs = now.getTime();
  let at: number | undefined;
  let reason: 'start' | 'due_day' | 'due_time' | undefined;
  if (!asked) {
    const due = dueRunAt(item);
    if (due != null && due > nowMs) {
      at = due;
      reason = item.due_date_only ? 'due_day' : 'due_time';
    }
  }
  if (item.start_at) {
    const start = Date.parse(item.start_at);
    if (Number.isFinite(start) && start > nowMs && (at == null || start > at)) {
      at = start;
      reason = 'start';
    }
  }
  if (at == null || !reason) return null;
  const stamp = fullStamp(new Date(at).toISOString());
  const title = reason === 'start'
    ? `Waiting until its start date (${stamp}). Click Run now to start immediately.`
    : reason === 'due_day'
      ? `Date-only agent cards run at 6:00 AM on their due day (${stamp}). Click Run now to start immediately.`
      : `Agent cards with a due time run at that time (${stamp}). Click Run now to start immediately.`;
  return { at, text: scheduledRunLabel(at, now), title };
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
