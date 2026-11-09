import { describe, expect, it } from 'vitest';
import { heldFor, movesPlan, planWhen } from './plan';

// 2026-11-03 02:00 CST (08:00 UTC).
const NOW = new Date('2026-11-03T08:00:00Z');

describe('planWhen', () => {
  it('says now for no time and for a time already past', () => {
    expect(planWhen(null, NOW)).toBe('now');
    expect(planWhen('2026-11-03T07:00:00Z', NOW)).toBe('now');
  });
  it('shows the time on the same Chicago day, the weekday on another', () => {
    expect(planWhen('2026-11-03T12:00:00Z', NOW)).toBe('6:00 AM');
    expect(planWhen('2026-11-04T12:00:00Z', NOW)).toBe('Wed 6:00 AM');
  });
});

describe('movesPlan', () => {
  it('re-reads after item, comment and account events only', () => {
    expect(movesPlan('item.run_requested')).toBe(true);
    expect(movesPlan('comment.assigned')).toBe(true);
    expect(movesPlan('account.updated')).toBe(true);
    expect(movesPlan('message.posted')).toBe(false);
    expect(movesPlan('doc.updated')).toBe(false);
  });
});

describe('heldFor', () => {
  it('reads as minutes then hours', () => {
    expect(heldFor(20)).toBe('held under a minute');
    expect(heldFor(4 * 60)).toBe('held 4 min');
    expect(heldFor(2 * 3600 + 5 * 60)).toBe('held 2 h 05 min');
    expect(heldFor(3 * 3600)).toBe('held 3 h');
  });
});
