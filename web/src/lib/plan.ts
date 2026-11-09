// The agent plan (GET /agents/plan): what each agent is about to run, for the Agents page's
// "Next up" block. Pure helpers here; the page fetches and listens.

import { workspaceDay } from './quickadd';
import { workspaceFormatter } from './timezone';

export type PlanReason = 'due' | 'run_requested' | 'ask';

export interface PlanNext {
  item_id: string;
  title: string;
  list: string | null;
  due_at: string | null;
  reason: PlanReason;
  /** When it becomes takeable; null = now. */
  at: string | null;
}

export interface PlanLease {
  item_id: string;
  title: string | null;
  claimed_at: string;
  until: string;
  age_seconds: number;
}

export interface AgentPlan {
  id: string;
  handle: string;
  display_name: string;
  next: PlanNext[];
  leases: PlanLease[];
}

export interface PlanOut {
  horizon_hours: number;
  agents: AgentPlan[];
}

const timeFmt = workspaceFormatter('en-US', { hour: 'numeric', minute: '2-digit' });
const dayFmt = workspaceFormatter('en-US', { weekday: 'short' });

/** "now", "6:00 AM" (today), "Tue 6:00 AM" (another day); a past time reads "now". */
export function planWhen(at: string | null, now: Date = new Date()): string {
  if (!at) return 'now';
  const d = new Date(at);
  if (!Number.isFinite(d.getTime()) || d.getTime() <= now.getTime()) return 'now';
  const time = timeFmt.format(d);
  return workspaceDay(d) === workspaceDay(now) ? time : `${dayFmt.format(d)} ${time}`;
}

export const REASON_LABEL: Record<PlanReason, string> = {
  due: 'due',
  run_requested: 'Run now',
  ask: 'asked',
};

/** Events after which the plan is re-read (anything that moves a task's gate or an Ask). */
export function movesPlan(type: string): boolean {
  return type.startsWith('item.') || type.startsWith('comment.') || type.startsWith('account.');
}

/** "held 4 min" / "held 2 h 05 min": how long an agent has had a lease. */
export function heldFor(seconds: number): string {
  const m = Math.floor(Math.max(0, seconds) / 60);
  if (m < 1) return 'held under a minute';
  if (m < 60) return `held ${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `held ${h} h ${String(rest).padStart(2, '0')} min` : `held ${h} h`;
}
