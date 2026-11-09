import { describe, expect, it } from 'vitest';
import { attention, byList, countBy, needOf } from './attention';
import { item } from './fixtures';
import type { Account, AgentFlag } from './types';

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
