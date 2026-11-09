import { describe, expect, it } from 'vitest';
import { firstName, waitingOn } from './words';

describe('words', () => {
  it('names the person by first name', () => {
    expect(waitingOn('Alex Example')).toBe('Waiting on Alex');
    expect(waitingOn('Alex')).toBe('Waiting on Alex');
    expect(firstName(undefined)).toBe('someone');
  });
});
