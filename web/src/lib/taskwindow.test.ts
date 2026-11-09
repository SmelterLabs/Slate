import { describe, expect, it } from 'vitest';
import {
  ACTIVITY_NARROWEST,
  clampActivity,
  clampHeight,
  clampWidth,
  DEFAULT_STATE,
  NARROWEST_WIDTH,
  readState,
  SHORTEST_HEIGHT,
  stacks,
  windowWidth,
  writeState,
} from './taskwindow';

describe('clamps', () => {
  it('keeps the centered size between the smallest window and the screen less its margin', () => {
    expect(clampWidth(1280, 1920)).toBe(1280);
    expect(clampWidth(100, 1920)).toBe(NARROWEST_WIDTH);
    expect(clampWidth(5000, 1920)).toBe(1920 - 48);
    expect(clampWidth(5000, 600)).toBe(NARROWEST_WIDTH);
    expect(clampHeight(900, 1080)).toBe(900);
    expect(clampHeight(10, 1080)).toBe(SHORTEST_HEIGHT);
    expect(clampHeight(5000, 1080)).toBe(1080 - 48);
  });
  it('keeps the activity column between its narrowest and what leaves the main column room', () => {
    expect(clampActivity(420, 1280)).toBe(420);
    expect(clampActivity(10, 1280)).toBe(ACTIVITY_NARROWEST);
    expect(clampActivity(5000, 1280)).toBe(1280 - 360);
    expect(clampActivity(5000, 400)).toBe(ACTIVITY_NARROWEST);
  });
});

describe('windowWidth and stacks', () => {
  it('centered is its own width; docked is the dock share of the screen', () => {
    expect(windowWidth(DEFAULT_STATE, 1920)).toBe(1280);
    expect(windowWidth({ ...DEFAULT_STATE, dock: 'third' }, 1920)).toBe(640);
    expect(windowWidth({ ...DEFAULT_STATE, dock: 'quarter' }, 1920)).toBe(480);
  });
  it('a narrow docked window stacks the activity under the task', () => {
    expect(stacks(DEFAULT_STATE, 1920)).toBe(false);
    expect(stacks({ ...DEFAULT_STATE, dock: 'third' }, 2560)).toBe(false);
    expect(stacks({ ...DEFAULT_STATE, dock: 'third' }, 1920)).toBe(true);
    expect(stacks({ ...DEFAULT_STATE, dock: 'quarter' }, 3840)).toBe(false);
    expect(stacks(DEFAULT_STATE, 390)).toBe(true);
  });
});

describe('readState / writeState', () => {
  it('round-trips and falls back on garbage', () => {
    const s = { dock: 'third' as const, width: 1000, height: 700, activity: 500 };
    expect(readState(writeState(s))).toEqual(s);
    expect(readState(null)).toEqual(DEFAULT_STATE);
    expect(readState('{nope')).toEqual(DEFAULT_STATE);
    expect(readState(JSON.stringify({ dock: 'left', width: -4, activity: 'x' }))).toEqual(DEFAULT_STATE);
  });
});
