// @vitest-environment happy-dom
import { mount, unmount } from 'svelte';
import { expect, it, vi } from 'vitest';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../lib/api', () => ({ get }));
vi.mock('../../lib/store.svelte', () => ({
  board: { canManage: true, now: new Date('2026-11-21T14:00:00Z'), onEvent: () => () => {} },
}));

import HealthPage from './HealthPage.svelte';

it('shows each available notification count, including zero, and omits absent counts', async () => {
  const base = {
    status: 'ok', at: '2026-11-21T14:00:00Z', uptime_s: 0,
    checks: [], pollers: [], engines: [], scheduler: { enabled: false },
    agents: { tokens: [], recent_errors: [], slow_calls: [] },
    latency: { samples: 0, slowest: [] }, automations: { recent: [] },
    queues: { engine_lag: {} }, db: { items: 0, events: 0 },
  };
  get.mockResolvedValueOnce({
    ...base, pushes_failed_1d: 0, push_registrations_removed_7d: 3, questions_open_over_24h: 5,
  }).mockResolvedValueOnce({ ...base, questions_open_over_24h: 1 });
  const target = document.createElement('div');
  const app = mount(HealthPage, { target });
  try {
    await vi.waitFor(() => expect(target.querySelector('[aria-label="Your notifications"]')?.textContent).toContain('0'));
    const counts = () => [...target.querySelectorAll('[aria-label="Your notifications"] strong')].map((n) => n.textContent);
    expect(counts()).toEqual(['0', '3', '5']);
    expect(target.querySelector('[aria-label="Your notifications"]')?.textContent).toContain('Questions open more than 24 hours');
    target.querySelector<HTMLButtonElement>('.work-head button')!.click();
    await vi.waitFor(() => expect(counts()).toEqual(['1']));
  } finally {
    await unmount(app);
  }
});
