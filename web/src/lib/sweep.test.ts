import { describe, expect, it } from 'vitest';
import { LIST } from './fixtures';
import { dropStatus, isSweepDay, nextSweepDay } from './sweep';

describe('sweep helpers', () => {
  // Thursday 2026-10-22, 15:00 Chicago (weekday = 3, Thursday).
  const THU = new Date('2026-10-22T20:00:00Z');
  // Friday 2026-10-23, 15:00 Chicago (weekday = 4, Friday).
  const FRI = new Date('2026-10-23T20:00:00Z');
  // Saturday 2026-10-24, 15:00 Chicago (weekday = 5, Saturday).
  const SAT = new Date('2026-10-24T20:00:00Z');

  it('computes the coming sweep day and detects when today is sweep day', () => {
    expect(nextSweepDay(THU, 4)).toBe('2026-10-23');
    expect(nextSweepDay(FRI, 4)).toBe('2026-10-23');
    expect(nextSweepDay(SAT, 4)).toBe('2026-10-30');
    expect(isSweepDay(THU, 4)).toBe(false);
    expect(isSweepDay(FRI, 4)).toBe(true);
    expect(isSweepDay(THU, 3)).toBe(true);
  });

  it('dropStatus picks the first cancelled status, else the first done status', () => {
    const withCancelled = {
      ...LIST,
      statuses: [
        ...LIST.statuses,
        { id: 'st_cancel', name: 'Cancelled', kind: 'cancelled' as const, color: null, position: 4 },
      ],
    };
    expect(dropStatus(withCancelled)?.id).toBe('st_cancel');
    expect(dropStatus(LIST)?.id).toBe('st_done');
  });
});
