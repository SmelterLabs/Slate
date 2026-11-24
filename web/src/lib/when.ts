// The drawer's date pickers: a stored UTC instant <-> a workspace date plus an optional time, and
// the quick picks. Pure; `now` is passed in. Slate reads `YYYY-MM-DD` as 17:00 workspace and a
// naive `YYYY-MM-DDTHH:MM` as workspace wall time (ARCHITECTURE "The date rule").

import { workspaceDay } from './quickadd';
import { workspaceFormatter } from './timezone';

/** The default due time: a date with no time means 17:00 workspace. */
export const DEFAULT_TIME = '17:00';

const hm = workspaceFormatter('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

/** 0 = Sunday … 6 = Saturday, for a YYYY-MM-DD calendar date. */
export function weekday(ymd: string): number {
  return new Date(`${ymd}T12:00:00Z`).getUTCDay();
}

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
export function longDay(ymd: string): string {
  return label.format(new Date(`${ymd}T12:00:00Z`));
}

/** "5:30 PM" for "17:30". */
export function timeLabel(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

export interface TimeSlot {
  hhmm: string;
  label: string;
}

const pad2 = (n: number) => String(n).padStart(2, '0');

function buildSlots(stepMinutes: number): TimeSlot[] {
  const out: TimeSlot[] = [];
  for (let mins = 0; mins < 24 * 60; mins += stepMinutes) {
    const hhmm = `${pad2(Math.floor(mins / 60))}:${pad2(mins % 60)}`;
    out.push({ hhmm, label: timeLabel(hhmm) });
  }
  return out;
}

/** One-click common task times shown in the date selector. */
export const TIME_PRESETS: readonly { hhmm: string; label: string; short: string }[] = [
  { hhmm: '06:00', label: '6:00 AM', short: '6 AM' },
  { hhmm: '09:00', label: '9:00 AM', short: '9 AM' },
  { hhmm: '12:00', label: '12:00 PM', short: '12 PM' },
  { hhmm: '15:00', label: '3:00 PM', short: '3 PM' },
  { hhmm: '17:00', label: '5:00 PM', short: '5 PM' },
  { hhmm: '20:00', label: '8:00 PM', short: '8 PM' },
];

/** All 48 half-hour slots across the day (12:00 AM … 11:30 PM). */
export const TIME_SLOTS: readonly TimeSlot[] = buildSlots(30);
const QUARTER_SLOTS: readonly TimeSlot[] = buildSlots(15);

/** Parse a typed time string into canonical 24-hour "HH:MM", or null if invalid.
 * Accepts "6a", "6am", "6:00 AM", "09:30", "930", "930p", "14:30", "1430", "noon", "midnight",
 * and bare hours (1-7 mean PM, 8-12 mean AM/noon, 13-23 mean 24-hour). */
export function parseTimeInput(raw: string): string | null {
  const s = raw.trim().toLowerCase().replace(/\./g, '');
  if (!s) return null;
  if (s === 'noon') return '12:00';
  if (s === 'midnight') return '00:00';

  const m = /^(\d{1,4})(?::(\d{2}))?\s*([ap]m?)?$/.exec(s);
  if (!m) return null;
  const [, digits, minPart, meridiem] = m;
  let h: number;
  let min: number;
  if (minPart !== undefined) {
    if (digits.length > 2) return null;
    h = Number(digits);
    min = Number(minPart);
  } else if (digits.length >= 3) {
    h = Number(digits.slice(0, -2));
    min = Number(digits.slice(-2));
  } else {
    h = Number(digits);
    min = 0;
  }
  if (min > 59) return null;

  if (meridiem) {
    if (h < 1 || h > 12) return null;
    if (meridiem.startsWith('p')) h = h === 12 ? 12 : h + 12;
    else h = h === 12 ? 0 : h;
  } else {
    if (h > 23) return null;
    // Bare 1-7 without colon or leading zero (e.g. "2") reads as PM, matching quick add.
    if (minPart === undefined && digits.length === 1 && h >= 1 && h <= 7) h += 12;
  }
  return `${pad2(h)}:${pad2(min)}`;
}

/** Clickable time suggestions for a typed query: 48 half-hour slots when blank, else matching
 * quarter-hour slots plus the exact parsed time first when it is valid. */
export function filterTimeSlots(query: string): TimeSlot[] {
  const trimmed = query.trim();
  if (!trimmed) return [...TIME_SLOTS];
  const lower = trimmed.toLowerCase();
  const compact = lower.replace(/[\s:]/g, '');

  const out: TimeSlot[] = [];
  const seen = new Set<string>();
  const add = (slot: TimeSlot) => {
    if (seen.has(slot.hhmm)) return;
    seen.add(slot.hhmm);
    out.push(slot);
  };

  // Match 15-minute slots against formatted label, compact label ("930am"), and 24-hour HH:MM.
  const matched: TimeSlot[] = [];
  for (const slot of QUARTER_SLOTS) {
    const lbl = slot.label.toLowerCase();
    const lblCompact = lbl.replace(/[\s:]/g, '');
    const hhmmCompact = slot.hhmm.replace(':', '');
    if (
      lbl.startsWith(lower) ||
      lblCompact.startsWith(compact) ||
      slot.hhmm.startsWith(lower) ||
      hhmmCompact.startsWith(compact) ||
      (lower === 'noon' && slot.hhmm === '12:00') ||
      (lower === 'midnight' && slot.hhmm === '00:00')
    ) {
      matched.push(slot);
    }
  }

  const parsed = parseTimeInput(trimmed);
  // If the user typed a valid non-quarter-hour time (e.g. "9:07am"), offer it first.
  if (parsed && !matched.some((s) => s.hhmm === parsed)) {
    add({ hhmm: parsed, label: timeLabel(parsed) });
  }
  for (const slot of matched) add(slot);
  // If nothing matched as a prefix (e.g. "2p" -> 14:00), still offer the parsed time.
  if (parsed && out.length === 0) add({ hhmm: parsed, label: timeLabel(parsed) });
  return out;
}
