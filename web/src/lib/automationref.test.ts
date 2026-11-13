import { describe, expect, it, vi } from 'vitest';

const get = vi.fn();
vi.mock('./api', () => ({ get: (path: string) => get(path) }));

import { automationNames, automationOf } from './automationref.svelte';

describe('automationOf', () => {
  it('names the rule behind an automation change', () => {
    expect(automationOf({ session_ref: 'automation:abc' })).toBe('abc');
    expect(automationOf({ session_ref: 'recurrence:abc' })).toBeNull();
    expect(automationOf({ session_ref: null })).toBeNull();
  });
});

describe('automation names', () => {
  it('loads the names once, on first need', async () => {
    get.mockResolvedValue([{ id: 'r1', name: 'Tag shipped work' }]);
    expect(automationNames.name('r1')).toBeUndefined();
    automationNames.ensure();
    automationNames.ensure();
    await vi.waitFor(() => expect(automationNames.name('r1')).toBe('Tag shipped work'));
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith('/automations');
  });
});
