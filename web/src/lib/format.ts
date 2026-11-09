// Display helpers. Dates use the configured workspace zone (the default until /me loads).

import { workspaceDay } from './quickadd';
import { workspaceFormatter } from './timezone';
import type { Item } from './types';

const short = workspaceFormatter('en-US', { month: 'short', day: 'numeric' });
const withYear = workspaceFormatter('en-US', {
  month: 'short',
  day: 'numeric',
  year: 'numeric',
});
const timeFmt = workspaceFormatter('en-US', {
  hour: 'numeric',
  minute: '2-digit',
});

/** "Nov 11", or "Nov 11, 2027" outside the current year. */
export function shortDate(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const sameYear = workspaceDay(d).slice(0, 4) === workspaceDay(now).slice(0, 4);
  return (sameYear ? short : withYear).format(d);
}

/** Due label for a card: "Today 5:00 PM" / "Tomorrow" / "Nov 11". */
export function dueLabel(iso: string, now: Date = new Date(), dateOnly?: boolean): string {
  const d = new Date(iso);
  const day = workspaceDay(d);
  const today = workspaceDay(now);
  const tomorrow = workspaceDay(new Date(now.getTime() + 86_400_000));
  const time = timeFmt.format(d);
  // The item's `due_date_only` when known; else 17:00 (Slate's default time) reads as no time.
  const tail = (dateOnly ?? time === '5:00 PM') ? '' : ` ${time}`;
  if (day === today) return `Today${tail}`;
  if (day === tomorrow) return `Tomorrow${tail}`;
  return shortDate(iso, now);
}

/** "just now", "2 min ago", "3 h ago", "yesterday", "Oct 3". */
export function ago(iso: string, now: Date = new Date()): string {
  const s = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 1000);
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  const days = Math.round(s / 86_400);
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  return shortDate(iso, now);
}

export function isOpen(item: Item): boolean {
  return item.status?.kind !== 'done' && item.status?.kind !== 'cancelled';
}

/** The tasks `item` waits on that are still open (on the board; one the caller cannot see is
 * left out). The server's `blocked` flag is the truth; this names them. */
export function openBlockers(item: Item, items: Record<string, Item>): Item[] {
  return (item.blocked_by ?? [])
    .map((id) => items[id])
    .filter((b): b is Item => !!b && isOpen(b) && !b.archived_at);
}

/** Tooltip for a Depends on badge: the open tasks it depends on. */
export function blockedTitle(blockers: Item[]): string {
  return blockers.length ? `Depends on: ${blockers.map((b) => b.title).join(', ')}` : 'Depends on an open task';
}

export function isOverdue(item: Item, now: Date = new Date()): boolean {
  return (
    isOpen(item) && !item.archived_at && !!item.due_at && new Date(item.due_at) < now
  );
}

/** Done items leave the board at workspace midnight after they were finished. */
export function hiddenAsFinished(item: Item, now: Date = new Date()): boolean {
  if (isOpen(item)) return false;
  const when = item.completed_at ?? item.updated_at;
  return workspaceDay(new Date(when)) < workspaceDay(now);
}

/** Date-only value for an <input type="date"> from a stored UTC instant. */
export function dateInput(iso: string | null): string {
  return iso ? workspaceDay(new Date(iso)) : '';
}

const DAY_NAMES: Record<string, string> = {
  MO: 'Monday',
  TU: 'Tuesday',
  WE: 'Wednesday',
  TH: 'Thursday',
  FR: 'Friday',
  SA: 'Saturday',
  SU: 'Sunday',
};
const UNITS: Record<string, [string, string]> = {
  DAILY: ['day', 'days'],
  WEEKLY: ['week', 'weeks'],
  MONTHLY: ['month', 'months'],
  YEARLY: ['year', 'years'],
};

function ordinal(n: number): string {
  if (n === -1) return 'last day';
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function joinWords(words: string[]): string {
  return words.length < 2 ? words.join('') : `${words.slice(0, -1).join(', ')} and ${words.at(-1)}`;
}

/** "FREQ=WEEKLY;INTERVAL=2;BYDAY=WE" -> "every 2 weeks on Wednesday". Falls back to the rule. */
export function describeRrule(rrule: string): string {
  const parts = Object.fromEntries(
    rrule
      .replace(/^RRULE:/i, '')
      .split(';')
      .map((p) => p.split('=') as [string, string]),
  );
  const unit = UNITS[parts.FREQ?.toUpperCase()];
  if (!unit) return rrule;
  const n = Number(parts.INTERVAL ?? 1) || 1;
  let text = n === 1 ? `every ${unit[0]}` : `every ${n} ${unit[1]}`;
  if (parts.BYDAY) {
    const days = parts.BYDAY.split(',').map((d: string) => {
      const m = /^([+-]?\d+)?([A-Z]{2})$/i.exec(d);
      if (!m) return d;
      const name = DAY_NAMES[m[2].toUpperCase()] ?? d;
      return m[1] ? `${m[1] === '-1' ? 'last' : ordinal(Number(m[1]))} ${name}` : name;
    });
    text += ` on ${joinWords(days)}`;
  } else if (parts.BYMONTHDAY) {
    text += ` on the ${joinWords(parts.BYMONTHDAY.split(',').map((d: string) => ordinal(Number(d))))}`;
  }
  return text;
}

/** "1h 30m" for minutes; "" for none. */
export function formatEstimate(minutes: number | null | undefined): string {
  if (!minutes) return '';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m}m`;
  return m ? `${h}h ${m}m` : `${h}h`;
}

/** Parse "90", "1.5h", "1h 30m", "45m", "2d" (8h days) into minutes; null when unreadable. */
export function parseEstimate(text: string): number | null {
  const t = text.trim().toLowerCase();
  if (!t) return null;
  if (/^\d+$/.test(t)) return Number(t);
  let total = 0;
  let any = false;
  for (const m of t.matchAll(/(\d+(?:\.\d+)?)\s*(d|h|m)/g)) {
    any = true;
    const n = Number(m[1]);
    total += m[2] === 'd' ? n * 480 : m[2] === 'h' ? n * 60 : n;
  }
  if (!any || t.replace(/(\d+(?:\.\d+)?)\s*(d|h|m)/g, '').trim()) return null;
  return Math.round(total);
}
