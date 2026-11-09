import { describe, expect, it } from 'vitest';
import { fromRrule, nthOf, ordinal, presetFor, shortRule, toRrule, weekdayOf, type Rule } from './rrule';

const never = { kind: 'never' } as const;

describe('toRrule / fromRrule round trips', () => {
  const cases: [string, Rule][] = [
    ['FREQ=DAILY', { preset: { kind: 'daily', interval: 1 }, ends: never }],
    ['FREQ=DAILY;INTERVAL=3', { preset: { kind: 'daily', interval: 3 }, ends: never }],
    ['FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR', { preset: { kind: 'weekdays' }, ends: never }],
    [
      'FREQ=WEEKLY;BYDAY=MO,WE,FR',
      { preset: { kind: 'weekly', interval: 1, days: ['MO', 'WE', 'FR'] }, ends: never },
    ],
    [
      'FREQ=WEEKLY;INTERVAL=2;BYDAY=WE',
      { preset: { kind: 'weekly', interval: 2, days: ['WE'] }, ends: never },
    ],
    ['FREQ=WEEKLY', { preset: { kind: 'weekly', interval: 1, days: [] }, ends: never }],
    [
      'FREQ=MONTHLY;BYMONTHDAY=15',
      { preset: { kind: 'monthly_day', interval: 1, monthDays: [15] }, ends: never },
    ],
    [
      'FREQ=MONTHLY;BYMONTHDAY=1,15,-1',
      { preset: { kind: 'monthly_day', interval: 1, monthDays: [1, 15, -1] }, ends: never },
    ],
    [
      'FREQ=MONTHLY;BYMONTHDAY=-1',
      { preset: { kind: 'monthly_day', interval: 1, monthDays: [-1] }, ends: never },
    ],
    [
      'FREQ=MONTHLY;BYDAY=2WE,4WE',
      { preset: { kind: 'monthly_nth', interval: 1, nths: [2, 4], day: 'WE' }, ends: never },
    ],
    [
      'FREQ=MONTHLY;BYDAY=-1FR',
      { preset: { kind: 'monthly_nth', interval: 1, nths: [-1], day: 'FR' }, ends: never },
    ],
    [
      'FREQ=MONTHLY;INTERVAL=3;BYDAY=1MO,-1MO',
      { preset: { kind: 'monthly_nth', interval: 3, nths: [1, -1], day: 'MO' }, ends: never },
    ],
    [
      'FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1',
      { preset: { kind: 'monthly_nth', interval: 1, nths: [-1], day: 'weekday' }, ends: never },
    ],
    [
      'FREQ=MONTHLY;INTERVAL=4;BYMONTHDAY=3',
      { preset: { kind: 'monthly_day', interval: 4, monthDays: [3] }, ends: never },
    ],
    [
      'FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=15',
      { preset: { kind: 'yearly_date', interval: 1, month: 3, monthDay: 15 }, ends: never },
    ],
    [
      'FREQ=YEARLY;BYMONTH=5;BYDAY=2SU',
      { preset: { kind: 'yearly_nth', interval: 1, month: 5, nth: 2, day: 'SU' }, ends: never },
    ],
    [
      'FREQ=YEARLY;BYMONTH=11;BYDAY=-1TH',
      { preset: { kind: 'yearly_nth', interval: 1, month: 11, nth: -1, day: 'TH' }, ends: never },
    ],
    [
      'FREQ=DAILY;COUNT=10',
      { preset: { kind: 'daily', interval: 1 }, ends: { kind: 'count', count: 10 } },
    ],
    [
      'FREQ=WEEKLY;BYDAY=TU;UNTIL=20261231',
      {
        preset: { kind: 'weekly', interval: 1, days: ['TU'] },
        ends: { kind: 'until', date: '2026-12-31' },
      },
    ],
  ];

  for (const [rrule, rule] of cases) {
    it(rrule, () => {
      expect(toRrule(rule)).toBe(rrule);
      expect(fromRrule(rrule)).toEqual(rule);
      expect(toRrule(fromRrule(rrule)!)).toBe(rrule);
    });
  }
});

