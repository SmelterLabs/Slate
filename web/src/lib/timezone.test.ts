import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { dateFormatter, setWorkspaceTimezone, workspaceFormatter, workspaceTimezone } from './timezone';
import { workspaceDay, parseQuickAdd } from './quickadd';
import { dateInput, dueLabel, shortDate } from './format';
import { chatDay } from './chat';
import { splitWhen } from './when';
import { cardDue } from './cards';
import { stamp } from './activity';
import { workspaceInstant, dayOf } from './views';

const boundary = new Date('2030-01-01T23:30:00Z');
// Each test starts in the sample zone; with no zone given the board falls back to UTC.
beforeEach(() => setWorkspaceTimezone('America/Chicago'));
afterEach(() => setWorkspaceTimezone());

describe('workspace timezone', () => {
  it('changes all pre-imported formatters and natural dates at a UTC day boundary', () => {
    expect(workspaceDay(boundary)).toBe('2030-01-01');
    expect(dayOf(boundary.toISOString())).toBe('2030-01-01');
    expect(splitWhen(boundary.toISOString(), false)).toEqual({ date: '2030-01-01', time: '17:30' });
    setWorkspaceTimezone('Asia/Tokyo');
    expect(workspaceTimezone()).toBe('Asia/Tokyo');
    expect(workspaceDay(boundary)).toBe('2030-01-02');
    expect(dayOf(boundary.toISOString())).toBe('2030-01-02');
    expect(workspaceInstant('2030-01-02', 9).toISOString()).toBe('2030-01-02T00:00:00.000Z');
    expect(dateInput(boundary.toISOString())).toBe('2030-01-02');
    expect(parseQuickAdd('Call today at 9am', boundary).due).toBe('2030-01-02T09:00');
    expect(parseQuickAdd('Call at 8am', boundary).due).toBe('2030-01-03T08:00');
    expect(shortDate(boundary.toISOString(), boundary)).toBe('Jan 2');
    expect(dueLabel(boundary.toISOString(), boundary, false)).toBe('Today 8:30 AM');
    expect(splitWhen(boundary.toISOString(), false)).toEqual({ date: '2030-01-02', time: '08:30' });
    expect(cardDue(boundary.toISOString(), boundary).text).toContain('8:30 am');
    expect(stamp(boundary.toISOString(), boundary)).toBe('8:30 AM');
    expect(chatDay('2030-01-01T00:30:00Z', boundary)).toBe('Yesterday');
    setWorkspaceTimezone('America/Chicago');
    expect(workspaceDay(boundary)).toBe('2030-01-01');
    expect(dueLabel(boundary.toISOString(), boundary, false)).toBe('Today 5:30 PM');
  });

  it('compares activity years in the workspace rather than the device zone', () => {
    setWorkspaceTimezone('Asia/Tokyo');
    expect(stamp('2029-12-31T14:00:00Z', new Date('2029-12-31T23:30:00Z'))).toContain('2029');
  });

  it('caches by zone, preserving explicitly requested rule zones', () => {
    const opts = { hour: 'numeric' } as const;
    const chicago = dateFormatter('en-US', opts);
    const dynamic = workspaceFormatter('en-US', opts);
    expect(dateFormatter('en-US', opts)).toBe(chicago);
    setWorkspaceTimezone('Asia/Tokyo');
    expect(dateFormatter('en-US', opts)).not.toBe(chicago);
    expect(dynamic.format(boundary)).toBe('8 AM');
    expect(dateFormatter('en-US', opts, 'America/Chicago')).toBe(chicago);
    setWorkspaceTimezone('America/Chicago');
    expect(dateFormatter('en-US', opts)).toBe(chicago);
    expect(dynamic.format(boundary)).toBe('5 PM');
  });

  it('defaults before metadata and resets missing or invalid zones safely', () => {
    for (const zone of [undefined, '', 'Not/A_Zone']) {
      setWorkspaceTimezone('Asia/Tokyo');
      setWorkspaceTimezone(zone);
      expect(workspaceTimezone()).toBe('UTC');
      expect(workspaceDay(boundary)).toBe('2030-01-01');
    }
  });
});
