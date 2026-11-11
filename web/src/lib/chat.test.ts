import { describe, expect, it } from 'vitest';
import {
  agentChats,
  applyTurnEvent,
  badge,
  channelIcon,
  channelKey,
  chatChannelOf,
  chatHref,
  dayLabel,
  hashPicks,
  insertHashPick,
  insertTaskRef,
  mentionsIn,
  pastedName,
  renderBody,
  rows,
  taskRefAt,
  threadOf,
  turnsIn,
  upsert,
  type AgentTurn,
  type ChatMessage,
} from './chat';

const msg = (id: string, over: Partial<ChatMessage> = {}): ChatMessage => ({
  id,
  channel: 'general',
  parent_id: null,
  author_id: 'A1',
  body_md: 'hi',
  mentions: [],
  item_id: null,
  created_at: '2026-10-23T15:00:00Z',
  edited_at: null,
  deleted_at: null,
  reactions: [],
  reply_count: 0,
  last_reply_at: null,
  reply_author_ids: [],
  linked_items: [],
  files: [],
  ...over,
});

describe('channel addresses', () => {
  it('names channels and reads them back from the hash', () => {
    expect(channelKey('list', 'L1')).toBe('list:L1');
    expect(channelKey('general')).toBe('general');
    expect(chatHref('list:L1', 'm1')).toBe('#/chat/list:L1?thread=m1');
    expect(chatChannelOf('#/chat/list:L1?thread=m1')).toBe('list:L1');
    expect(chatChannelOf('#/chat')).toBeNull();
    expect(threadOf('#/chat/general?thread=m1')).toBe('m1');
    expect(threadOf('#/chat/general')).toBeNull();
  });

  it("an agent's chat has its own key and a robot", () => {
    expect(chatChannelOf('#/chat/agent:A1')).toBe('agent:A1');
    expect(channelIcon({ key: 'agent:A1', kind: 'agent' })).toBe('robot');
    expect(channelIcon({ key: 'agent:A1' })).toBe('robot');
    expect(channelIcon({ key: 'section:S1', kind: 'section' })).toBe('folder');
    expect(channelIcon({ key: 'list:L1', kind: 'list' })).toBe('hash');
    expect(channelIcon(undefined)).toBe('hash');
  });

  it('picks the agents chats out of the channel list with their unread', () => {
    const chans = [
      { key: 'general', kind: 'general', unread: 4, mentions: 0 },
      { key: 'agent:A1', kind: 'agent', unread: 2, mentions: 2 },
      { key: 'agent:A2', kind: 'agent', unread: 1, mentions: 0 },
      { key: 'list:L1', kind: 'list', unread: 9, mentions: 1 },
    ];
    const got = agentChats(chans);
    expect(got.chats.map((c) => c.key)).toEqual(['agent:A1', 'agent:A2']);
    expect(got.unread).toBe(3);
  });
});

describe('message lists', () => {
  it('upserts in id order', () => {
    const list = [msg('a'), msg('c')];
    expect(upsert(list, msg('b')).map((m) => m.id)).toEqual(['a', 'b', 'c']);
    expect(upsert(list, msg('c', { body_md: 'new' }))[1].body_md).toBe('new');
  });

  it('folds one author within five minutes and splits days', () => {
    const now = new Date('2026-10-23T18:00:00Z');
    const r = rows(
      [
        msg('1', { created_at: '2026-10-22T15:00:00Z' }),
        msg('2', { created_at: '2026-10-23T15:00:00Z' }),
        msg('3', { created_at: '2026-10-23T15:03:00Z' }),
        msg('4', { created_at: '2026-10-23T15:04:00Z', author_id: 'B' }),
        msg('5', { created_at: '2026-10-23T15:20:00Z', author_id: 'B' }),
      ],
      now,
    );
    expect(r.map((x) => (x.kind === 'day' ? x.label : `${x.message.id}${x.head ? '*' : ''}`))).toEqual([
      'Yesterday',
      '1*',
      'Today',
      '2*',
      '3',
      '4*',
      '5*',
    ]);
    expect(dayLabel('2026-10-20T15:00:00Z', now)).toBe('Tuesday, October 20');
  });
});

