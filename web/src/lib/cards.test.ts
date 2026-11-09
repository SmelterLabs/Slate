import { describe, expect, it } from 'vitest';
import { cardDue, isCollapsed, tagColor, textOn } from './cards';

// Friday Oct 23 2026, 10:00 CDT.
const FRI = new Date('2026-10-23T15:00:00Z');

describe('cardDue', () => {
  it('today, with the time unless it is the 5 pm default', () => {
    expect(cardDue('2026-10-23T21:00:00Z', FRI)).toEqual({ text: 'Today, 4:00 pm', tone: 'today' });
    expect(cardDue('2026-10-23T22:00:00Z', FRI)).toEqual({ text: 'Today', tone: 'today' });
  });

  it('earlier today is overdue', () => {
    expect(cardDue('2026-10-23T13:00:00Z', FRI)).toEqual({ text: 'Today, 8:00 am', tone: 'overdue' });
  });

  it('tomorrow, then weekday names for the rest of the week, then dates', () => {
    expect(cardDue('2026-10-24T22:00:00Z', FRI).text).toBe('Tomorrow');
    expect(cardDue('2026-10-25T09:00:00Z', FRI)).toEqual({ text: 'Sun, 4:00 am', tone: 'soon' });
    expect(cardDue('2026-10-29T22:00:00Z', FRI).text).toBe('Thu');
    expect(cardDue('2026-10-30T22:00:00Z', FRI)).toEqual({ text: 'Oct 30', tone: 'later' });
    expect(cardDue('2027-01-05T22:00:00Z', FRI).text).toBe('Jan 5, 2027');
  });

  it('past days: Yesterday, N days ago, then the date with how late; closed is never red', () => {
    expect(cardDue('2026-10-22T22:00:00Z', FRI)).toEqual({ text: 'Yesterday', tone: 'overdue' });
    expect(cardDue('2026-10-19T22:00:00Z', FRI).text).toBe('4 days ago');
    expect(cardDue('2026-10-16T22:00:00Z', FRI).text).toBe('Oct 16 · 7d late');
    expect(cardDue('2026-08-02T22:00:00Z', FRI).text).toBe('Aug 2 · 82d late');
    expect(cardDue('2025-08-02T22:00:00Z', FRI).text).toBe('Aug 2, 2025 · 447d late');
    expect(cardDue('2026-08-02T22:00:00Z', FRI, true)).toEqual({ text: 'Aug 2', tone: 'done' });
  });
});

describe('tag colours', () => {
  it('uses the tag colour, else a stable palette pick', () => {
    const tags = { home: { id: 't1', name: 'home', color: '#ffffff' } };
    expect(tagColor('home', tags)).toEqual({ bg: '#ffffff', fg: '#1d2127' });
    expect(tagColor('agent', {})).toEqual(tagColor('agent', {}));
    expect(tagColor('agent', {}).bg).toMatch(/^#[0-9a-f]{6}$/);
  });

  it('picks readable text', () => {
    expect(textOn('#000000')).toBe('#fff');
    expect(textOn('#f2c200')).toBe('#1d2127');
    expect(textOn('nonsense')).toBe('#fff');
  });
});

describe('column collapse', () => {
  it('empty columns collapse unless the viewer opened them', () => {
    expect(isCollapsed('mon', 0, {}, true)).toBe(true);
    expect(isCollapsed('mon', 0, {}, false)).toBe(false);
    expect(isCollapsed('mon', 3, {}, true)).toBe(false);
    expect(isCollapsed('mon', 0, { mon: false }, true)).toBe(false);
    expect(isCollapsed('mon', 5, { mon: true }, true)).toBe(true);
  });
});
