// @vitest-environment happy-dom
import { expect, it, vi } from 'vitest';
import { FakeEventSource, freshBoard, ME, routes, startLive, tearDown } from '../lib/board/testkit';
import { ev } from '../lib/fixtures';
import type { Section } from '../lib/types';

it('shows the server next step and updates to No next step after a live completion', async () => {
  const board = await freshBoard();
  const { flushSync, mount, unmount } = await import('svelte');
  const { shortDate } = await import('../lib/format');
  const step = { item_id: 'I1', title: 'Send the draft', assignee_ids: [ME.account.id], due_at: '2026-11-02T22:00:00Z' };
  const section: Section = {
    id: 'S1', name: 'Launch', slug: 'launch', audience: 'work', position: 0,
    created_at: '2026-10-01T00:00:00Z', archived_at: null, project: true, next_step: step,
  };
  routes.set('/sections', () => [section]);
  await startLive(board);
  board.now = new Date('2026-10-29T12:00:00Z');
  const { default: Nav } = await import('./Nav.svelte');
  const target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(Nav, { target });
  try {
    const next = target.querySelector('[data-next-step="S1"]');
    expect(next?.textContent?.trim()).toBe(`Next: Send the draft · Alex · ${shortDate(step.due_at, board.now)}`);
    expect(next?.getAttribute('href')).toBe('#/section/S1?task=I1');
    routes.set('/sections', () => [{ ...section, next_step: null }]);
    FakeEventSource.last.emit(ev({ type: 'item.status_changed', field: 'status_id', after: 'st_done' }));
    await vi.advanceTimersByTimeAsync(400);
    flushSync();
    expect(target.querySelector('[data-next-step="S1"]')).toBeNull();
    expect(target.querySelector('[data-no-next-step="S1"]')?.textContent?.trim()).toBe('No next step');
  } finally {
    await unmount(app);
    target.remove();
    tearDown(board);
  }
});
