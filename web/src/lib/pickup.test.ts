import { describe, expect, it } from 'vitest';
import { item } from './fixtures';
import { pickupSince, pickupStatus, scheduledRun, scheduledRunLabel, workingItems } from './pickup';
import type { AgentEntry, AgentFlag } from './types';

const now = new Date('2026-10-28T15:20:00Z');
const at = '2026-10-28T15:13:00Z';
const agent2 = { id: 'P', handle: 'agent2', display_name: 'Agent 2', kind: 'agent' as const };
const flag: AgentFlag = { state: 'working', by: 'P', at };
const waiting = item({ id: 'wait', due_at: at, assignees: [agent2] });
const working = item({ id: 'work', agent: flag, assignees: [agent2] });
const roster: AgentEntry[] = [{
  account: agent2, live_streams: 1, online: true, last_seen_at: at,
  last_seen_via: 'stream', working: [], open_assigned: 4, events_24h: 0,
  last_event_at: at, throttled: 0,
}];
const since = Date.parse(at);

function status(items = [working], entries = roster, rosterAt: number | null = now.getTime()) {
  return pickupStatus(waiting, since, ['P'], workingItems(items, now), entries, rosterAt, now);
}

describe('pickup evidence', () => {
  it('work on another card is no reason to wait: agents run several cards at once', () => {
    const cards = [working, ...['a', 'b', 'c'].map((id) => ({ ...waiting, id }))];
    const work = workingItems(cards, now);
    expect(pickupSince(working, undefined, now)).toBeUndefined();
    for (const card of cards.slice(1)) {
      expect(pickupSince(card, undefined, now)).toBe(since);
      const result = pickupStatus(card, since, ['P'], work, roster, now.getTime(), now);
      expect(result).toMatchObject({ state: 'unclaimed', warning: true });
      expect(result.text).not.toMatch(/another card|queued|after|next|slot/i);
    }
  });

  it('warns on an unexplained wait even with a connected agent', () => {
    expect(status([])).toMatchObject({ state: 'unclaimed', warning: true, text: 'Not picked up yet. Check agent status.' });
  });

  it('a fresh disconnected snapshot is actionable even if an older working report remains', () => {
    expect(status([working], [{ ...roster[0], live_streams: 0 }])).toMatchObject({ state: 'no-runner', warning: true });
    expect(status([working], [{ ...roster[0], live_streams: 0 }]).text).not.toContain('offline');
  });

  it('unknown or stale presence does not assert disconnection', () => {
    expect(status([working], [], null).state).toBe('unclaimed');
    expect(status([], [], null).state).toBe('unclaimed');
    expect(status([working], [{ ...roster[0], live_streams: 0 }], since).state).toBe('unclaimed');
  });

  it('a partial roster cannot declare all assignees disconnected', () => {
    const result = pickupStatus(waiting, since, ['P', 'missing'], {}, [{ ...roster[0], live_streams: 0 }], now.getTime(), now);
    expect(result.state).toBe('unclaimed');
  });

  it.each(['blocked', 'review'] as const)('a %s report elsewhere is not working', (state) => {
    expect(status([{ ...working, agent: { ...flag, state } }]).state).toBe('unclaimed');
  });

  it('ignores self, other assignees, archived, closed and stale work', () => {
    for (const other of [
      { ...working, id: waiting.id },
      { ...working, agent: { ...flag, by: 'someone-else' } },
      { ...working, archived_at: at },
      { ...working, status: { ...working.status!, kind: 'done' as const } },
      { ...working, agent: { ...flag, at: '2026-10-27T15:13:00Z' } },
    ]) expect(status([other]).state).toBe('unclaimed');
  });

  it('new requests get a neutral grace interval, not a receipt claim', () => {
    expect(pickupStatus(waiting, now.getTime(), ['P'], {}, roster, now.getTime(), now)).toMatchObject({
      state: 'requested', warning: false, text: 'Waiting for pickup…',
    });
  });
});

describe('initial-load due gate', () => {
  it('allows a newly filed overdue card time to be dispatched', () => {
    const fresh = { ...waiting, created_at: now.toISOString() };
    const requestedAt = pickupSince(fresh, undefined, now)!;
    expect(requestedAt).toBe(now.getTime());
    expect(pickupStatus(fresh, requestedAt, ['P'], {}, roster, now.getTime(), now).warning).toBe(false);
  });

  it('uses 06:00 Chicago for date-only tasks, including across DST', () => {
    const dayOnly = item({ due_at: '2026-11-02T23:00:00Z', due_date_only: true });
    expect(pickupSince(dayOnly, undefined, new Date('2026-11-02T11:59:59Z'))).toBeUndefined();
    expect(pickupSince(dayOnly, undefined, new Date('2026-11-02T12:00:00Z'))).toBe(Date.parse('2026-11-02T12:00:00Z'));
  });

  it('does not invent pending pickup for future, undated, blocked, waiting, closed or archived cards', () => {
    for (const card of [
      { ...waiting, due_at: '2026-10-29T15:13:00Z' },
      { ...waiting, due_at: null },
      { ...waiting, start_at: '2026-10-29T15:13:00Z' },
      { ...waiting, blocked: true },
      { ...waiting, waiting_on_account_id: 'person' },
      { ...waiting, archived_at: at },
      { ...waiting, stalled: true },
      { ...waiting, status: { ...waiting.status!, kind: 'cancelled' as const } },
    ]) expect(pickupSince(card, undefined, now)).toBeUndefined();
    expect(pickupSince({ ...waiting, stalled: true }, now.getTime(), now)).toBe(now.getTime());
  });

  it('does not overwrite working, review or blocked reports with inferred waiting', () => {
    for (const state of ['working', 'review', 'blocked'] as const) {
      expect(pickupSince({ ...waiting, agent: { ...flag, state } }, undefined, now)).toBeUndefined();
      expect(pickupSince({ ...waiting, agent: { ...flag, state } }, since - 1000, now)).toBeUndefined();
    }
  });

  it('an explicit request can override a future due date and an older nonworking report', () => {
    expect(pickupSince({ ...waiting, due_at: null }, now.getTime(), now)).toBe(now.getTime());
    expect(pickupSince({ ...waiting, agent: { ...flag, state: 'review' } }, now.getTime(), now)).toBe(now.getTime());
    expect(pickupSince(working, now.getTime(), now)).toBeUndefined();
  });

  it('does not infer due-gate pickup for a parked card', () => {
    expect(pickupSince({ ...waiting, parked_until: '2026-11-10', parked_note: 'Vendor' }, undefined, now)).toBeUndefined();
  });
});

