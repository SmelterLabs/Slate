import { describe, expect, it } from 'vitest';
import { addDays, dayLabel, joinWhen, quickPicks, splitWhen, timeLabel } from './when';

describe('splitWhen / joinWhen', () => {
  it('17:00 Chicago is a date with no time (CDT and CST)', () => {
    expect(splitWhen('2026-10-28T22:00:00Z')).toEqual({ date: '2026-10-28', time: '' });
    expect(splitWhen('2026-11-04T23:00:00Z')).toEqual({ date: '2026-11-04', time: '' });
  });

  it('other times are kept, on the Chicago calendar', () => {
    expect(splitWhen('2026-10-28T14:30:00Z')).toEqual({ date: '2026-10-28', time: '09:30' });
    // 03:00Z on the 29th is still the 28th in Chicago.
    expect(splitWhen('2026-10-29T03:00:00Z')).toEqual({ date: '2026-10-28', time: '22:00' });
  });

  it('null is empty', () => {
    expect(splitWhen(null)).toEqual({ date: '', time: '' });
  });

  it('joins for PATCH', () => {
    expect(joinWhen('', '09:00')).toBeNull();
    expect(joinWhen('2026-10-28', '')).toBe('2026-10-28');
    expect(joinWhen('2026-10-28', '09:30')).toBe('2026-10-28T09:30');
  });
});

describe('quickPicks', () => {
  // Wednesday Oct 28 2026, 10:00 Chicago.
  const now = new Date('2026-10-28T15:00:00Z');

  it('today, tomorrow, next Monday, +1 week from today', () => {
    expect(quickPicks(now).map((p) => [p.label, p.date])).toEqual([
      ['Today', '2026-10-28'],
      ['Tomorrow', '2026-10-29'],
      ['Next Mon', '2026-11-02'],
      ['+1 week', '2026-11-04'],
    ]);
  });

  it('+1 week pushes the current date a week', () => {
    expect(quickPicks(now, '2026-11-10T23:00:00Z')[3].date).toBe('2026-11-17');
  });

  it('on a Monday, next Monday is a week away', () => {
    expect(quickPicks(new Date('2026-11-02T15:00:00Z'))[2].date).toBe('2026-11-09');
  });

  it('late evening in Chicago is still that day', () => {
    // 23:30 Chicago on Oct 28 is 04:30Z on the 29th.
    expect(quickPicks(new Date('2026-10-29T04:30:00Z'))[0].date).toBe('2026-10-28');
  });
});

describe('labels', () => {
  it('formats', () => {
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(dayLabel('2026-10-28')).toBe('Wed, Oct 28');
    expect(timeLabel('09:05')).toBe('9:05 AM');
    expect(timeLabel('00:00')).toBe('12:00 AM');
    expect(timeLabel('17:30')).toBe('5:30 PM');
  });
});

describe('splitWhen with the date-only flag', () => {
  it('hides the time only for a date-only due date when the flag is known', () => {
    const five = '2026-11-03T23:00:00Z'; // 17:00 Chicago
    expect(splitWhen(five, true)).toEqual({ date: '2026-11-03', time: '' });
    expect(splitWhen(five, false)).toEqual({ date: '2026-11-03', time: '17:00' });
    expect(splitWhen(five)).toEqual({ date: '2026-11-03', time: '' }); // no flag: 17:00 default
  });
});
