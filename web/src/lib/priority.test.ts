import { describe, expect, it } from 'vitest';
import { priorityLabel, priorityLong } from './priority';

describe('priority words', () => {
  it('names each flag in words', () => {
    expect(priorityLabel('urgent')).toBe('Urgent');
    expect(priorityLabel('low')).toBe('Low');
    expect(priorityLong('high')).toBe('High');
  });
});
