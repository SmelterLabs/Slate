// @vitest-environment happy-dom
// The chat store's agent turns ("Agent is working…"): seeded from GET /channels, kept by the
// turn events, and never undone by a channel list fetched before an event. `fetch` is faked.
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Channel } from './chat';
import { chat } from './chat.svelte';
import type { SlateEvent } from './types';

const channel = (over: Partial<Channel> = {}): Channel => ({
  key: 'list:L1',
  kind: 'list',
  name: 'Todo',
  list_id: 'L1',
  section_id: 'S1',
  position: 1,
  unread: 0,
  mentions: 0,
  last_message_id: null,
  last_message_at: null,
  agent_turns: [],
  ...over,
});

const ev = (type: string, after: Record<string, unknown>): SlateEvent => ({
  id: `E${Math.random()}`,
  type,
  item_id: null,
  subject_id: 'M1',
  actor_id: 'A1',
  source: 'api',
  field: null,
  before: null,
  after: { channel: 'list:L1', thread_id: null, ...after },
  session_ref: null,
  at: '2026-11-04T15:00:00Z',
});

function serve(body: unknown) {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      await gate;
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    }),
  );
  return release;
}

afterEach(() => {
  vi.unstubAllGlobals();
  chat.turns = {};
});

describe('chat store agent turns', () => {
  it('seeds from the channel list, then follows the events', async () => {
    const release = serve([channel({ agent_turns: [{ agent_id: 'A1', thread_id: null, state: 'working' }] })]);
    release();
    await chat.refreshChannels();
    expect(chat.turns['list:L1']).toEqual([{ agent_id: 'A1', thread_id: null, state: 'working' }]);

    const done = ev('message.agent_turn', { agent: 'A1', state: 'done', pending: false });
    chat.applyTurn(done);
    chat.applyTurn(done);
    expect(chat.turns['list:L1']).toEqual([]);

    const wake = ev('message.mentioned_agent', { for: ['A1'], queued: [] });
    chat.applyTurn(wake);
    chat.applyTurn(wake);
    expect(chat.turns['list:L1'].map((t) => t.state)).toEqual(['waiting']);
    chat.applyTurn(ev('message.agent_turn', { agent: 'A1', state: 'started' }));
    expect(chat.turns['list:L1'].map((t) => t.state)).toEqual(['working']);
  });

  it('a channel list fetched before a turn event does not undo it', async () => {
    const release = serve([channel({ agent_turns: [{ agent_id: 'A1', thread_id: null, state: 'waiting' }] })]);
    const refresh = chat.refreshChannels();
    chat.applyTurn(ev('message.agent_turn', { agent: 'A1', state: 'started' }));
    release();
    await refresh;
    expect(chat.turns['list:L1'].map((t) => t.state)).toEqual(['working']);
  });
});
