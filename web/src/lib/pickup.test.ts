import { describe, expect, it } from 'vitest';
import { item } from './fixtures';
import { pickupSince, pickupStatus, workingItems } from './pickup';
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
      { ...waiting, status: { ...waiting.status!, kind: 'cancelled' as const } },
      { ...waiting, kind: 'doc' as const },
    ]) expect(pickupSince(card, undefined, now)).toBeUndefined();
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
});
