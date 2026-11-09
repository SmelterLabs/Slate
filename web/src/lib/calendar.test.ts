import { describe, expect, it } from 'vitest';
import { pageDays, pageTitle, placeItems, placeRepeats, shiftPage, weekStart } from './calendar';
import type { Item, UpcomingRepeat } from './types';

const item = (id: string, due: string | null, start: string | null = null) =>
  ({ id, due_at: due, start_at: start }) as unknown as Item;

describe('calendar pages', () => {
  it('starts weeks on Sunday', () => {
    expect(weekStart('2026-10-23')).toBe('2026-10-18'); // a Friday
    expect(weekStart('2026-10-18')).toBe('2026-10-18');
  });

  it('covers a month in whole weeks', () => {
    const days = pageDays('2026-10-23', 'month');
    expect(days[0]).toBe('2026-09-27');
    expect(days.at(-1)).toBe('2026-10-31');
    expect(days.length % 7).toBe(0);
    const nov = pageDays('2026-11-05', 'month');
    expect(nov[0]).toBe('2026-11-01');
    expect(nov.at(-1)).toBe('2026-12-05');
  });

  it('shifts months across a year and weeks by seven days', () => {
    expect(shiftPage('2026-12-15', 'month', 1)).toBe('2027-01-01');
    expect(shiftPage('2026-01-15', 'month', -1)).toBe('2025-12-01');
    expect(shiftPage('2026-10-23', 'week', 1)).toBe('2026-10-30');
  });

  it('titles pages', () => {
    expect(pageTitle('2026-10-23', 'month')).toBe('October 2026');
    expect(pageTitle('2026-10-23', 'week')).toBe('Oct 18 – Oct 24');
  });
});

describe('placing items', () => {
  const days = pageDays('2026-10-23', 'week');

  it('puts an item on its Chicago due day, and undated ones aside', () => {
    // 2026-10-24T02:00Z is 21:00 on Oct 23 in Chicago.
    const { byDay, unscheduled } = placeItems([item('a', '2026-10-24T02:00:00Z'), item('b', null)], days);
    expect(byDay.get('2026-10-23')!.map((i) => i.id)).toEqual(['a']);
    expect(unscheduled.map((i) => i.id)).toEqual(['b']);
  });

  it('spreads a start-to-due item across its days, clipped to the page', () => {
    const { byDay, spans } = placeItems([item('a', '2026-10-20T22:00:00Z', '2026-10-10T22:00:00Z')], days);
    expect([...byDay].filter(([, v]) => v.length).map(([d]) => d)).toEqual([
      '2026-10-18',
      '2026-10-19',
      '2026-10-20',
    ]);
    expect(spans.has('2026-10-19|a')).toBe(true);
    expect(spans.has('2026-10-20|a')).toBe(false);
  });

  it('ignores items off the page', () => {
    const { byDay } = placeItems([item('a', '2026-12-01T22:00:00Z')], days);
    expect([...byDay.values()].flat()).toEqual([]);
  });
});

describe('future repeats', () => {
  const rep = (list_id: string, local_date: string) => ({ list_id, local_date, title: 'x' }) as UpcomingRepeat;
  const repeats = [rep('L1', '2026-11-04'), rep('L2', '2026-11-04'), rep('L1', '2026-11-11')];
  const section = (id: string) => (id === 'L1' ? 'S1' : 'S2');
  it('keeps a list page to its own rules', () => {
    const by = placeRepeats(repeats, { kind: 'list', id: 'L1' }, section);
    expect(by.get('2026-11-04')?.length).toBe(1);
    expect(by.get('2026-11-11')?.length).toBe(1);
  });
  it('keeps a section page to its lists, and Everything to all', () => {
    expect(placeRepeats(repeats, { kind: 'section', id: 'S2' }, section).get('2026-11-04')?.length).toBe(1);
    expect(placeRepeats(repeats, { kind: 'everything' }, section).get('2026-11-04')?.length).toBe(2);
  });
});
