import { describe, expect, it } from 'vitest';
import { groupHits, hitHref, shouldSearch, type SearchHit } from './search';

const hit = (kind: SearchHit['kind'], id: string, extra: Partial<SearchHit> = {}): SearchHit => ({
  kind,
  id,
  title: id,
  snippet: null,
  where: null,
  at: '2026-11-04T12:00:00Z',
  ...extra,
});

describe('shouldSearch', () => {
  it('asks the server for two or more plain characters only', () => {
    expect(shouldSearch('a')).toBe(false);
    expect(shouldSearch(' ab ')).toBe(true);
    expect(shouldSearch('>inbox')).toBe(false);
    expect(shouldSearch('#tag')).toBe(false);
    expect(shouldSearch('@alex')).toBe(false);
    expect(shouldSearch('')).toBe(false);
  });
});

describe('groupHits', () => {
  it('groups Tasks, Docs then Chat and drops empty groups', () => {
    const groups = groupHits([hit('message', 'm1'), hit('task', 't1'), hit('doc', 'd1'), hit('task', 't2')]);
    expect(groups.map((g) => g.label)).toEqual(['Tasks', 'Docs', 'Chat']);
    expect(groups[0].hits.map((h) => h.id)).toEqual(['t1', 't2']);
    expect(groupHits([hit('message', 'm1')]).map((g) => g.label)).toEqual(['Chat']);
    expect(groupHits([])).toEqual([]);
  });

  it('leaves out tasks and docs the palette already shows, and duplicates', () => {
    const groups = groupHits([hit('task', 't1'), hit('task', 't2'), hit('doc', 'd1'), hit('doc', 'd1'), hit('message', 'm1')], {
      task: ['t1'],
      doc: new Set(['dX']),
    });
    expect(groups.map((g) => [g.kind, g.hits.map((h) => h.id)])).toEqual([
      ['task', ['t2']],
      ['doc', ['d1']],
      ['message', ['m1']],
    ]);
  });
});

describe('hitHref', () => {
  it('opens docs and chat by hash; a task opens in the window', () => {
    expect(hitHref(hit('doc', 'd1'))).toBe('#/docs/d1');
    expect(hitHref(hit('message', 'm1', { channel: 'list:L1' }))).toBe('#/chat/list:L1');
    expect(hitHref(hit('message', 'm2', { channel: 'general', thread_id: 'm1' }))).toBe('#/chat/general?thread=m1');
    expect(hitHref(hit('task', 't1'))).toBeNull();
  });
});
