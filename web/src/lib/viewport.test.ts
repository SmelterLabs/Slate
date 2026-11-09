import { describe, expect, it } from 'vitest';
import { keyboardInset } from './viewport';
import { axisOf, follow, THRESHOLD } from './swipe';

describe('keyboardInset', () => {
  it('is the height the keyboard covers', () => {
    expect(keyboardInset(844, 508, 0)).toBe(336);
    expect(keyboardInset(844, 508, 120)).toBe(216); // page slid up under it
  });
  it('ignores browser toolbars coming and going', () => {
    expect(keyboardInset(844, 800, 0)).toBe(0);
    expect(keyboardInset(844, 844, 0)).toBe(0);
  });
});

describe('swipe', () => {
  it('waits to tell a swipe from a scroll', () => {
    expect(axisOf(4, 3)).toBe(null);
    expect(axisOf(30, 5)).toBe('x');
    expect(axisOf(5, 30)).toBe('y');
    expect(axisOf(20, 18)).toBe('y'); // diagonal counts as a scroll
  });
  it('follows the finger, then resists past the threshold', () => {
    expect(follow(40)).toBe(40);
    expect(follow(-THRESHOLD)).toBe(-THRESHOLD);
    expect(follow(THRESHOLD + 100)).toBeLessThan(THRESHOLD + 100);
    expect(follow(-(THRESHOLD + 100))).toBeGreaterThan(-(THRESHOLD + 100));
  });
});
