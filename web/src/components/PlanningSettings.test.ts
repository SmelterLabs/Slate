// @vitest-environment happy-dom
import { expect, it } from 'vitest';
import { freshBoard, ME, routes, settle, startLive, tearDown, writes } from '../lib/board/testkit';
import type { Prefs } from '../lib/types';

it('loads the planning defaults and saves both settings through account prefs', async () => {
  const board = await freshBoard();
  const { mount, unmount } = await import('svelte');
  let saved: Prefs = {};
  routes.set('/me', () => ({ ...ME, prefs: saved }));
  routes.set('PATCH /me/prefs', (body) => (saved = { ...saved, ...(body as Prefs) }));
  await startLive(board);
  const { default: NotificationSettings } = await import('./NotificationSettings.svelte');
  const target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(NotificationSettings, { target });
  try {
    const lead = target.querySelector<HTMLInputElement>('[aria-label="Hide cards due more than N days away"]')!;
    const sweep = target.querySelector<HTMLSelectElement>('[aria-label="Weekly sweep day"]')!;
    expect(lead.value).toBe('14');
    expect(sweep.selectedOptions[0]?.textContent).toBe('Friday');

    lead.value = '0';
    lead.dispatchEvent(new Event('change', { bubbles: true }));
    await settle();
    sweep.value = '1';
    sweep.dispatchEvent(new Event('change', { bubbles: true }));
    await settle();
    expect(writes()).toEqual([
      { method: 'PATCH', path: '/me/prefs', body: { lead_days: 0 } },
      { method: 'PATCH', path: '/me/prefs', body: { sweep_weekday: 1 } },
    ]);
    expect(board.prefs).toMatchObject({ lead_days: 0, sweep_weekday: 1 });
  } finally {
    await unmount(app);
    target.remove();
    tearDown(board);
  }
});
