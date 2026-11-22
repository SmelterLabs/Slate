// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Prefs } from '../lib/types';

const { get, patch, post } = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn(), post: vi.fn() }));
const answer = async (path: string) => (path === '/me/digest' ? null : []);
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  get,
  patch,
  post,
}));

import { ApiError } from '../lib/api';
import { board } from '../lib/store.svelte';
import NotificationSettings from './NotificationSettings.svelte';

beforeEach(() => get.mockImplementation(answer));
afterEach(() => {
  board.me = null;
  get.mockReset();
  patch.mockReset();
  post.mockReset();
  vi.unstubAllGlobals();
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

describe('this device', () => {
  const ALEX = { account: { id: 'P1', handle: 'alex', display_name: 'Alex', kind: 'person' as const }, token_id: 'T1', scopes: ['write'], prefs: {} };
  const OFFLINE = () => new ApiError(0, 'You are offline: showing what was already loaded');
  let subscribed = false;

  /** A browser that can show notifications; `subscribed` says whether this one is. */
  function pushBrowser(permission: NotificationPermission) {
    const sub = { endpoint: 'https://push.example.com/1', unsubscribe: vi.fn(async () => { subscribed = false; return true; }) };
    vi.stubGlobal('PushManager', function PushManager() {});
    vi.stubGlobal('Notification', { permission });
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: { getRegistration: async () => ({ pushManager: { getSubscription: async () => (subscribed ? sub : null) } }) },
    });
  }

  async function open() {
    board.me = structuredClone(ALEX);
    const target = document.createElement('div');
    document.body.appendChild(target);
    const app = mount(NotificationSettings, { target });
    const state = () => target.querySelector('.ns-state')?.textContent ?? '';
    const button = (name: string) =>
      [...target.querySelectorAll('button')].find((b) => b.textContent?.trim() === name);
    return { state, button, close: async () => { await unmount(app); target.remove(); } };
  }

  it('says it could not check, offers to check again, and adds no toast while offline', async () => {
    pushBrowser('default');
    let reachable = false;
    get.mockImplementation(async (path: string) => {
      if (path !== '/push/key') return answer(path);
      if (!reachable) throw OFFLINE();
      return { enabled: true, public_key: 'BKey' };
    });
    const win = await open();
    try {
      await vi.waitFor(() => expect(win.state()).toBe('Could not check notifications for this device.'));
      expect(board.toast).toBeNull();
      reachable = true;
      win.button('Check again')!.click();
      await vi.waitFor(() => expect(win.state()).toBe('Off for this device.'));
    } finally {
      await win.close();
    }
  });

  it('does not still say "on" after turning off failed to reach Slate', async () => {
    pushBrowser('granted');
    subscribed = true;
    let reachable = true;
    get.mockImplementation(async (path: string) => {
      if (path !== '/push/key') return answer(path);
      if (!reachable) throw OFFLINE();
      return { enabled: true, public_key: 'BKey' };
    });
    post.mockImplementation(async () => { throw OFFLINE(); });
    const win = await open();
    try {
      await vi.waitFor(() => expect(win.state()).toBe('On for this device.'));
      reachable = false;
      win.button('Turn off here')!.click();
      await vi.waitFor(() => expect(win.state()).toBe('Could not check notifications for this device.'));
      expect(subscribed).toBe(false);
    } finally {
      await win.close();
    }
  });
});