describe('bodies', () => {
  it('highlights mentions and names linked tasks', () => {
    const html = renderBody(
      'ask @alex about #Ab12Cd34Ef56 and #nope',
      (h) => h === 'alex',
      'alex',
      (id) => (id === 'Ab12Cd34Ef56' ? 'Fix <gutter>' : undefined),
    );
    expect(html).toContain('<span class="mention me">@alex</span>');
    expect(html).toContain('data-task="Ab12Cd34Ef56">#Fix &lt;gutter&gt;</a>');
    expect(html).toContain('#nope');
  });

  it('finds the task reference being typed and inserts it', () => {
    expect(taskRefAt('see #gut', 8)).toEqual({ start: 4, query: 'gut' });
    expect(taskRefAt('a#b', 3)).toBeNull();
    expect(insertTaskRef('see #gut', 4, 8, 'Ab12Cd34Ef56')).toEqual({ text: 'see #Ab12Cd34Ef56 ', caret: 18 });
  });

  it('renders page and list tokens with the live title, or a plain word when the reader cannot see them', () => {
    const refs = {
      doc: (id: string) => (id === 'Dd12Cd34Ef56' ? { title: 'Oven <manual>', icon: '🍳' } : undefined),
      list: (id: string) => (id === 'Ll12Cd34Ef56' ? 'Kitchen & co' : undefined),
    };
    const html = renderBody('see [[doc:Dd12Cd34Ef56]] in [[list:Ll12Cd34Ef56]]', () => false, undefined, () => undefined, refs);
    expect(html).toContain('<a class="doc-ref" href="#/docs/Dd12Cd34Ef56">🍳 Oven &lt;manual&gt;</a>');
    expect(html).toContain('<a class="list-ref" href="#/list/Ll12Cd34Ef56">Kitchen &amp; co</a>');
    const hidden = renderBody('see [[doc:Xx12Cd34Ef56]] in [[list:Yy12Cd34Ef56]]', () => false, undefined, () => undefined, refs);
    expect(hidden).toContain('<a class="doc-ref" href="#/docs/Xx12Cd34Ef56">📄 a page</a>');
    expect(hidden).toContain('<a class="list-ref" href="#/list/Yy12Cd34Ef56">a list</a>');
    expect(hidden).not.toContain('Oven');
  });

  it('lists mentions and badge text', () => {
    expect(mentionsIn('@alex and @Agent, @alex')).toEqual(['alex', 'agent']);
    expect([badge(0), badge(5), badge(120)]).toEqual(['', '5', '99+']);
  });
});

