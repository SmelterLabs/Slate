import { describe, expect, it } from 'vitest';
import { dotsChangedBy } from './commentdots';
import type { SlateEvent } from './types';

const ev = (type: string, actor_id: string, subject_id: string | null = null): SlateEvent => ({
  id: '1',
  type,
  item_id: type === 'item.read' ? null : 'it',
  subject_id,
  actor_id,
  source: 'ui',
  field: null,
  before: null,
  after: null,
  session_ref: null,
  at: '2026-11-04T20:00:00Z',
});

describe('dotsChangedBy', () => {
  it('reloads on someone else commenting, mentioning or asking', () => {
    for (const t of ['item.commented', 'comment.edited', 'comment.deleted', 'item.mentioned', 'comment.assigned']) {
      expect(dotsChangedBy(ev(t, 'agent'), 'me')).toBe(true);
    }
  });
  it('ignores my own comments', () => {
    expect(dotsChangedBy(ev('item.commented', 'me'), 'me')).toBe(false);
  });
  it('hears my own reads (another device) but not anyone else’s', () => {
    expect(dotsChangedBy(ev('item.read', 'me', 'me'), 'me')).toBe(true);
    expect(dotsChangedBy(ev('item.read', 'guest', 'guest'), 'me')).toBe(false);
  });
  it('ignores everything else and a board with no account yet', () => {
    expect(dotsChangedBy(ev('item.updated', 'agent'), 'me')).toBe(false);
    expect(dotsChangedBy(ev('item.commented', 'agent'), undefined)).toBe(false);
  });
});
