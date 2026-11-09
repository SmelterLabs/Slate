import { describe, expect, it } from 'vitest';
import { workspaceDay, matchList, parseQuickAdd } from './quickadd';

// Thursday 2026-10-22, 15:00 in Chicago (CDT, UTC-5).
const THU = new Date('2026-10-22T20:00:00Z');

describe('parseQuickAdd', () => {
  it('returns a plain title untouched', () => {
    expect(parseQuickAdd('Call the plumber', THU)).toEqual({
      title: 'Call the plumber',
      tags: [],
      assignees: [],
      priority: null,
      due: null,
      start: null,
      list: null,
      rrule: null,
    });
  });

  it('pulls every token out, wherever it sits', () => {
    const r = parseQuickAdd('#home Fix  the gutter !high @agent due:tomorrow #Chores', THU);
    expect(r).toEqual({
      title: 'Fix the gutter',
      tags: ['home', 'chores'],
      assignees: ['agent'],
      priority: 'high',
      due: '2026-10-23',
      start: null,
      list: null,
      rrule: null,
    });
  });

  it.each([
    ['!urgent', 'urgent'],
    ['!HIGH', 'high'],
    ['!low', 'low'],
  ])('priority %s', (tok, want) => {
    expect(parseQuickAdd(`x ${tok}`, THU).priority).toBe(want);
  });

  it('leaves unknown bang words in the title', () => {
    const r = parseQuickAdd('Ship it !now', THU);
    expect(r.title).toBe('Ship it !now');
    expect(r.priority).toBeNull();
  });

  it.each([
    ['due:today', '2026-10-22'],
    ['due:tomorrow', '2026-10-23'],
    ['due:friday', '2026-10-23'],
    ['due:fri', '2026-10-23'],
    ['due:thursday', '2026-10-22'], // same weekday = today
    ['due:wednesday', '2026-10-28'],
    ['due:monday', '2026-10-26'],
    ['due:2026-11-01', '2026-11-01'],
    ['DUE:Tomorrow', '2026-10-23'],
  ])('%s -> %s', (tok, want) => {
    expect(parseQuickAdd(`x ${tok}`, THU).due).toBe(want);
  });

  it('keeps an unparseable due token in the title', () => {
    const r = parseQuickAdd('x due:someday', THU);
    expect(r.due).toBeNull();
    expect(r.title).toBe('x due:someday');
  });

  it('uses the Chicago calendar day, not UTC', () => {
    // 03:30Z on the 23rd is still 22:30 on the 22nd in Chicago.
    expect(parseQuickAdd('x due:today', new Date('2026-10-23T03:30:00Z')).due).toBe('2026-10-22');
  });

  it('dedupes tags and assignees and ignores a bare # or @', () => {
    const r = parseQuickAdd('a #x #X @alex @Alex # @', THU);
    expect(r.tags).toEqual(['x']);
    expect(r.assignees).toEqual(['alex']);
    expect(r.title).toBe('a # @');
  });

  it('gives an empty title when only tokens were typed', () => {
    expect(parseQuickAdd('#a !high', THU).title).toBe('');
  });
});

describe('natural language', () => {
  const due = (text: string, now = THU) => parseQuickAdd(text, now).due;
  const title = (text: string) => parseQuickAdd(text, THU).title;

  it('reads the line from the brief', () => {
    expect(parseQuickAdd('Call the bank tomorrow 4pm #personal !high', THU)).toMatchObject({
      title: 'Call the bank',
      tags: ['personal'],
      priority: 'high',
      due: '2026-10-23T16:00',
    });
  });

  it.each([
    ['x today', '2026-10-22'],
    ['x tonight', '2026-10-22T20:00'],
    ['x tomorrow', '2026-10-23'],
    ['x tmrw', '2026-10-23'],
    ['x friday', '2026-10-23'],
    ['x monday', '2026-10-26'],
    ['x on mon', '2026-10-26'],
    ['x by fri', '2026-10-23'],
    ['x due wed', '2026-10-28'],
    ['x this friday', '2026-10-23'],
    ['x next friday', '2026-10-30'],
    ['x next mon', '2026-10-26'],
    ['x next week', '2026-10-26'],
    ['x next month', '2026-11-01'],
    ['x this weekend', '2026-10-24'],
    ['x eow', '2026-10-23'],
    ['x end of month', '2026-10-31'],
    ['x in 3 days', '2026-10-25'],
    ['x in a week', '2026-10-29'],
    ['x in 2 months', '2026-12-22'],
    ['x in 2 hours', '2026-10-22T17:00'],
    ['x jan 5', '2027-01-05'],
    ['x November 1st', '2026-11-01'],
    ['x 5 dec', '2026-12-05'],
    ['x 11/2', '2026-11-02'],
    ['x 1/15/2027', '2027-01-15'],
    ['x 2026-11-01', '2026-11-01'],
    ['x friday at 9am', '2026-10-23T09:00'],
    ['x friday 4:30pm', '2026-10-23T16:30'],
    ['x tomorrow 4 pm', '2026-10-23T16:00'],
    ['x tomorrow at 4', '2026-10-23T16:00'],
    ['x tomorrow noon', '2026-10-23T12:00'],
    ['x monday 16:00', '2026-10-26T16:00'],
    ['x at 5pm', '2026-10-22T17:00'], // still ahead today (it is 15:00)
    ['x 9am', '2026-10-23T09:00'], // already past, so tomorrow
    ['x due:fri 8am', '2026-10-23T08:00'],
  ])('%s -> %s', (text, want) => {
    expect(due(text)).toBe(want);
    expect(title(text)).toBe('x');
  });

  it('keeps ordinary words that only look like dates', () => {
    expect(parseQuickAdd('Put books on the shelf', THU)).toMatchObject({
      title: 'Put books on the shelf',
      due: null,
    });
    expect(title('Buy 3 apples at the store')).toBe('Buy 3 apples at the store');
    expect(title('Wash the car sat')).toBe('Wash the car sat');
    expect(due('Room 4 cleanup')).toBeNull();
  });

  it('keeps a backslashed word as typed', () => {
    expect(parseQuickAdd('Watch \\friday night lights', THU)).toMatchObject({
      title: 'Watch friday night lights',
      due: null,
    });
  });

  it('the last date wins', () => {
    expect(due('x monday tuesday')).toBe('2026-10-27');
  });

  it('reads a start date', () => {
    expect(parseQuickAdd('x start:monday due:friday', THU)).toMatchObject({
      start: '2026-10-26',
      due: '2026-10-23',
    });
  });

  it.each([
    ['!1', 'urgent'],
    ['!2', 'high'],
    ['!3', 'normal'],
    ['!normal', 'normal'],
    ['!4', 'low'],
  ])('priority %s', (tok, want) => {
    expect(parseQuickAdd(`x ${tok}`, THU).priority).toBe(want);
  });

  it('reads a target list', () => {
    expect(parseQuickAdd('Pay water ~bills', THU)).toMatchObject({ title: 'Pay water', list: 'bills' });
    expect(parseQuickAdd('Pay water ~Example-Project', THU).list).toBe('Example-Project');
  });
});

