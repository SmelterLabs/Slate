// Reminders on the board: the "Remind me" presets in the task window, how a reminder reads
// ("Tomorrow 9:00 AM", "1 hour before due"), and the daily digest's one-line summary. The
// server fires them (service/reminders.py); the board only sets, lists and removes.

import { dueLabel } from "./format";

export interface Reminder {
  id: string;
  item_id: string;
  item_title: string | null;
  account_id: string;
  account: string | null;
  remind_at: string | null;
  before_minutes: number | null;
  at: string | null;
  note: string | null;
  fired: boolean;
  created_by: string;
  created_at: string;
}

/** What POST /items/{id}/reminders takes. */
export interface ReminderBody {
  at?: string;
  before_minutes?: number;
  account?: string;
  note?: string;
}

export interface Preset {
  key: string;
  label: string;
  body: ReminderBody;
}

/** "at the due time", "15 min before due", "1 hour before due", "2 days before due". */
export function leadText(minutes: number): string {
  if (minutes <= 0) return "at the due time";
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;
  if (minutes % 10080 === 0)
    return `${plural(minutes / 10080, "week")} before due`;
  if (minutes % 1440 === 0)
    return `${plural(minutes / 1440, "day")} before due`;
  if (minutes % 60 === 0) return `${plural(minutes / 60, "hour")} before due`;
  return `${minutes} min before due`;
}

/** How a reminder reads on its chip. */
export function whenText(
  r: Pick<Reminder, "remind_at" | "before_minutes" | "at">,
  now = new Date(),
): string {
  if (r.remind_at)
    return dueLabel(r.remind_at, now).replace(
      /^(Today|Tomorrow)$/,
      "$1 5:00 PM",
    );
  return leadText(r.before_minutes ?? 0);
}

function at(d: Date, hour: number, minute = 0): Date {
  const x = new Date(d);
  x.setHours(hour, minute, 0, 0);
  return x;
}

/** The one-click fixed times, relative to now (before-due ones are BEFORE_DUE). Times are
 * the device's local clock, usually the same zone as Slate's display zone. */
export function presets(now: Date): Preset[] {
  const iso = (d: Date) => d.toISOString();
  const tomorrow = at(new Date(now.getTime() + 86_400_000), 9);
  const monday = new Date(now);
  monday.setDate(now.getDate() + ((8 - now.getDay()) % 7 || 7));
  const out: Preset[] = [
    {
      key: "20m",
      label: "In 20 minutes",
      body: { at: iso(new Date(now.getTime() + 20 * 60_000)) },
    },
    {
      key: "1h",
      label: "In 1 hour",
      body: { at: iso(new Date(now.getTime() + 3_600_000)) },
    },
    {
      key: "3h",
      label: "In 3 hours",
      body: { at: iso(new Date(now.getTime() + 3 * 3_600_000)) },
    },
  ];
  if (now.getHours() < 17)
    out.push({
      key: "eve",
      label: "This evening (6 PM)",
      body: { at: iso(at(now, 18)) },
    });
  out.push(
    {
      key: "tom",
      label: "Tomorrow morning (9 AM)",
      body: { at: iso(tomorrow) },
    },
    {
      key: "mon",
      label: "Next Monday (9 AM)",
      body: { at: iso(at(monday, 9)) },
    },
  );
  return out;
}

/** The "Before due" toggles in the task window: any number at once, none by default. */
export const BEFORE_DUE: { minutes: number; label: string }[] = [
  { minutes: 0, label: "At due" },
  { minutes: 5, label: "5 min" },
  { minutes: 15, label: "15 min" },
  { minutes: 30, label: "30 min" },
  { minutes: 60, label: "1 hour" },
  { minutes: 120, label: "2 hours" },
  { minutes: 1440, label: "1 day" },
  { minutes: 2880, label: "2 days" },
  { minutes: 10080, label: "1 week" },
];

export type LeadUnit = "min" | "hour" | "day" | "week";
const UNIT_MINUTES: Record<LeadUnit, number> = { min: 1, hour: 60, day: 1440, week: 10080 };
/** The server's limit on a lead (service/reminders.py MAX_BEFORE_MINUTES: 60 days). */
export const MAX_LEAD_MINUTES = 60 * 24 * 60;

/** A custom "N units before due" as minutes, or null when it is not a usable lead. */
export function customLead(amount: number | string, unit: LeadUnit): number | null {
  const n = Number(amount);
  if (!Number.isInteger(n) || n < 1) return null;
  const m = n * UNIT_MINUTES[unit];
  return m <= MAX_LEAD_MINUTES ? m : null;
}

/** A `datetime-local` input's value as an ISO instant (the device's zone), or null. */
export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

export interface DigestCounts {
  overdue?: number;
  today?: number;
  waiting?: number;
}

/** "2 overdue, 1 due today, 1 waiting on you" (the digest event's `after.counts`). */
export function digestText(counts: DigestCounts | undefined): string {
  const c = counts ?? {};
  const bits = [
    c.overdue ? `${c.overdue} overdue` : "",
    c.today ? `${c.today} due today` : "",
    c.waiting ? `${c.waiting} waiting on you` : "",
  ].filter(Boolean);
  return bits.length ? bits.join(", ") : "nothing due";
}

/** Hour choices for the digest, "6 AM" … "9 PM". */
export function hourLabel(h: number): string {
  if (h === 0) return "12 AM";
  if (h === 12) return "12 PM";
  return h < 12 ? `${h} AM` : `${h - 12} PM`;
}
