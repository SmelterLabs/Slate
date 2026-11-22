// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, expect, it, vi } from 'vitest';
import type { Prefs } from '../lib/types';

const { patch } = vi.hoisted(() => ({ patch: vi.fn() }));
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  get: vi.fn(async (path: string) => path === '/me/digest' ? null : []),
  patch,
}));

import { board } from '../lib/store.svelte';
import NotificationSettings from './NotificationSettings.svelte';

afterEach(() => {
  board.me = null;
  patch.mockReset();
});

it('round-trips the quiet hours and escalation controls through account preferences', async () => {
  let saved: Prefs = {};
  patch.mockImplementation(async (_path: string, changes: Prefs) => {
    saved = { ...saved, ...changes };
    return structuredClone(saved);
  });
  board.me = {
    account: { id: 'P1', handle: 'alex', display_name: 'Alex', kind: 'person' },
    token_id: 'T1', scopes: ['write'], prefs: {},
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  let app = mount(NotificationSettings, { target });

  const input = (label: string) => target.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
  const enable = 'Enable quiet hours';
  const from = 'Quiet hours from';
  const to = 'Quiet hours to';
  const minutes = 'If I have not answered, alert my phone after';
  const save = async (label: string, value: string | boolean) => {
    const field = input(label);
    if (typeof value === 'boolean') field.checked = value;
    else field.value = value;
    field.dispatchEvent(new Event('change', { bubbles: true }));
    await Promise.resolve();
    flushSync();
  };

  try {
    expect(target.querySelector('[aria-label="Nudge again about unread chat"]')).toBeNull();
    expect(input(enable).checked).toBe(false);
    expect(input(minutes).value).toBe('120');
    await save(enable, true);
    await save(from, '21:30');
    await save(to, '06:45');
    await save(minutes, '75');
    expect(patch).toHaveBeenCalledWith('/me/prefs', { quiet_hours: { from: '21:30', to: '06:45' } });
    expect(patch).toHaveBeenCalledWith('/me/prefs', { escalate_after_minutes: 75 });

    await unmount(app);
    board.me.prefs = structuredClone(saved);
    app = mount(NotificationSettings, { target });
    expect(input(enable).checked).toBe(true);
    expect([input(from).value, input(to).value, input(minutes).value]).toEqual(['21:30', '06:45', '75']);
    await save(enable, false);
    await save(minutes, '0');
    expect(patch).toHaveBeenCalledWith('/me/prefs', { quiet_hours: null });
    expect(patch).toHaveBeenCalledWith('/me/prefs', { escalate_after_minutes: 0 });
  } finally {
    await unmount(app);
    target.remove();
  }
});

it('reports invalid or empty settings and restores the saved inputs', async () => {
  board.me = {
    account: { id: 'P1', handle: 'alex', display_name: 'Alex', kind: 'person' },
    token_id: 'T1', scopes: ['write'],
    prefs: { escalate_after_minutes: 75, quiet_hours: { from: '21:30', to: '06:45' } },
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(NotificationSettings, { target });

  try {
    const minutes = target.querySelector<HTMLInputElement>('input[type="number"]')!;
    const times = [...target.querySelectorAll<HTMLInputElement>('input[type="time"]')];
    for (const [field, saved, invalidValues] of [
      [minutes, '75', ['-5', '1.5', '']],
      [times[0], '21:30', ['']],
      [times[1], '06:45', ['']],
    ] as const) {
      const report = vi.spyOn(field, 'reportValidity');
      for (const invalid of invalidValues) {
        field.value = invalid;
        field.dispatchEvent(new Event('change', { bubbles: true }));
        expect(report).toHaveLastReturnedWith(false);
        expect(field.value).toBe(saved);
      }
      expect(report).toHaveBeenCalledTimes(invalidValues.length);
    }
    expect(patch).not.toHaveBeenCalled();
  } finally {
    await unmount(app);
    target.remove();
  }
});
