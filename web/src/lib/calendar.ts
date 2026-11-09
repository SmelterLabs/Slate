// The Calendar layout's date math: which days a month or week page shows, and which items land
// on which day. Days are workspace calendar dates (YYYY-MM-DD), weeks start on Sunday (ClickUp's
// default). Pure; Vitest covers it.

import { addDays, dayOf, weekday, type Scope } from './views';
import type { Item, UpcomingRepeat } from './types';

export type Span = 'month' | 'week';

/** The Sunday on or before a day. */
export function weekStart(ymd: string): string {
  return addDays(ymd, -weekday(ymd));
}

/** Every day a page shows: whole weeks covering the month, or the one week around `anchor`. */
export function pageDays(anchor: string, span: Span): string[] {
  if (span === 'week') return Array.from({ length: 7 }, (_, i) => addDays(weekStart(anchor), i));
  const first = `${anchor.slice(0, 7)}-01`;
  const start = weekStart(first);
  const nextMonth = shiftPage(first, 'month', 1);
  const days: string[] = [];
  for (let d = start; d < nextMonth || weekday(d) !== 0; d = addDays(d, 1)) days.push(d);
  return days;
}

/** The anchor of the previous or next page. */
export function shiftPage(anchor: string, span: Span, dir: -1 | 1): string {
  if (span === 'week') return addDays(anchor, 7 * dir);
  const [y, m] = anchor.split('-').map(Number);
  const total = y * 12 + (m - 1) + dir;
  return `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, '0')}-01`;
}

const monthFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'long', year: 'numeric' });
const shortFmt = new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric' });

/** "October 2026", or "Oct 18 – Oct 24" for a week. */
export function pageTitle(anchor: string, span: Span): string {
  if (span === 'month') return monthFmt.format(new Date(`${anchor.slice(0, 7)}-01T12:00:00Z`));
  const days = pageDays(anchor, 'week');
  const f = (d: string) => shortFmt.format(new Date(`${d}T12:00:00Z`));
  return `${f(days[0])} – ${f(days[6])}`;
}

/**
 * Items by the day they sit on: the due day; an item with a start date before its due day
 * also shows on every day between (`spans`), so multi-day work reads as a bar of chips.
 * Items with no due date are returned as `unscheduled`.
 */
export function placeItems(
  items: Iterable<Item>,
  days: string[],
): { byDay: Map<string, Item[]>; spans: Set<string>; unscheduled: Item[] } {
  const byDay = new Map<string, Item[]>(days.map((d) => [d, []]));
  const spans = new Set<string>(); // `${day}|${item id}` for continuation chips
  const unscheduled: Item[] = [];
  const first = days[0];
  const last = days[days.length - 1];
  for (const item of items) {
    if (!item.due_at) {
      unscheduled.push(item);
      continue;
    }
    const due = dayOf(item.due_at);
    const start = item.start_at ? dayOf(item.start_at) : due;
    if (due < first || start > last) continue;
    for (let d = start < first ? first : start; d <= due && d <= last; d = addDays(d, 1)) {
      byDay.get(d)?.push(item);
      if (d !== due) spans.add(`${d}|${item.id}`);
    }
  }
  return { byDay, spans, unscheduled };
}

/** A view's calendar span (month unless it says week). */
export function calendarSpan(config: { calendar_span?: Span }): Span {
  return config.calendar_span === 'week' ? 'week' : 'month';
}

/** Future repeats that belong to a scope, by day (a list page shows only its own rules). */
export function placeRepeats(
  repeats: UpcomingRepeat[],
  scope: Scope,
  listSection: (listId: string) => string | undefined,
): Map<string, UpcomingRepeat[]> {
  const byDay = new Map<string, UpcomingRepeat[]>();
  for (const r of repeats) {
    if (scope.kind === 'list' && r.list_id !== scope.id) continue;
    if (scope.kind === 'section' && listSection(r.list_id) !== scope.id) continue;
    const day = byDay.get(r.local_date) ?? [];
    day.push(r);
    byDay.set(r.local_date, day);
  }
  return byDay;
}
