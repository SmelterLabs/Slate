import { describe, expect, it } from 'vitest';
import {
  entriesFrom,
  highlightMentions,
  insertMention,
  mentionAt,
  stamp,
  timeline,
  toggleReaction,
  toggleTaskLine,
  fileSize,
} from './activity';
import { renderMarkdown } from './markdown';
import { ev } from './fixtures';
import type { ActivityEntry, Comment } from './types';

const at = (min: number) => new Date(Date.UTC(2026, 9, 23, 18, min)).toISOString();

function evEntry(over: Parameters<typeof ev>[0]): ActivityEntry {
  const e = ev(over);
  return { kind: 'event', at: e.at, comment: null, event: e };
}

const comment: Comment = {
  id: 'c1',
  item_id: 'I1',
  author_id: 'acc_alex',
  body_md: 'hi',
  created_at: at(5),
  edited_at: null,
};

describe('timeline', () => {
  it('folds repeated description edits by one actor into one line with the net change', () => {
    const rows = timeline([
      evEntry({ id: 'e1', field: 'body_md', before: '', after: 'a', at: at(0) }),
      evEntry({ id: 'e2', field: 'body_md', before: 'a', after: 'ab', at: at(1) }),
      evEntry({ id: 'e3', field: 'body_md', before: 'ab', after: 'abc', at: at(2) }),
    ]);
    expect(rows).toHaveLength(1);
    const row = rows[0];
    expect(row.kind === 'event' && [row.times, row.event.before, row.event.after]).toEqual([3, '', 'abc']);
  });

  it('drops the item.blocked that only repeats a just-added dependency', () => {
    const rows = timeline([
      evEntry({ id: 'e1', type: 'item.linked', field: 'links', after: { kind: 'blocked_by', item_id: 'x' }, at: at(0) }),
      evEntry({ id: 'e2', type: 'item.blocked', field: 'blocked', after: { blocked_by: ['x'] }, at: at(0) }),
      evEntry({ id: 'e3', type: 'item.unblocked', field: 'blocked', after: { cleared_by: ['x'] }, at: at(3) }),
      evEntry({ id: 'e4', type: 'item.blocked', field: 'blocked', after: { blocked_by: ['x'] }, at: at(4) }),
    ]);
    expect(rows.map((r) => r.key)).toEqual(['e:e1', 'e:e3', 'e:e4']);
  });

  it('does not fold across a comment, a gap, or a different field', () => {
    const rows = timeline([
      evEntry({ id: 'e1', field: 'body_md', at: at(0) }),
      { kind: 'comment', at: at(1), comment, event: null },
      evEntry({ id: 'e2', field: 'body_md', at: at(2) }),
      evEntry({ id: 'e3', field: 'body_md', at: at(30) }),
      evEntry({ id: 'e4', field: 'title', at: at(31) }),
      evEntry({ id: 'e5', field: 'tags', at: at(31) }),
      evEntry({ id: 'e6', field: 'tags', at: at(31) }),
    ]);
    expect(rows.map((r) => r.kind)).toEqual(['event', 'comment', 'event', 'event', 'event', 'event', 'event']);
  });

  it('merges a detail into ordered entries without the events the comments already show', () => {
    const entries = entriesFrom([comment], [
      ev({ id: 'e1', type: 'item.created', at: at(0) }),
      ev({ id: 'e2', type: 'item.commented', at: at(5) }),
      ev({ id: 'e3', type: 'comment.reacted', at: at(6) }),
      ev({ id: 'e4', at: at(7) }),
    ]);
    expect(entries.map((e) => e.event?.id ?? e.comment?.id)).toEqual(['e1', 'c1', 'e4']);
  });
});

describe('mentions', () => {
  it('finds the @query under the caret', () => {
    expect(mentionAt('hi @sc', 6)).toEqual({ start: 3, query: 'sc' });
    expect(mentionAt('@', 1)).toEqual({ start: 0, query: '' });
    expect(mentionAt('mail a@b', 8)).toBeNull();
    expect(mentionAt('hi @agent there', 15)).toBeNull();
  });

  it('inserts the handle and moves the caret past it', () => {
    expect(insertMention('hi @sc!', 3, 6, 'agent')).toEqual({ text: 'hi @agent !', caret: 10 });
  });

  it('wraps only known handles, never inside tags', () => {
    const html = renderMarkdown('ping @agent and @ghost, see [x](mailto:a@agent.test)');
    const out = highlightMentions(html, (h) => h === 'agent', 'agent');
    expect(out).toContain('<span class="mention me">@agent</span>');
    expect(out).toContain('@ghost');
    expect(out).toContain('href="mailto:a@agent.test"');
  });
});

describe('reactions and task boxes', () => {
  it('toggles my reaction and drops empty ones', () => {
    const r = toggleReaction([{ emoji: '👍', account_ids: ['me'] }], '👍', 'me');
    expect(r).toEqual([]);
    expect(toggleReaction(r, '🎉', 'me')).toEqual([{ emoji: '🎉', account_ids: ['me'] }]);
    expect(toggleReaction([{ emoji: '👍', account_ids: ['you'] }], '👍', 'me')[0].account_ids).toEqual([
      'you',
      'me',
    ]);
  });

  it('renders task boxes with their line and ticks the right one', () => {
    const md = 'Steps\n- [ ] call\n- [x] pay';
    const html = renderMarkdown(md, { taskBoxes: true });
    expect(html).toContain('data-line="1"');
    expect(html).toContain('data-line="2" checked');
    expect(toggleTaskLine(md, 1)).toBe('Steps\n- [x] call\n- [x] pay');
    expect(toggleTaskLine(md, 2)).toBe('Steps\n- [ ] call\n- [ ] pay');
    expect(toggleTaskLine(md, 0)).toBe(md);
  });

  it('formats stamps and sizes', () => {
    const now = new Date('2026-10-23T20:00:00Z');
    expect(stamp('2026-10-23T19:14:00Z', now)).toBe('2:14 PM');
    expect(stamp('2026-10-21T19:14:00Z', now)).toBe('Oct 21 at 2:14 PM');
    expect(fileSize(2_500_000)).toBe('2.4 MB');
    expect(fileSize(2048)).toBe('2 KB');
  });
});
