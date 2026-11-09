import { describe, expect, it } from 'vitest';
import { ev } from './fixtures';
import { verb, type HistoryContext } from './history';

const ctx: HistoryContext = { name: (id) => ({ c: 'Alex' })[id] ?? id, status: (s) => s };

describe('verb', () => {
  it('says what the scheduler did in words, not event names', () => {
    expect(verb(ev({ type: 'item.due' }), ctx)).toBe('marked this due now');
    expect(verb(ev({ type: 'item.overdue' }), ctx)).toBe('marked this overdue');
    expect(verb(ev({ type: 'item.due_soon', after: { lead_hours: 24 } }), ctx)).toBe('flagged this due within 24 hours');
    expect(verb(ev({ type: 'item.due_soon', after: { lead_hours: 1 } }), ctx)).toBe('flagged this due within 1 hour');
  });

  it('a move between lists reads like one', () => {
    expect(verb(ev({ type: 'item.moved', field: 'list_id', before: 'L1', after: 'L2' }), ctx)).toBe(
      'moved this to another list',
    );
  });

  it('never shows a raw event type', () => {
    for (const type of ['item.due', 'item.overdue', 'item.due_soon', 'item.moved', 'recurrence.advanced']) {
      expect(verb(ev({ type }), ctx)).not.toMatch(/^item |^recurrence /);
    }
  });

  it('a chat message names the tasks it links instead of showing their ids', () => {
    const withTitles: HistoryContext = { ...ctx, title: (id) => ({ abcdefABCDEF: 'Fix the gutter' })[id] };
    const posted = ev({ type: 'message.posted', after: { body_md: 'Picked up #abcdefABCDEF and #zzzzzzzzzzzz' } });
    expect(verb(posted, withTitles)).toBe('wrote: “Picked up #Fix the gutter and #zzzzzzzzzzzz”');
  });

  it('a doc mention says where and what', () => {
    const e = ev({ type: 'doc.mentioned', after: { mentions: ['agent'], title: 'Roof plan', excerpt: '@agent price it' } });
    expect(verb(e, ctx)).toBe('mentioned @agent in “Roof plan”: “@agent price it”');
  });
});
