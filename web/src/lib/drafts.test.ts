import { describe, expect, it } from 'vitest';
import {
  DRAFT_MAX_CHARS,
  DRAFT_PREFIX,
  DRAFT_TTL_MS,
  draftKey,
  itemDraftKeys,
  liveDraftKeys,
  readDraft,
  writeDraft,
  type DraftStore,
} from './drafts';

function memory(): DraftStore & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return {
    map,
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
    key: (i) => [...map.keys()][i] ?? null,
    get length() {
      return map.size;
    },
  };
}

describe('drafts', () => {
  it('keeps typed text until it is sent (blank) and reads it back', () => {
    const s = memory();
    expect(writeDraft('comment:a', 'half a thought', s)).toBe(true);
    expect(readDraft('comment:a', s)).toBe('half a thought');
    expect(writeDraft('comment:a', '   ', s)).toBe(false);
    expect(readDraft('comment:a', s)).toBe('');
    expect(s.map.size).toBe(0);
  });

  it('drops a draft nobody came back to, and prunes it from the key list', () => {
    const s = memory();
    writeDraft('old', 'stale', s, 0);
    writeDraft('new', 'fresh', s, DRAFT_TTL_MS);
    expect(readDraft('old', s, DRAFT_TTL_MS + 1)).toBe('');
    expect(liveDraftKeys(s, DRAFT_TTL_MS + 1)).toEqual(['new']);
    expect(s.map.has(DRAFT_PREFIX + 'old')).toBe(false);
  });

  it('ignores other storage and junk under the prefix', () => {
    const s = memory();
    s.setItem('slate.token', 'secret');
    s.setItem(DRAFT_PREFIX + 'bad', '{not json');
    writeDraft('ok', 'x', s);
    expect(liveDraftKeys(s)).toEqual(['ok']);
    expect(s.getItem('slate.token')).toBe('secret');
  });

  it('caps a runaway paste and survives a full or missing storage', () => {
    const s = memory();
    writeDraft('big', 'x'.repeat(DRAFT_MAX_CHARS + 50), s);
    expect(readDraft('big', s)).toHaveLength(DRAFT_MAX_CHARS);
    const full = { ...memory(), setItem: () => { throw new Error('QuotaExceededError'); } };
    expect(writeDraft('k', 'text', full)).toBe(false);
    expect(writeDraft('k', 'text', null)).toBe(false);
    expect(readDraft('k', null)).toBe('');
  });

  it('partitions document composers by account, document, field and review/thread context', () => {
    const s = memory();
    const alice = draftKey.doc('Alice', 'doc', 'review', 'revision:1:quote');
    writeDraft(alice, 'Private review', s);
    for (const key of [
      draftKey.doc('Bob', 'doc', 'review', 'revision:1:quote'),
      draftKey.doc('Alice', 'other', 'review', 'revision:1:quote'),
      draftKey.doc('Alice', 'doc', 'discussion', 'revision:1:quote'),
      draftKey.doc('Alice', 'doc', 'review', 'revision:2:quote'),
      draftKey.doc('Alice', 'doc', 'review', 'revision:1:other-quote'),
    ]) expect(readDraft(key, s)).toBe('');
    expect(readDraft(alice, s)).toBe('Private review');
  });

  it('a card is marked for its comment, subtask and hand-off drafts', () => {
    expect(itemDraftKeys('i1')).toEqual([draftKey.comment('i1'), draftKey.subtask('i1'), draftKey.handoff('i1')]);
    expect(draftKey.chat('list:abc', null)).not.toBe(draftKey.chat('list:abc', 'm1'));
  });
});