describe('repeats', () => {
  const rep = (text: string) => parseQuickAdd(text, THU);

  it.each([
    ['x daily', 'FREQ=DAILY', null],
    ['x every day', 'FREQ=DAILY', null],
    ['x every 2 days', 'FREQ=DAILY;INTERVAL=2', null],
    ['x weekly', 'FREQ=WEEKLY', null],
    ['x every other week', 'FREQ=WEEKLY;INTERVAL=2', null],
    ['x monthly', 'FREQ=MONTHLY', null],
    ['x every year', 'FREQ=YEARLY', null],
    ['x every weekday', 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR', null],
    ['x every monday', 'FREQ=WEEKLY;BYDAY=MO', '2026-10-26'],
    ['x every mon and thu', 'FREQ=WEEKLY;BYDAY=MO,TH', '2026-10-22'],
    ['x every tue, fri', 'FREQ=WEEKLY;BYDAY=TU,FR', '2026-10-23'],
    ['x every other friday', 'FREQ=WEEKLY;INTERVAL=2;BYDAY=FR', '2026-10-23'],
    ['x every 15th', 'FREQ=MONTHLY;BYMONTHDAY=15', '2026-11-15'],
    ['x every monday at 9am', 'FREQ=WEEKLY;BYDAY=MO', '2026-10-26T09:00'],
    ['x every day 6pm', 'FREQ=DAILY', '2026-10-22T18:00'],
    ['x weekly starting friday', 'FREQ=WEEKLY', null],
  ])('%s', (text, rrule, due) => {
    const r = rep(text);
    expect(r.rrule).toBe(rrule);
    if (due !== null) expect(r.due).toBe(due);
    if (!text.includes('starting')) expect(r.title).toBe('x');
  });

  it('a typed date sets the first occurrence', () => {
    expect(rep('Trash every week from friday')).toMatchObject({ rrule: 'FREQ=WEEKLY' });
    expect(rep('Trash monthly nov 3')).toMatchObject({ rrule: 'FREQ=MONTHLY', due: '2026-11-03' });
  });

  it('leaves "every" alone when nothing follows it', () => {
    expect(rep('Read every page')).toMatchObject({ rrule: null, title: 'Read every page' });
  });
});

describe('workspaceDay', () => {
  it('formats a Chicago calendar date', () => {
    expect(workspaceDay(new Date('2026-03-08T07:59:00Z'))).toBe('2026-03-08');
    expect(workspaceDay(new Date('2026-01-01T05:59:00Z'))).toBe('2025-12-31');
  });
});

describe('matchList', () => {
  const lists = [
    { name: 'Example Project', slug: 'example-project' },
    { name: 'Bills', slug: 'bills' },
    { name: 'Billing old', slug: 'billing-old', archived_at: '2026-01-01T00:00:00Z' },
    { name: 'Home projects', slug: 'home-projects' },
  ];
  it.each([
    ['bills', 'Bills'],
    ['Bill', 'Bills'],
    ['example-project', 'Example Project'],
    ['exampleproject', 'Example Project'],
    ['projects', 'Home projects'],
    ['billing', null],
    ['nope', null],
  ])('%s -> %s', (hint, want) => {
    expect(matchList(hint, lists)?.name ?? null).toBe(want);
  });
});