describe('the # picker', () => {
  const tasks = [
    { id: 'T1', title: 'Fix the oven', list_id: 'L1', archived_at: null, updated_at: '2026-10-20T00:00:00Z' },
    { id: 'T2', title: 'Oven warranty', list_id: 'L2', archived_at: null, updated_at: '2026-10-21T00:00:00Z' },
    { id: 'T3', title: 'Old oven', list_id: 'L1', archived_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-22T00:00:00Z' },
    { id: 'T4', title: 'Walk the dog', list_id: 'L1', archived_at: null, updated_at: '2026-10-23T00:00:00Z' },
  ];
  const docs = [
    { id: 'D1', title: 'Oven manual', icon: '🍳', list_id: 'L2', section_id: null, archived_at: null },
    { id: 'D2', title: 'Kitchen notes', icon: null, list_id: null, section_id: 'S1', archived_at: null },
    { id: 'D3', title: 'Oven (archived)', icon: null, list_id: null, section_id: null, archived_at: '2026-10-01T00:00:00Z' },
  ];
  const lists = [
    { id: 'L1', name: 'Kitchen', section_id: 'S1', position: 0, archived_at: null },
    { id: 'L2', name: 'Garage', section_id: 'S2', position: 1, archived_at: null },
    { id: 'L3', name: 'Old kitchen', section_id: 'S1', position: 2, archived_at: '2026-10-01T00:00:00Z' },
  ];
  const names = { listName: (id: string) => lists.find((l) => l.id === id)?.name, sectionName: (id: string) => ({ S1: 'Home', S2: 'Cars' })[id] };
  const here = { listId: 'L1', sectionId: 'S1' };

  it('groups tasks, pages and lists that match the title, skipping archived ones', () => {
    const groups = hashPicks('oven', { tasks, docs, lists, here, ...names });
    expect(groups.map((g) => g.label)).toEqual(['Tasks', 'Pages']);
    // The current list's task first, then by recency; archived tasks and pages are left out.
    expect(groups[0].picks.map((p) => p.id)).toEqual(['T1', 'T2']);
    expect(groups[1].picks).toEqual([{ kind: 'doc', id: 'D1', title: 'Oven manual', icon: '🍳', sub: 'Garage', index: 2 }]);
  });

  it('offers lists by name, the current section first, and everything on an empty query', () => {
    const groups = hashPicks('kit', { tasks, docs, lists, here, ...names });
    expect(groups.map((g) => g.label)).toEqual(['Pages', 'Lists']);
    expect(groups[1].picks).toEqual([{ kind: 'list', id: 'L1', title: 'Kitchen', sub: 'Home', index: 1 }]);
    const all = hashPicks('', { tasks, docs, lists, here, ...names });
    expect(all.map((g) => [g.label, g.picks.length])).toEqual([['Tasks', 3], ['Pages', 2], ['Lists', 2]]);
    expect(all[2].picks.map((p) => p.id)).toEqual(['L1', 'L2']);
    expect(all.flatMap((g) => g.picks).map((p) => p.index)).toEqual([0, 1, 2, 3, 4, 5, 6]);
  });

  it('inserts the token each kind renders from', () => {
    expect(insertHashPick('see #ov', 4, 7, { kind: 'task', id: 'T1' })).toEqual({ text: 'see #T1 ', caret: 8 });
    expect(insertHashPick('see #ov', 4, 7, { kind: 'doc', id: 'D1' })).toEqual({ text: 'see [[doc:D1]] ', caret: 15 });
    expect(insertHashPick('see #ov', 4, 7, { kind: 'list', id: 'L1' })).toEqual({ text: 'see [[list:L1]] ', caret: 16 });
  });
});

describe('agent turns (applyTurnEvent)', () => {
  const wake = (over: Record<string, unknown> = {}) => ({
    type: 'message.mentioned_agent',
    at: '2026-11-04T15:00:00Z',
    after: { channel: 'list:L1', thread_id: null, for: ['A1'], queued: [], ...over },
  });
  const turn = (state: string, over: Record<string, unknown> = {}) => ({
    type: 'message.agent_turn',
    at: '2026-11-04T15:00:05Z',
    after: { channel: 'list:L1', thread_id: null, agent: 'A1', state, pending: false, ...over },
  });
  const states = (t: AgentTurn[]) => t.map((x) => `${x.agent_id}/${x.thread_id ?? '-'}/${x.state}`);

  it('walks mention, take and every end', () => {
    let t = applyTurnEvent([], wake());
    expect(states(t)).toEqual(['A1/-/waiting']);
    t = applyTurnEvent(t, turn('started'));
    expect(states(t)).toEqual(['A1/-/working']);
    for (const end of ['done', 'released', 'expired']) {
      expect(applyTurnEvent(t, turn(end))).toEqual([]);
    }
  });

  it('a mention that arrived during the turn waits again after it ends', () => {
    const t = applyTurnEvent([{ agent_id: 'A1', thread_id: null, state: 'working' }], turn('done', { pending: true }));
    expect(states(t)).toEqual(['A1/-/waiting']);
  });

  it('a queued mention keeps (or restores) the open turn as working', () => {
    expect(states(applyTurnEvent([], wake({ for: [], queued: ['A1'] })))).toEqual(['A1/-/working']);
    const open: AgentTurn[] = [{ agent_id: 'A1', thread_id: null, state: 'working' }];
    expect(applyTurnEvent(open, wake({ for: [], queued: ['A1'] }))).toBe(open);
  });

  it('keeps threads and agents apart', () => {
    let t = applyTurnEvent([], wake({ for: ['A1', 'A2'] }));
    t = applyTurnEvent(t, wake({ thread_id: 'M9' }));
    t = applyTurnEvent(t, turn('started', { thread_id: 'M9' }));
    expect(states(t).sort()).toEqual(['A1/-/waiting', 'A1/M9/working', 'A2/-/waiting']);
    expect(states(turnsIn(t, 'M9'))).toEqual(['A1/M9/working']);
    expect(states(turnsIn(t, null)).sort()).toEqual(['A1/-/waiting', 'A2/-/waiting']);
    t = applyTurnEvent(t, turn('done', { thread_id: 'M9' }));
    expect(turnsIn(t, 'M9')).toEqual([]);
  });

  it('is idempotent: every event applied twice gives the same state', () => {
    const events = [wake(), turn('started'), wake({ for: [], queued: ['A1'] }), turn('done', { pending: true }), turn('started'), turn('released')];
    let once: AgentTurn[] = [];
    let twice: AgentTurn[] = [];
    for (const ev of events) {
      once = applyTurnEvent(once, ev);
      twice = applyTurnEvent(applyTurnEvent(twice, ev), ev);
      expect(twice).toEqual(once);
    }
    expect(once).toEqual([]);
  });

  it('ignores other events and a turn event without an agent', () => {
    const t: AgentTurn[] = [{ agent_id: 'A1', thread_id: null, state: 'working' }];
    expect(applyTurnEvent(t, { type: 'message.posted', after: { channel: 'list:L1' } })).toBe(t);
    expect(applyTurnEvent(t, { type: 'message.agent_turn', after: { state: 'done' } })).toBe(t);
  });
});

describe('pastedName', () => {
  const at = new Date(2026, 10, 9, 8, 5, 3);
  it('names a clipboard screenshot by when it was pasted', () => {
    expect(pastedName('image.png', 'image/png', at)).toBe('Screenshot 2026-11-09 08.05.03.png');
    expect(pastedName('', 'image/jpeg', at)).toBe('Screenshot 2026-11-09 08.05.03.jpg');
    expect(pastedName('', '', at)).toBe('Screenshot 2026-11-09 08.05.03.png');
  });
  it('keeps a real file name', () => {
    expect(pastedName('floor-plan.pdf', 'application/pdf', at)).toBe('floor-plan.pdf');
    expect(pastedName('IMG_2041.png', 'image/png', at)).toBe('IMG_2041.png');
  });
});
