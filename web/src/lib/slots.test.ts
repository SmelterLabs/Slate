import { describe, expect, it } from 'vitest';
import { beforeAt, slotFor, STEP } from './slots';

const g = (...ps: number[]) => ps.map((position, i) => ({ id: String.fromCharCode(97 + i), position }));

describe('slotFor (manual order after a drop)', () => {
  it('takes the midpoint of the gap it lands in: one write', () => {
    expect(slotFor(g(10, 20, 30), 'c', 'b')).toEqual([{ id: 'c', position: 15 }]);
  });
  it('goes before the first or after the last by a step', () => {
    expect(slotFor(g(10, 20, 30), 'b', 'a')).toEqual([{ id: 'b', position: 10 - STEP }]);
    expect(slotFor(g(10, 20, 30), 'a', null)).toEqual([{ id: 'a', position: 30 + STEP }]);
  });
  it('writes nothing when dropped back in place', () => {
    expect(slotFor(g(10, 20, 30), 'b', 'c')).toEqual([]);
    expect(slotFor(g(10, 20, 30), 'c', null)).toEqual([]);
  });
  it('renumbers the group only when there is no gap', () => {
    expect(slotFor(g(1, 2, 3), 'c', 'b')).toEqual([
      { id: 'a', position: STEP },
      { id: 'c', position: 2 * STEP },
      { id: 'b', position: 3 * STEP },
    ]);
  });
  it('places a card arriving from another group', () => {
    const visitor = { id: 'z', position: 500 };
    expect(slotFor(g(10, 20), 'z', 'b', visitor)).toEqual([{ id: 'z', position: 15 }]);
    expect(slotFor([], 'z', null, visitor)).toEqual([]);
  });
});

describe('beforeAt', () => {
  const rects = [
    { id: 'a', top: 0, bottom: 40 },
    { id: 'b', top: 50, bottom: 90 },
  ];
  it('is the first element whose middle is below the pointer', () => {
    expect(beforeAt(rects, 5)).toBe('a');
    expect(beforeAt(rects, 30)).toBe('b');
    expect(beforeAt(rects, 80)).toBe(null);
  });
});
