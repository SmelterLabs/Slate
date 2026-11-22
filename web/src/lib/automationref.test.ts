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
    automationNames.ensure(vi.fn());
    automationNames.ensure(vi.fn());
    await vi.waitFor(() => expect(automationNames.name('r1')).toBe('Tag shipped work'));
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith('/automations');
  });

  it('reports a failure once and does not ask again on the next render', async () => {
    vi.resetModules();
    const { automationNames: fresh } = await import('./automationref.svelte');
    const boom = new Error('database is locked');
    get.mockReset();
    get.mockRejectedValue(boom);
    const report = vi.fn();
    fresh.ensure(report);
    await vi.waitFor(() => expect(report).toHaveBeenCalledWith('load automation names', boom));
    fresh.ensure(report);
    fresh.ensure(report);
    await Promise.resolve();
    expect(get).toHaveBeenCalledTimes(1);
    expect(report).toHaveBeenCalledTimes(1);
  });
});
