// The drawer's date pickers: a stored UTC instant <-> a workspace date plus an optional time, and
// the quick picks. Pure; `now` is passed in. Slate reads `YYYY-MM-DD` as 17:00 workspace and a
// naive `YYYY-MM-DDTHH:MM` as workspace wall time (TECHNICAL-GUIDE "The date rule").

import { workspaceDay } from './quickadd';
import { workspaceFormatter } from './timezone';

/** The default due time: a date with no time means 17:00 workspace. */
export const DEFAULT_TIME = '17:00';

const hm = workspaceFormatter('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

export function addDays(ymd: string, days: number): string {
  const d = new Date(`${ymd}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** A stored instant as the workspace date and time; `time` is '' for a date-only value: the item's
 * `due_date_only` when given, else (start dates, which have no flag) when it is the 17:00 default. */
export function splitWhen(iso: string | null, dateOnly?: boolean): { date: string; time: string } {
  if (!iso) return { date: '', time: '' };
  const at = new Date(iso);
  const time = hm.format(at);
  const bare = dateOnly ?? time === DEFAULT_TIME;
  return { date: workspaceDay(at), time: bare ? '' : time };
}

/** What to PATCH: null clears, a bare date means 17:00, a date and time is workspace wall time. */
export function joinWhen(date: string, time: string): string | null {
  if (!date) return null;
  return time ? `${date}T${time}` : date;
}

export interface QuickPick {
  label: string;
  date: string;
}

/** Today, Tomorrow, next Monday, and one week on from the current date (or from today). */
export function quickPicks(now: Date, current: string | null = null): QuickPick[] {
  const today = workspaceDay(now);
  const weekday = new Date(`${today}T12:00:00Z`).getUTCDay(); // 0 = Sunday
  const toMonday = ((8 - weekday) % 7) || 7; // strictly after today
  const from = current ? splitWhen(current).date : today;
  return [
    { label: 'Today', date: today },
    { label: 'Tomorrow', date: addDays(today, 1) },
    { label: 'Next Mon', date: addDays(today, toMonday) },
    { label: '+1 week', date: addDays(from, 7) },
  ];
}

const label = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC',
  weekday: 'short',
  month: 'short',
  day: 'numeric',
});

/** "Wed, Oct 28" for a YYYY-MM-DD, independent of the viewer's zone. */
export function dayLabel(ymd: string): string {
  return label.format(new Date(`${ymd}T12:00:00Z`));
}

/** "5:30 PM" for "17:30". */
export function timeLabel(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}
