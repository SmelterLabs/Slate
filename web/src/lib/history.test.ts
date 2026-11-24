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

  it('a Stop names the agent it was sent to', () => {
    expect(verb(ev({ type: 'item.stop_requested', after: { for: ['c'] } }), ctx)).toBe('asked Alex to stop');
  });

  it('a run and an Ask read as words', () => {
    expect(verb(ev({ type: 'item.work_started' }), ctx)).toBe('started working on this');
    expect(verb(ev({ type: 'item.work_finished', after: { steps: 6 } }), ctx)).toBe('ended the run (6 steps)');
    expect(verb(ev({ type: 'item.work_finished', after: { outcome: 'ask', steps: 1 } }), ctx)).toBe('ended the run with a question (1 step)');
    expect(verb(ev({ type: 'item.work_finished', after: { outcome: 'later' } }), ctx)).toBe('ended the run until the card is due');
    expect(verb(ev({ type: 'item.work_finished', after: { outcome: 'release', expired: true } }), ctx)).toBe('ended the run: it stopped reporting');
    expect(verb(ev({ type: 'item.run_requested', after: { reason: 'now' } }), ctx)).toBe('pressed Run now');
    expect(verb(ev({ type: 'item.reported', after: { state: 'failed', note: 'Connection refused' } }), ctx)).toBe('could not run: Connection refused');
    expect(verb(ev({ type: 'item.reported', after: { state: 'failed' } }), ctx)).toBe('could not run');
    expect(verb(ev({ type: 'item.run_failed', after: { failures: 3, note: 'Connection\nrefused' } }), ctx)).toBe('could not run after 3 tries: Connection refused');
    expect(verb(ev({ type: 'item.work_finished', after: { outcome: 'failed', note: 'Slate refused this.\n\nIt said: hello', steps: 2 } }), ctx)).toBe('ended the run: it failed: Slate refused this. It said: hello (2 steps)');
    expect(verb(ev({ type: 'item.run_requested', after: { reason: 'retry' } }), ctx)).toBe('ran this again after a failed run');
    expect(verb(ev({ type: 'item.updated', field: 'parked_until', after: null }), ctx)).toBe('took this out of parked');
    expect(verb(ev({ type: 'item.updated', field: 'parked_note', after: 'the supplier' }), ctx)).toBe('noted what this waits on: the supplier');
    expect(verb(ev({ type: 'item.stalled', after: { agent_id: 'c' } }), ctx)).toBe('stopped without finishing');
    expect(verb(ev({ type: 'comment.assigned', after: { assignee_id: 'c' } }), ctx)).toBe('asked Alex');
    expect(verb(ev({ type: 'comment.resolved' }), ctx)).toBe('resolved an Ask');
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

  it('a deleted comment says whose it was, never what it said', () => {
    expect(verb(ev({ type: 'comment.deleted', actor_id: 'c', before: { author_id: 'c', body_md: 'x' } }), ctx)).toBe(
      'deleted a comment',
    );
    expect(verb(ev({ type: 'comment.deleted', actor_id: 'd', before: { author_id: 'c', body_md: 'x' } }), ctx)).toBe(
      'deleted Alex’s comment',
    );
  });

  it('a doc mention says where and what', () => {
    const e = ev({ type: 'doc.mentioned', after: { mentions: ['agent'], title: 'Roof plan', excerpt: '@agent price it' } });
    expect(verb(e, ctx)).toBe('mentioned @agent in “Roof plan”: “@agent price it”');
  });
});
