import { describe, expect, it } from 'vitest';
import { ev } from './fixtures';
import { verb, type HistoryContext } from './history';
import { deviceLabel, urlBase64ToBytes } from './push';
import { BEFORE_DUE, customLead, digestText, fromLocalInput, hourLabel, leadText, presets, whenText } from './reminders';

describe('leadText', () => {
  it('reads minutes before the due date in whole units', () => {
    expect(leadText(0)).toBe('at the due time');
    expect(leadText(15)).toBe('15 min before due');
    expect(leadText(60)).toBe('1 hour before due');
    expect(leadText(180)).toBe('3 hours before due');
    expect(leadText(1440)).toBe('1 day before due');
    expect(leadText(2880)).toBe('2 days before due');
    expect(leadText(10080)).toBe('1 week before due');
  });

  it('offers the before-due toggles', () => {
    expect(BEFORE_DUE.map((b) => b.minutes)).toEqual([0, 5, 15, 30, 60, 120, 1440, 2880, 10080]);
  });

  it('turns a custom amount into minutes', () => {
    expect(customLead(3, 'hour')).toBe(180);
    expect(customLead('10', 'min')).toBe(10);
    expect(customLead(3, 'week')).toBe(30240);
    expect(customLead(0, 'day')).toBeNull();
    expect(customLead(1.5, 'hour')).toBeNull();
    expect(customLead(9, 'week')).toBeNull(); // over 60 days
  });
});

describe('whenText', () => {
  const now = new Date('2030-01-09T15:00:00Z');
  it('shows a fixed time like a due date, and a lead as words', () => {
    expect(whenText({ remind_at: '2030-01-09T20:30:00Z', before_minutes: null, at: null }, now)).toBe('Today 2:30 PM');
    expect(whenText({ remind_at: null, before_minutes: 60, at: '2030-01-10T15:00:00Z' }, now)).toBe('1 hour before due');
  });
});

describe('presets', () => {
  it('offers fixed times; due-relative ones are the Before due toggles', () => {
    const now = new Date(2030, 0, 9, 10, 0); // a Wednesday morning, local time
    expect(presets(now).map((p) => p.key)).toEqual(['20m', '1h', '3h', 'eve', 'tom', 'mon']);
  });

  it('computes tomorrow 9 AM and next Monday 9 AM on the local clock', () => {
    const now = new Date(2030, 0, 9, 19, 0); // Wednesday evening: no "this evening"
    const by = Object.fromEntries(presets(now).map((p) => [p.key, p.body.at]));
    expect(by.eve).toBeUndefined();
    expect(new Date(by.tom!)).toEqual(new Date(2030, 0, 10, 9, 0));
    expect(new Date(by.mon!)).toEqual(new Date(2030, 0, 14, 9, 0));
    expect(new Date(by['1h']!).getTime() - now.getTime()).toBe(3_600_000);
  });

  it('next Monday from a Monday is a week out', () => {
    const now = new Date(2030, 0, 14, 8, 0);
    const mon = presets(now).find((p) => p.key === 'mon')!;
    expect(new Date(mon.body.at!)).toEqual(new Date(2030, 0, 21, 9, 0));
  });
});

describe('small helpers', () => {
  it('reads a datetime-local value as an instant', () => {
    expect(fromLocalInput('')).toBeNull();
    expect(fromLocalInput('nonsense')).toBeNull();
    expect(fromLocalInput('2030-01-09T09:00')).toBe(new Date(2030, 0, 9, 9, 0).toISOString());
  });

  it('summarises a digest', () => {
    expect(digestText({ overdue: 2, today: 1, waiting: 0 })).toBe('2 overdue, 1 due today');
    expect(digestText({ waiting: 1 })).toBe('1 waiting on you');
    expect(digestText(undefined)).toBe('nothing due');
  });

  it('labels hours', () => {
    expect([0, 7, 12, 18].map(hourLabel)).toEqual(['12 AM', '7 AM', '12 PM', '6 PM']);
  });

  it('names the device', () => {
    const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1';
    const mac = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36';
    expect(deviceLabel(iphone, true)).toBe('iPhone app');
    expect(deviceLabel(mac, false)).toBe('Mac (Chrome)');
  });

  it('decodes the server key', () => {
    expect([...urlBase64ToBytes('AQID_-8')]).toEqual([1, 2, 3, 255, 239]);
  });
});

describe('history', () => {
  const ctx: HistoryContext = { name: (id) => ({ c: 'Alex', d: 'Guest', s: 'Slate' })[id], status: () => undefined };
  it('reads reminder events', () => {
    expect(
      verb(ev({ type: 'item.reminder_set', actor_id: 'c', after: { account_id: 'c', before_minutes: 60 } }), ctx),
    ).toBe('set a reminder 1 hour before due');
    expect(
      verb(ev({ type: 'item.reminder_set', actor_id: 'c', after: { account_id: 'd', before_minutes: 0 } }), ctx),
    ).toBe('set a reminder for Guest at the due time');
    expect(verb(ev({ type: 'item.reminder_removed', actor_id: 'c', before: { account_id: 'c' } }), ctx)).toBe(
      'removed a reminder',
    );
    expect(verb(ev({ type: 'item.reminder', actor_id: 's', after: { account_id: 'c', note: 'Call' } }), ctx)).toBe(
      'reminded Alex: Call',
    );
    expect(verb(ev({ type: 'account.digest', actor_id: 's', after: { counts: { overdue: 1 } } }), ctx)).toBe(
      'sent the daily digest: 1 overdue',
    );
  });
});
