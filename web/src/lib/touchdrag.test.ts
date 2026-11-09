import { describe, expect, it } from 'vitest';
import { beyondSlop, edgeSpeed, MAX_SPEED } from './touchdrag';

describe('edgeSpeed (auto-scroll near an edge)', () => {
  it('is still in the middle and ramps up toward each edge', () => {
    expect(edgeSpeed(500, 0, 1000)).toBe(0);
    expect(edgeSpeed(50, 0, 1000)).toBeLessThan(0);
    expect(edgeSpeed(0, 0, 1000)).toBe(-MAX_SPEED);
    expect(edgeSpeed(1000, 0, 1000)).toBe(MAX_SPEED);
    expect(Math.abs(edgeSpeed(30, 0, 1000))).toBeGreaterThan(Math.abs(edgeSpeed(50, 0, 1000)));
  });
  it('keeps the zone small in a small box', () => {
    expect(edgeSpeed(50, 0, 100)).toBe(0);
    expect(edgeSpeed(10, 0, 100)).toBeLessThan(0);
  });
});

describe('beyondSlop', () => {
  it('tells a held finger from a scroll', () => {
    expect(beyondSlop(0, 0, 3, 4)).toBe(false);
    expect(beyondSlop(0, 0, 9, 0)).toBe(true);
  });
});