describe('fromRrule reads equivalent spellings', () => {
  it('BYSETPOS on one weekday is the Nth weekday (2nd and 4th Wednesday)', () => {
    const r = fromRrule('FREQ=MONTHLY;BYDAY=WE;BYSETPOS=4,2')!;
    expect(r.preset).toEqual({ kind: 'monthly_nth', interval: 1, nths: [2, 4], day: 'WE' });
    expect(toRrule(r)).toBe('FREQ=MONTHLY;BYDAY=2WE,4WE');
  });

  it('daily on weekdays is "every weekday"', () => {
    expect(fromRrule('FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR')!.preset).toEqual({ kind: 'weekdays' });
  });

  it('weekly on weekdays every 2 weeks stays weekly', () => {
    expect(fromRrule('FREQ=WEEKLY;INTERVAL=2;BYDAY=FR,MO,TU,WE,TH')!.preset).toEqual({
      kind: 'weekly',
      interval: 2,
      days: ['MO', 'TU', 'WE', 'TH', 'FR'],
    });
  });

  it('yearly Nth weekday via BYSETPOS (Thanksgiving)', () => {
    expect(fromRrule('FREQ=YEARLY;BYMONTH=11;BYDAY=TH;BYSETPOS=4')!.preset).toEqual({
      kind: 'yearly_nth',
      interval: 1,
      month: 11,
      nth: 4,
      day: 'TH',
    });
  });

  it('a bare yearly rule takes the anchor date', () => {
    expect(fromRrule('FREQ=YEARLY', '2026-10-28')!.preset).toEqual({
      kind: 'yearly_date',
      interval: 1,
      month: 10,
      monthDay: 28,
    });
    expect(fromRrule('FREQ=YEARLY')).toBeNull();
  });

  it('accepts a prefix, lowercase and a timed UNTIL', () => {
    expect(fromRrule('rrule:freq=weekly;byday=we;until=20261231T235959Z')).toEqual({
      preset: { kind: 'weekly', interval: 1, days: ['WE'] },
      ends: { kind: 'until', date: '2026-12-31' },
    });
  });
});

describe('fromRrule leaves what presets cannot say to Advanced', () => {
  for (const rrule of [
    'FREQ=DAILY;BYHOUR=9',
    'FREQ=WEEKLY;WKST=SU;BYDAY=MO',
    'FREQ=MONTHLY;BYDAY=2WE,4TH',
    'FREQ=MONTHLY;BYDAY=WE,TH;BYSETPOS=2',
    'FREQ=MONTHLY;BYMONTH=1;BYMONTHDAY=1',
    'FREQ=YEARLY;BYMONTH=1,7;BYMONTHDAY=1',
    'FREQ=YEARLY;BYYEARDAY=100',
    'FREQ=HOURLY',
    'FREQ=DAILY;COUNT=2;UNTIL=20270101',
    'FREQ=DAILY;INTERVAL=0',
    'nonsense',
    '',
  ]) {
    it(rrule || '(empty)', () => expect(fromRrule(rrule)).toBeNull());
  }
});

describe('defaults from a start date', () => {
  it('knows weekdays and Nth weekdays', () => {
    expect(weekdayOf('2026-10-28')).toBe('WE');
    expect(weekdayOf('2026-11-01')).toBe('SU');
    expect(nthOf('2026-10-14')).toBe(2); // 2nd Wednesday
    expect(nthOf('2026-10-28')).toBe(-1); // last Wednesday (4th, but no 5th in October)
    expect(nthOf('2026-10-22')).toBe(4);
  });

  it('suggests presets that contain the start date', () => {
    const a = '2026-10-14';
    expect(toRrule({ preset: presetFor('weekly', a), ends: never })).toBe('FREQ=WEEKLY;BYDAY=WE');
    expect(toRrule({ preset: presetFor('monthly_day', a), ends: never })).toBe(
      'FREQ=MONTHLY;BYMONTHDAY=14',
    );
    expect(toRrule({ preset: presetFor('monthly_nth', a), ends: never })).toBe(
      'FREQ=MONTHLY;BYDAY=2WE',
    );
    expect(toRrule({ preset: presetFor('yearly_date', a), ends: never })).toBe(
      'FREQ=YEARLY;BYMONTH=10;BYMONTHDAY=14',
    );
    expect(toRrule({ preset: presetFor('yearly_nth', a), ends: never })).toBe(
      'FREQ=YEARLY;BYMONTH=10;BYDAY=2WE',
    );
  });

  it('the last day of a month suggests "last day"', () => {
    expect(presetFor('monthly_day', '2026-09-30')).toEqual({
      kind: 'monthly_day',
      interval: 1,
      monthDays: [-1],
    });
    expect(presetFor('monthly_day', '2026-02-28')).toEqual({
      kind: 'monthly_day',
      interval: 1,
      monthDays: [28],
    });
  });

  it('ordinals', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, -1, -2].map(ordinal)).toEqual([
      '1st',
      '2nd',
      '3rd',
      '4th',
      '11th',
      '12th',
      '13th',
      '21st',
      '22nd',
      '23rd',
      'last',
      '2nd to last',
    ]);
  });
});

describe('shortRule', () => {
  it('names a rule for a card', () => {
    expect(shortRule({ rrule: 'FREQ=WEEKLY;BYDAY=WE' })).toBe('Weekly');
    expect(shortRule({ rrule: 'FREQ=WEEKLY;INTERVAL=2;BYDAY=WE' })).toBe('Every 2 weeks');
    expect(shortRule({ rrule: 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR' })).toBe('Weekdays');
    expect(shortRule({ rrule: 'FREQ=DAILY;INTERVAL=3', trigger: 'on_done' })).toBe('Every 3 days · when done');
    expect(shortRule({ rrule: 'FREQ=MONTHLY;BYMONTHDAY=15' })).toBe('Monthly');
  });
});
