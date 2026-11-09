import { describe, expect, it } from 'vitest';
import { item } from './fixtures';
import { draftProblem, draftWarning, place, reorder, strandedCount } from './structure';

describe('reorder', () => {
  const rows = [
    { id: 'a', position: 0 },
    { id: 'b', position: 1 },
    { id: 'c', position: 2 },
  ];

  it('swaps with the neighbour', () => {
    expect(reorder(rows, 'b', -1)).toEqual([
      { id: 'b', position: 0 },
      { id: 'a', position: 1 },
    ]);
    expect(reorder(rows, 'b', 1)).toEqual([
      { id: 'c', position: 1 },
      { id: 'b', position: 2 },
    ]);
  });

  it('does nothing at an edge or for an unknown id', () => {
    expect(reorder(rows, 'a', -1)).toEqual([]);
    expect(reorder(rows, 'c', 1)).toEqual([]);
    expect(reorder(rows, 'z', 1)).toEqual([]);
  });

  it('renumbers gaps and ties from imported data', () => {
    const messy = [
      { id: 'a', position: 5 },
      { id: 'b', position: 5 },
      { id: 'c', position: 9 },
    ];
    expect(reorder(messy, 'c', -1)).toEqual([
      { id: 'a', position: 0 },
      { id: 'c', position: 1 },
      { id: 'b', position: 2 },
    ]);
  });
});

describe('place (drag a list)', () => {
  const work = [
    { id: 'w1', position: 0 },
    { id: 'w2', position: 1 },
  ];

  it('into another section, before a list', () => {
    expect(place(work, 'p1', 'P', 'W', 'w2')).toEqual([
      { id: 'p1', position: 1, section_id: 'W' },
      { id: 'w2', position: 2 },
    ]);
  });

  it('into another section, last', () => {
    expect(place(work, 'p1', 'P', 'W', null)).toEqual([
      { id: 'p1', position: 2, section_id: 'W' },
    ]);
  });

  it('within a section', () => {
    const rows = [...work, { id: 'w3', position: 2 }];
    expect(place(rows, 'w3', 'W', 'W', 'w1')).toEqual([
      { id: 'w3', position: 0 },
      { id: 'w1', position: 1 },
      { id: 'w2', position: 2 },
    ]);
    expect(place(rows, 'w1', 'W', 'W', null)).toEqual([
      { id: 'w1', position: 2 },
      { id: 'w2', position: 0 },
      { id: 'w3', position: 1 },
    ]);
    expect(place(rows, 'w1', 'W', 'W', 'w2')).toEqual([]); // already there
  });
});

describe('statuses editor guard', () => {
  it('counts items left on removed statuses', () => {
    const open = { id: 's1', name: 'Open', kind: 'open' as const, color: null };
    const done = { id: 's2', name: 'Done', kind: 'done' as const, color: null };
    const items = [
      item({ id: '1', list_id: 'L', status: open }),
      item({ id: '2', list_id: 'L', status: open }),
      item({ id: '3', list_id: 'L', status: done }),
      item({ id: '4', list_id: 'other', status: open }),
      item({ id: '5', list_id: 'L', status: open, archived_at: '2026-10-01T00:00:00Z' }),
    ];
    const keepDone = [{ id: 's2', name: 'Done', kind: 'done' as const, color: null }];
    expect(strandedCount(items, 'L', ['s1', 's2'], keepDone)).toBe(2);
    expect(strandedCount(items, 'L', ['s1', 's2'], [...keepDone, { ...open }])).toBe(0);
  });

  it('explains what is wrong with a draft', () => {
    const done = { name: 'Done', kind: 'done' as const, color: null };
    expect(draftProblem([])).toMatch(/at least one/);
    expect(draftProblem([{ ...done, name: ' ' }])).toMatch(/needs a name/);
    expect(draftProblem([done, { ...done, name: 'done' }])).toMatch(/twice/);
    expect(draftProblem([{ ...done, kind: 'open' }])).toBeNull();
    expect(draftWarning([{ ...done, kind: 'open' }])).toMatch(/Done-kind/);
    expect(draftWarning([done])).toBeNull();
    expect(draftProblem([{ name: 'To do', kind: 'open', color: null }, done])).toBeNull();
  });
});
