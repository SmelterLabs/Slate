import { afterEach, describe, expect, it, vi } from 'vitest';
import { agents, isOnline, toFeed } from './agents.svelte';
import { get } from './api';
import { ev } from './fixtures';
import type { AgentEntry } from './types';
vi.mock('./api', () => ({ get: vi.fn() }));

afterEach(() => {
  agents.resetRoster();
  vi.restoreAllMocks();
});

it('a concurrent presence refresh does not leave the Agents page loading forever', async () => {
  let resolveTail!: (value: { events: never[] }) => void;
  vi.mocked(get).mockImplementation(async (path) => {
    if (path.startsWith('/events')) return new Promise((resolve) => { resolveTail = resolve; });
    return [];
  });
  const load = agents.load();
  await agents.refreshRoster();
  resolveTail({ events: [] });
  await load;
  expect(agents.loaded).toBe(true);
});

it('a roster response from a signed-out scope cannot restore presence', async () => {
  let resolve!: (value: never[]) => void;
  vi.mocked(get).mockImplementation(() => new Promise((done) => { resolve = done; }));
  const refresh = agents.refreshRoster();
  agents.resetRoster();
  resolve([]);
  await refresh;
  expect(agents.rosterAt).toBeNull();
});
const AT = '2026-10-23T22:00:00.000000Z';

describe('toFeed', () => {
  it('newest first, without due-time noise or the comment a report echoes', () => {
    const report = ev({ type: 'item.reported', at: AT, after: { state: 'working', note: 'On it' } });
    const echo = ev({ type: 'item.commented', at: AT, after: { body_md: '**Working** · On it' } });
    const due = ev({ type: 'item.due_soon', at: AT });
    const later = ev({ type: 'item.commented', at: '2026-10-23T22:05:00.000000Z' });
    expect(toFeed([report, echo, due, later]).map((e) => e.id)).toEqual([later.id, report.id]);
  });

  it("keeps a comment on another item or by someone else", () => {
    const report = ev({ type: 'item.reported', at: AT });
    const other = ev({ type: 'item.commented', at: AT, item_id: 'I2' });
    expect(toFeed([report, other])).toHaveLength(2);
  });
});

describe('isOnline', () => {
  const base: AgentEntry = {
    account: { id: 'A', handle: 'agent', display_name: 'Agent', kind: 'agent' },
    last_seen_at: null,
    last_seen_via: null,
    online: false,
    live_streams: 0,
    working: [],
    open_assigned: 0,
    events_24h: 0,
    last_event_at: null,
    throttled: 0,
  };
  const now = new Date('2026-10-23T22:10:00Z');

  it('a live stream or a request in the last two minutes', () => {
    expect(isOnline({ ...base, live_streams: 1 }, now)).toBe(true);
    expect(isOnline({ ...base, last_seen_at: '2026-10-23T22:09:00Z' }, now)).toBe(true);
    expect(isOnline({ ...base, last_event_at: '2026-10-23T22:00:00Z' }, now)).toBe(false);
    expect(isOnline(base, now)).toBe(false);
  });
});