describe('scheduled run indicator', () => {
  // 2026-11-23 00:05 CST (06:05 UTC).
  const afterMidnight = new Date('2026-11-23T06:05:00Z');
  // 2026-11-22 19:39 CST (2026-11-23 01:39 UTC).
  const eveningBefore = new Date('2026-11-23T01:39:00Z');
  // Date-only Mon, Nov 23 (stored as 17:00 CST = 23:00 UTC); run gate is 06:00 CST (12:00 UTC).
  const dueToday = item({
    id: 'due-today',
    due_at: '2026-11-23T23:00:00Z',
    due_date_only: true,
    assignees: [agent2],
  });

  it('shows 6:00 AM today when a date-only card is due today before 06:00', () => {
    const res = scheduledRun(dueToday, undefined, afterMidnight);
    expect(res).not.toBeNull();
    expect(res?.at).toBe(Date.parse('2026-11-23T12:00:00Z'));
    expect(res?.text).toBe('Runs at 6:00 AM');
    expect(res?.title).toContain('6:00 AM');
  });

  it('shows tomorrow at 6:00 AM the evening before and a calendar date further out', () => {
    expect(scheduledRun(dueToday, undefined, eveningBefore)?.text).toBe('Runs tomorrow at 6:00 AM');
    const nextWeek = { ...dueToday, due_at: '2026-11-26T23:00:00Z' };
    expect(scheduledRun(nextWeek, undefined, afterMidnight)?.text).toBe('Runs Nov 26 at 6:00 AM');
  });

  it('formats tomorrow accurately across a 25-hour DST fall-back day', () => {
    // 2026-11-01 00:30 CDT (05:30 UTC): fall-back day in Chicago. Next day 06:00 CST = 2026-11-02T12:00:00Z.
    const dstMidnight = new Date('2026-11-01T05:30:00Z');
    expect(scheduledRunLabel(Date.parse('2026-11-02T12:00:00Z'), dstMidnight)).toBe('Runs tomorrow at 6:00 AM');
  });

  it('uses the exact due time for timed due dates and the later of due gate and start_at', () => {
    const timed = item({
      due_at: '2026-11-23T21:30:00Z', // 3:30 PM CST
      due_date_only: false,
      assignees: [agent2],
    });
    expect(scheduledRun(timed, undefined, afterMidnight)?.text).toBe('Runs at 3:30 PM');

    const withStart = { ...dueToday, start_at: '2026-11-23T16:00:00Z' }; // 10:00 AM CST > 6:00 AM CST
    const res = scheduledRun(withStart, undefined, afterMidnight);
    expect(res?.text).toBe('Runs at 10:00 AM');
    expect(res?.title).toContain('start date');

    const startOnly = item({ due_at: null, start_at: '2026-11-24T15:00:00Z', assignees: [agent2] }); // 9:00 AM CST tomorrow
    expect(scheduledRun(startOnly, undefined, afterMidnight)?.text).toBe('Runs tomorrow at 9:00 AM');
  });

  it('returns null once the run time has passed, on undated cards, or when overridden or inactive', () => {
    const atSix = new Date('2026-11-23T12:00:00Z');
    expect(scheduledRun(dueToday, undefined, atSix)).toBeNull();
    expect(scheduledRun({ ...dueToday, due_at: null }, undefined, afterMidnight)).toBeNull();
    expect(scheduledRun(dueToday, afterMidnight.getTime(), afterMidnight)).toBeNull();
    for (const card of [
      { ...dueToday, parked_until: '2026-11-25', parked_note: 'Vendor' },
      { ...dueToday, blocked: true },
      { ...dueToday, waiting_on_account_id: 'me' },
      { ...dueToday, stalled: true },
      { ...dueToday, archived_at: at },
      { ...dueToday, status: { ...dueToday.status!, kind: 'done' as const } },
      { ...dueToday, agent: flag },
      { ...dueToday, open_asks: ['P'] }, // open Ask to the assigned agent bypasses the due gate
    ]) expect(scheduledRun(card, undefined, afterMidnight)).toBeNull();
  });

  it('still respects a future start_at even when an assigned agent has an open Ask', () => {
    const startWithAsk = {
      ...dueToday,
      start_at: '2026-11-23T16:00:00Z',
      open_asks: ['P'],
    };
    expect(scheduledRun(startWithAsk, undefined, afterMidnight)?.text).toBe('Runs at 10:00 AM');
  });
});
