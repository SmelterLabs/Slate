import { describe, expect, it } from 'vitest';
import { attention, byList, countBy, needOf, projectAttention } from './attention';
import { LIST } from './fixtures';
import { item } from './fixtures';
import type { Account, AgentFlag, MyAccess, Section } from './types';

const ME = 'me';
const accounts: Record<string, Account> = {
  me: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' },
  guest: { id: 'guest', handle: 'guest', display_name: 'Guest', kind: 'person' },
  sc: { id: 'sc', handle: 'agent', display_name: 'Agent', kind: 'agent' },
};
const DONE = { id: 's3', name: 'Done', kind: 'done' as const, color: null };
const flag = (state: AgentFlag['state'], at = '2026-10-29T10:00:00Z'): AgentFlag => ({ state, by: 'sc', at });

describe('needOf', () => {
  it('waiting on me wins, whoever filed it', () => {
    expect(needOf(item({ created_by: 'guest', waiting_on_account_id: ME, agent: flag('blocked') }), ME, accounts)).toBe('waiting');
  });
  it('an agent blocked on my card with nobody named', () => {
    expect(needOf(item({ created_by: ME, agent: flag('blocked') }), ME, accounts)).toBe('blocked');
  });
  it('an answered agent (its reply is in, an open Ask for it) needs nobody until it picks it up', () => {
    expect(needOf(item({ created_by: ME, open_asks: ['sc'], agent: flag('blocked') }), ME, accounts)).toBeNull();
  });
  it('an agent blocked on someone else is not mine', () => {
    expect(needOf(item({ created_by: ME, waiting_on_account_id: 'guest', agent: flag('blocked') }), ME, accounts)).toBeNull();
  });
  it('an open Ask addressed to me', () => {
    expect(needOf(item({ created_by: 'guest', open_asks: [ME] }), ME, accounts)).toBe('asked');
  });
  it('ready for review on my card, or on one an agent filed', () => {
    expect(needOf(item({ created_by: ME, agent: flag('review') }), ME, accounts)).toBe('review');
    expect(needOf(item({ created_by: 'sc', agent: flag('review') }), ME, accounts)).toBe('review');
  });
  it("review on another person's card is theirs", () => {
    expect(needOf(item({ created_by: 'guest', agent: flag('review') }), ME, accounts)).toBeNull();
  });
  it('a structured ask waiting on me is a need', () => {
    const ask = { comment_id: 'C1', question: 'Start the import now?', options: ['Yes', 'No'], by_id: 'sc', at: '2026-10-29T10:00:00Z' };
    expect(needOf(item({ created_by: 'guest', waiting_on_account_id: ME, agent: flag('blocked'), ask }), ME, accounts)).toBe('waiting');
  });
  it('a failed agent is not a need, whoever filed the card or what wait/ask was left on it', () => {
    expect(needOf(item({ created_by: ME, agent: flag('failed') }), ME, accounts)).toBeNull();
    expect(needOf(item({ created_by: 'sc', agent: flag('failed') }), ME, accounts)).toBeNull();
    expect(needOf(item({ created_by: ME, waiting_on_account_id: ME, agent: flag('failed') }), ME, accounts)).toBeNull();
    expect(needOf(item({ created_by: ME, open_asks: [ME], agent: flag('failed') }), ME, accounts)).toBeNull();
  });
  it('working agents, dependency blocks, closed and archived cards never count', () => {
    expect(needOf(item({ created_by: ME, agent: flag('working') }), ME, accounts)).toBeNull();
    expect(needOf(item({ created_by: ME, blocked: true }), ME, accounts)).toBeNull();
    expect(needOf(item({ created_by: ME, status: DONE, agent: flag('review') }), ME, accounts)).toBeNull();
    expect(needOf(item({ created_by: ME, archived_at: '2026-10-29T00:00:00Z', agent: flag('review') }), ME, accounts)).toBeNull();
  });
});

describe('attention', () => {
  it('most urgent first, then the oldest flag', () => {
    const list = attention(
      [
        item({ id: 'r2', created_by: ME, agent: flag('review', '2026-10-29T12:00:00Z') }),
        item({ id: 'r1', created_by: ME, agent: flag('review', '2026-10-29T09:00:00Z') }),
        item({ id: 'a', created_by: ME, open_asks: [ME] }),
        item({ id: 'w', created_by: ME, waiting_on_account_id: ME }),
        item({ id: 'n', created_by: ME }),
      ],
      ME,
      accounts,
    );
    expect(list.map((x) => x.item.id)).toEqual(['w', 'a', 'r1', 'r2']);
    expect(countBy(list)).toEqual({ waiting: 1, blocked: 0, asked: 1, review: 2 });
  });
  it('nobody signed in: nothing', () => {
    expect(attention([item({ waiting_on_account_id: ME })], '', accounts)).toEqual([]);
  });
});

describe('byList', () => {
  it('counts per list, colored by the most urgent', () => {
    const list = attention(
      [
        item({ id: 'r', list_id: 'L1', created_by: ME, agent: flag('review') }),
        item({ id: 'w', list_id: 'L1', created_by: ME, waiting_on_account_id: ME }),
        item({ id: 'q', list_id: 'L2', created_by: ME, open_asks: [ME] }),
      ],
      ME,
      accounts,
    );
    const m = byList(list);
    expect(m.get('L1')).toEqual({ n: 2, top: 'waiting' });
    expect(m.get('L2')).toEqual({ n: 1, top: 'asked' });
    expect(m.get('L3')).toBeUndefined();
  });
});

describe('attention in reference lists', () => {
  it('a reference-list card never needs me, waiting or asked', () => {
    const lists = { L1: LIST, L_REF: { ...LIST, id: 'L_REF', reference: true } };
    const items = [
      item({ id: 'w', list_id: 'L_REF', created_by: ME, waiting_on_account_id: ME }),
      item({ id: 'a', list_id: 'L_REF', created_by: 'guest', open_asks: [ME] }),
      item({ id: 'n', list_id: 'L1', created_by: ME, waiting_on_account_id: ME }),
    ];
    expect(attention(items, ME, accounts, lists).map((x) => x.item.id)).toEqual(['n']);
  });
});

describe('projectAttention', () => {
  const sec = (over: Partial<Section>): Section => ({
    id: 'S1',
    name: 'Launch',
    slug: 'launch',
    position: 0,
    audience: 'work',
    created_at: '2026-10-20T00:00:00Z',
    archived_at: null,
    project: true,
    next_step: null,
    ...over,
  });
  it('flags open project sections with no next step only for managers of that section', () => {
    const sections = [
      sec({ id: 'S1', name: 'Launch', project: true, next_step: null }),
      sec({ id: 'S2', name: 'Has next', project: true, next_step: { item_id: 'I1', title: 'Ship', assignee_ids: ['me'], due_at: '2026-11-01T22:00:00Z' } }),
      sec({ id: 'S3', name: 'Not a project', project: false, next_step: null }),
      sec({ id: 'S4', name: 'Archived', project: true, next_step: null, archived_at: '2026-10-21T00:00:00Z' }),
      sec({ id: 'S5', name: 'View only', project: true, next_step: null }),
    ];
    const access: MyAccess = {
      all: false,
      level: 'edit',
      manage: false,
      general: true,
      sections: { S1: 'edit', S2: 'edit', S3: 'edit', S4: 'edit', S5: 'view' },
      lists: {},
    };
    expect(projectAttention(sections, access).map((p) => p.section.id)).toEqual(['S1']);
    expect(projectAttention(sections, undefined).map((p) => p.section.id)).toEqual(['S1', 'S5']);
  });
});
