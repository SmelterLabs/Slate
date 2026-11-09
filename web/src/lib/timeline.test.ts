import { describe, expect, it } from 'vitest';
import {
  arrows,
  axisDays,
  axisRange,
  barSpan,
  type Bar,
  dayIndex,
  dayToIso,
  dragDays,
  rows,
  ticks,
} from './timeline';
import type { Item } from './types';

// Dates are built at noon UTC, which is 07:00 America/Chicago, so the Chicago calendar day of
// `${ymd}T12:00:00Z` is the same ymd — the fixtures line up with dayOf().
const iso = (ymd: string) => `${ymd}T12:00:00Z`;
const item = (id: string, due: string | null, start: string | null = null, extra: Partial<Item> = {}) =>
  ({ id, due_at: due && iso(due), start_at: start && iso(start), blocked_by: [], ...extra }) as unknown as Item;

describe('barSpan', () => {
  it('spans start to due, a lone due date is a milestone, no due date is null', () => {
    expect(barSpan(item('a', '2026-10-20', '2026-10-18'))).toEqual({
      start: '2026-10-18',
      end: '2026-10-20',
      milestone: false,
    });
    expect(barSpan(item('b', '2026-10-20'))).toEqual({ start: '2026-10-20', end: '2026-10-20', milestone: true });
    expect(barSpan(item('c', null, '2026-10-18'))).toBeNull();
    // a start after the due date collapses to the due day, never a backwards bar
    expect(barSpan(item('d', '2026-10-18', '2026-10-25'))).toEqual({
      start: '2026-10-18',
      end: '2026-10-18',
      milestone: false,
    });
  });
});

describe('axisRange', () => {
  const today = '2026-10-21';
  it('covers the earliest start to the latest due, padded, and always includes today', () => {
    const r = axisRange([item('a', '2026-10-25', '2026-10-23'), item('b', '2026-10-30')], today);
    expect(r).toEqual({ from: '2026-10-19', to: '2026-11-01' }); // 2 days of padding each side
  });
  it('stretches back to today when all work is in the future', () => {
    const r = axisRange([item('a', '2026-11-10', '2026-11-05')], today, 0);
    expect(r.from).toBe(today);
    expect(r.to).toBe('2026-11-10');
  });
  it('falls back to a month around today when nothing is dated', () => {
    expect(axisRange([item('a', null)], today)).toEqual({ from: '2026-10-07', to: '2026-11-04' });
  });
});

describe('axis helpers', () => {
  it('lists inclusive days and indexes them', () => {
    expect(axisDays('2026-10-19', '2026-10-22')).toEqual(['2026-10-19', '2026-10-20', '2026-10-21', '2026-10-22']);
    expect(dayIndex('2026-10-19', '2026-10-22')).toBe(3);
    expect(dayIndex('2026-10-19', '2026-10-19')).toBe(0);
  });
});

describe('ticks', () => {
  it('day zoom: one per day, Mondays major', () => {
    const t = ticks('2026-10-18', '2026-10-20', 'day'); // Sun, Mon, Tue
    expect(t.map((x) => x.span)).toEqual([1, 1, 1]);
    expect(t.map((x) => x.major)).toEqual([false, true, false]);
  });
  it('week zoom: one per Sunday week, spanning 7 days', () => {
    const t = ticks('2026-10-19', '2026-11-02', 'week');
    // The week starts on the Sunday before the axis: it is clipped to the axis (so its label
    // shows) and the rest keep a full 7 days.
    expect(t[0].at).toBe(0);
    expect(t[0].span).toBe(6);
    expect(t[0].label).toBe(t[0].label.trim());
    expect(t.slice(1).every((x) => x.span === 7 && x.at > 0)).toBe(true);
  });
  it('month zoom: one per month covering its days', () => {
    const t = ticks('2026-10-15', '2026-11-10', 'month');
    expect(t.map((x) => x.label)).toEqual(['October 2026', 'November 2026']);
    expect(t[0].span).toBe(17); // Oct 15..31 inclusive from the axis start
    expect(t.every((x) => x.major)).toBe(true);
  });
});

describe('rows', () => {
  const groups = [
    { title: 'Doing', items: [item('a', '2026-10-20'), item('b', null)] },
    { title: 'Someday', items: [item('c', null)] }, // only undated: no header, no rows
    { title: 'Done', items: [] as Item[] },
  ];
  it('keeps only dated items, with a header per group that has one', () => {
    const r = rows(groups, true);
    expect(r.map((x) => x.kind)).toEqual(['group', 'item']);
    expect(r[0].title).toBe('Doing');
    expect(r[1].bar?.milestone).toBe(true);
  });
  it('omits headers when ungrouped', () => {
    expect(rows(groups, false).map((x) => [x.kind, x.item?.id])).toEqual([['item', 'a']]);
  });
});

describe('arrows', () => {
  it('links a blocker to the item that waits, both on the board, no self-links or danglers', () => {
    const bar = (id: string, blocked_by: string[]): Bar => ({
      item: item(id, '2026-10-20', '2026-10-18', { blocked_by }),
      start: '2026-10-18',
      end: '2026-10-20',
      milestone: false,
    });
    const bars = new Map([
      ['build', bar('build', ['design', 'copy', 'gone', 'build'])],
      ['design', bar('design', [])],
      ['copy', bar('copy', [])],
    ]);
    expect(arrows(bars).sort((a, b) => a.fromId.localeCompare(b.fromId))).toEqual([
      { fromId: 'copy', toId: 'build' },
      { fromId: 'design', toId: 'build' },
    ]);
  });
});

describe('drag math', () => {
  it('snaps a pixel delta to whole days per zoom', () => {
    expect(dragDays(44, 'day')).toBe(1);
    expect(dragDays(30, 'week')).toBe(2); // 20px/day, 30/20 -> 1.5 -> 2
    expect(dragDays(-21, 'week')).toBe(-1);
  });
  it('dayToIso keeps an existing time of day', () => {
    const kept = dayToIso('2026-10-25', '2026-10-20T09:30:00Z');
    expect(new Date(kept).getHours()).toBe(new Date('2026-10-20T09:30:00Z').getHours());
    expect(dayToIso('2026-10-25').slice(0, 10)).toBe(new Date('2026-10-25T12:00:00').toISOString().slice(0, 10));
  });
});
