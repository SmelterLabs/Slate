// The Timeline (Gantt) layout's date math, pure and Vitest-covered. A row is one item with a
// bar from its start day to its due day (a dated item with only a due date is a one-day marker);
// the axis is a run of workspace calendar days at a zoom (day/week/month) that sets the day width
// and the tick labels. Dependency arrows come from blocked_by links, drawn bar-end to bar-start.

import { addDays, dayOf, weekday } from './views';
import type { Item } from './types';

export type Zoom = 'day' | 'week' | 'month';

/** Pixels per calendar day at each zoom. Day is roomy; month is a dense overview. */
export const DAY_PX: Record<Zoom, number> = { day: 44, week: 20, month: 7 };
export const ROW_H = 36;
export const BAR_H = 20;

export interface Bar {
  item: Item;
  /** Inclusive day span the bar covers (YYYY-MM-DD). */
  start: string;
  end: string;
  /** Only a due date, no start: a point marker, not a stretch. */
  milestone: boolean;
}

/** An item's day span: start→due, or the due day alone, or null when it has no due date.
 * A start after the due date is clamped to a single day (bad data shouldn't draw backwards). */
export function barSpan(item: Item): { start: string; end: string; milestone: boolean } | null {
  if (!item.due_at) return null;
  const end = dayOf(item.due_at);
  if (!item.start_at) return { start: end, end, milestone: true };
  const start = dayOf(item.start_at);
  if (start > end) return { start: end, end, milestone: false };
  return { start, end, milestone: false };
}

/** The window the axis spans: the earliest start to the latest due across the bars, padded, and
 * always wide enough to show `today`. Falls back to a month around today when nothing is dated. */
export function axisRange(items: Item[], today: string, pad = 2): { from: string; to: string } {
  let from: string | null = null;
  let to: string | null = null;
  for (const it of items) {
    const span = barSpan(it);
    if (!span) continue;
    if (from === null || span.start < from) from = span.start;
    if (to === null || span.end > to) to = span.end;
  }
  if (from === null || to === null) return { from: addDays(today, -14), to: addDays(today, 14) };
  from = from < today ? from : today;
  to = to > today ? to : today;
  return { from: addDays(from, -pad), to: addDays(to, pad) };
}

/** Every day on the axis, inclusive. */
export function axisDays(from: string, to: string): string[] {
  const days: string[] = [];
  for (let d = from; d <= to; d = addDays(d, 1)) days.push(d);
  return days;
}

/** Whole-day offset of `day` from the axis start (can be used as a column index). */
export function dayIndex(from: string, day: string): number {
  return Math.round((Date.parse(`${day}T12:00:00Z`) - Date.parse(`${from}T12:00:00Z`)) / 86_400_000);
}

export interface Tick {
  /** Left offset in days from the axis start. */
  at: number;
  /** How many days the tick's cell spans (for the width of its header cell). */
  span: number;
  label: string;
  /** A heavier line (month starts under week zoom, Mondays under day zoom). */
  major: boolean;
}

const dayFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', day: 'numeric' });
const monFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric' });
const monthFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'long', year: 'numeric' });
const utc = (d: string) => new Date(`${d}T12:00:00Z`);

/** The header ticks for a zoom: one per day (day), per week starting Sunday (week), or per month
 * (month). Each carries where it starts and how wide it is, so the header lays out by day width. */
export function ticks(from: string, to: string, zoom: Zoom): Tick[] {
  const out: Tick[] = [];
  if (zoom === 'day') {
    for (let d = from; d <= to; d = addDays(d, 1)) {
      out.push({ at: dayIndex(from, d), span: 1, label: dayFmt.format(utc(d)), major: weekday(d) === 1 });
    }
    return out;
  }
  if (zoom === 'week') {
    let d = addDays(from, -weekday(from)); // the Sunday on/before the start
    for (; d <= to; d = addDays(d, 7)) {
      const at = dayIndex(from, d);
      // The first week may start before the axis: clip it to the axis so its label still shows.
      out.push({
        at: Math.max(0, at),
        span: at < 0 ? 7 + at : 7,
        label: monFmt.format(utc(d < from ? from : d)),
        major: Number(d.slice(8, 10)) <= 7,
      });
    }
    return out;
  }
  let d = `${from.slice(0, 7)}-01`;
  for (; d <= to; d = `${nextMonth(d)}-01`) {
    const next = `${nextMonth(d)}-01`;
    const at = Math.max(0, dayIndex(from, d)); // a partial first month starts at the axis edge
    const span = dayIndex(from, next) - at;
    out.push({ at, span, label: monthFmt.format(utc(d)), major: true });
  }
  return out;
}

function nextMonth(ymd: string): string {
  const [y, m] = ymd.split('-').map(Number);
  const t = y * 12 + (m - 1) + 1;
  return `${Math.floor(t / 12)}-${String((t % 12) + 1).padStart(2, '0')}`;
}

export interface Placed {
  item: Item;
  bar: Bar | null;
  row: number;
}

/** Flatten the view's groups into timeline rows, keeping group order; a group with a title and
 * at least one dated item gets a header row. Items with no due date are left out — they live in
 * the tray below the chart, not as empty rows — so every row here has a bar. */
export interface Row {
  kind: 'group' | 'item';
  /** group row */
  title?: string;
  color?: string;
  /** item row */
  item?: Item;
  bar?: Bar | null;
}

export function rows(groups: { title: string; color?: string; items: Item[] }[], withHeaders: boolean): Row[] {
  const out: Row[] = [];
  for (const g of groups) {
    const dated = g.items.map((item) => ({ item, span: barSpan(item) })).filter((x) => x.span);
    if (!dated.length) continue;
    if (withHeaders) out.push({ kind: 'group', title: g.title, color: g.color });
    for (const { item, span } of dated) out.push({ kind: 'item', item, bar: { item, ...span! } });
  }
  return out;
}

export interface Arrow {
  fromId: string;
  toId: string;
}

/** blocked_by links between items both on the timeline and both dated: an arrow points from the
 * blocker (the thing waited on) to the item that waits. Self-links and danglers are dropped. */
export function arrows(bars: Map<string, Bar>): Arrow[] {
  const out: Arrow[] = [];
  const seen = new Set<string>();
  for (const [toId, bar] of bars) {
    for (const fromId of bar.item.blocked_by ?? []) {
      if (fromId === toId || !bars.has(fromId)) continue;
      const key = `${fromId}>${toId}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ fromId, toId });
    }
  }
  return out;
}

/** Days moved when a bar is dragged `dx` pixels at a zoom (snaps to whole days). */
export function dragDays(dx: number, zoom: Zoom): number {
  return Math.round(dx / DAY_PX[zoom]);
}

/** Noon (local) on a day, as an ISO instant — a sensible default time for a dragged date so the
 * item sorts mid-day and reads as that calendar day everywhere. Keeps an existing time of day. */
export function dayToIso(day: string, keepFrom?: string | null): string {
  const d = new Date(`${day}T12:00:00`);
  if (keepFrom) {
    const prev = new Date(keepFrom);
    d.setHours(prev.getHours(), prev.getMinutes(), 0, 0);
  }
  return d.toISOString();
}
