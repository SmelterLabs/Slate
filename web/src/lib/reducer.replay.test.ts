// Every event type the board listens for, through the reducer once and then again (the server's
// replay after a reconnect can repeat what the board already applied): the second pass must
// leave the board exactly as the first did, and must not ask for a reload the first did not.
// A new type in board/events.ts is covered here at once; one with a payload the reducer reads
// gets a sample in SAMPLES.
import { describe, expect, it } from 'vitest';
import { applyEvent, type BoardData, type Effects } from './reducer';
import { EVENT_TYPES } from './board/events';
import { LIST, ev, item } from './fixtures';
import type { SlateEvent } from './types';

const AT = '2026-11-13T12:00:00Z';

function board(): BoardData {
  return {
    items: {
      P1: item({ id: 'P1', title: 'Parent', subtask_count: 1 }),
      I1: item({ id: 'I1', parent_id: 'P1' }),
    },
    lists: { L1: structuredClone(LIST) },
  };
}

function createdSnapshot() {
  const snap = { ...item({ id: 'N1', parent_id: 'P1', title: 'New' }), status_id: 'st_open' } as Record<string, unknown>;
  for (const k of ['status', 'assignees', 'tags', 'subtask_count', 'comment_count']) delete snap[k];
  return snap;
}

/** What each type carries when the reducer reads its payload; the rest take the defaults. */
const SAMPLES: Record<string, Partial<SlateEvent>> = {
  'item.created': { item_id: 'N1', subject_id: 'N1', after: createdSnapshot() },
  'item.updated': { field: 'title', before: 'Fix gutter', after: 'Fixed' },
  'item.status_changed': { field: 'status', before: 'st_open', after: 'st_done' },
  'item.archived': { field: 'archived_at', before: null, after: AT },
  'item.unarchived': { field: 'archived_at', before: AT, after: null },
  'item.moved': { field: 'position', before: 1, after: 7 },
  'item.deleted': { before: { parent_id: 'P1' } },
  'item.purged': { before: { parent_id: 'P1' } },
  'item.trashed': { after: { trashed_at: AT, archived_at: AT } },
  'item.restored': { after: { trashed_at: null, archived_at: null } },
  'item.reported': { after: { state: 'working', note: 'On it', thread: { ref: 'thread-1' } } },
  'item.due': { field: 'due_at', after: { due_at: AT } },
  'item.due_soon': { field: 'due_at', after: { due_at: AT } },
  'item.overdue': { field: 'due_at', after: { due_at: AT } },
  'recurrence.spawned': { item_id: 'N2', subject_id: 'R1' },
};

/** Event types about something other than an item carry no item id. */
const ITEM_KINDS = new Set(['item', 'comment', 'checklist', 'recurrence']);

function sample(type: string): SlateEvent {
  const kind = type.split('.')[0];
  const base: Partial<SlateEvent> = ITEM_KINDS.has(kind) ? {} : { item_id: null, subject_id: 'X1' };
  return ev({ type, actor_id: 'A_agent', at: AT, ...base, ...SAMPLES[type] });
}

/** An effect the second pass may not add: a reload or a nav reload the first did not ask for. */
function heavier(second: Effects, first: Effects) {
  return (second.reload && !first.reload) || (second.structure && !first.structure);
}

describe('every event type, applied twice', () => {
  it.each([...EVENT_TYPES])('%s', (type) => {
    const d = board();
    const e = sample(type);
    const first = applyEvent(d, e);
    const once = JSON.stringify(d);
    const second = applyEvent(d, e);
    expect(JSON.stringify(d)).toBe(once);
    expect(heavier(second, first)).toBe(false);
    // Whatever the second pass asks for again is only ever a refetch of what the board holds
    // or what the event names.
    for (const id of second.refetch) expect([e.item_id, 'P1']).toContain(id);
  });

  it('covers every sample it carries (a renamed type must not leave a stale sample)', () => {
    const listed = new Set<string>(EVENT_TYPES);
    expect(Object.keys(SAMPLES).filter((t) => !listed.has(t))).toEqual([]);
  });
});

describe('what each kind of event asks of the store', () => {
  const fx = (type: string) => applyEvent(board(), sample(type));

  it('structure events reload the nav; statuses and restores reload everything', () => {
    for (const t of EVENT_TYPES.filter((t) => /^(section|list)\./.test(t))) {
      expect(fx(t).structure, t).toBe(true);
      expect(fx(t).reload, t).toBe(t === 'list.statuses_changed' || t.endsWith('.unarchived'));
    }
  });

  it('tag, view, favorite and capacity events each reload their own cache and touch no item', () => {
    const flags: Record<string, keyof Effects> = { tag: 'tags', view: 'views', favorite: 'favorites', capacity: 'capacities' };
    for (const t of EVENT_TYPES) {
      const flag = flags[t.split('.')[0]];
      if (!flag) continue;
      const d = board();
      const before = JSON.stringify(d);
      const out = applyEvent(d, sample(t));
      expect(out[flag], t).toBe(true);
      expect(out.refetch, t).toEqual([]);
      expect(JSON.stringify(d), t).toBe(before);
    }
  });

  it('events about accounts, tokens, docs, chat, webhooks and the rest change no item', () => {
    const quiet = EVENT_TYPES.filter((t) => !ITEM_KINDS.has(t.split('.')[0]) && !/^(section|list|tag|view|favorite|capacity)\./.test(t));
    expect(quiet.length).toBeGreaterThan(50);
    for (const t of quiet) {
      const d = board();
      const before = JSON.stringify(d);
      const out = applyEvent(d, sample(t));
      expect(JSON.stringify(d), t).toBe(before);
      expect(out.refetch, t).toEqual([]);
      expect(out.reload || out.structure, t).toBe(false);
    }
  });

  it('comment and checklist events refetch the item the card counts for, only when held', () => {
    for (const t of EVENT_TYPES.filter((t) => t.startsWith('checklist.') || /^comment\.(assigned|resolved|deleted)$/.test(t))) {
      expect(fx(t).refetch, t).toEqual(['I1']);
      expect(applyEvent(board(), sample(t).item_id ? { ...sample(t), item_id: 'gone' } : sample(t)).refetch, t).toEqual([]);
    }
  });

  it('the scheduler notices change nothing on the card (their after is not a column value)', () => {
    for (const t of ['item.due', 'item.due_soon', 'item.overdue']) {
      const d = board();
      const before = JSON.stringify(d);
      expect(applyEvent(d, sample(t)).refetch, t).toEqual([]);
      expect(JSON.stringify(d), t).toBe(before);
    }
  });
});
