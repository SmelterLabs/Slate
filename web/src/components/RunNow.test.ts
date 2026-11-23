// @vitest-environment happy-dom
import { mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST, item } from '../lib/fixtures';
import { defaultConfig } from '../lib/views';

const CFG = defaultConfig({ kind: 'list', id: 'L1' });

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return {
    ...actual,
    api: vi.fn(async () => undefined),
    get: vi.fn(async () => []),
    post: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
  };
});

import { board } from '../lib/store.svelte';
import Card from './Card.svelte';
import ItemRow from './ItemRow.svelte';
import RunNow from './RunNow.svelte';

const AGENT = { id: 'sc', handle: 'my-agent', display_name: 'My Agent', kind: 'agent' as const };

beforeEach(() => {
  board.me = {
    account: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person', role: 'owner' },
    token_id: 'T1',
    scopes: ['admin'],
    prefs: {},
  };
  board.accounts = {
    me: { id: 'me', handle: 'alex', display_name: 'Alex', kind: 'person' },
    sc: { ...AGENT, wake: 'push' },
  };
  board.lists = { L1: LIST };
  // 2026-11-23 00:05 CST (06:05 UTC).
  board.now = new Date('2026-11-23T06:05:00Z');
  board.runRequested = {};
});

describe('Scheduled run pill on RunNow, Card, and ItemRow', () => {
  // Date-only Mon, Nov 23 (17:00 CST = 23:00 UTC); runs at 6:00 AM CST.
  const scheduled = item({
    id: 'I1',
    title: 'Check the build',
    due_at: '2026-11-23T23:00:00Z',
    due_date_only: true,
    assignees: [AGENT],
  });

  it('RunNow shows the scheduled time beside the Run now button', async () => {
    const target = document.createElement('div');
    const app = mount(RunNow, { target, props: { item: scheduled } });
    try {
      const pill = target.querySelector('[data-run-state="scheduled"]');
      expect(pill?.textContent?.trim()).toBe('Runs at 6:00 AM');
      const btn = target.querySelector<HTMLButtonElement>('button.run-now');
      expect(btn?.textContent?.trim()).toBe('Run now');
    } finally {
      await unmount(app);
    }
  });

  it('Card and ItemRow show the scheduled pill without adding a duplicate Run now button', async () => {
    const cardTarget = document.createElement('div');
    const rowTarget = document.createElement('div');
    const cardApp = mount(Card, { target: cardTarget, props: { item: scheduled, config: CFG } });
    const rowApp = mount(ItemRow, { target: rowTarget, props: { item: scheduled, config: CFG } });
    try {
      expect(cardTarget.querySelector('[data-run-state="scheduled"]')?.textContent?.trim()).toBe('Runs at 6:00 AM');
      expect(cardTarget.querySelector('.card-agent button.run-now')).toBeNull();
      expect(rowTarget.querySelector('[data-run-state="scheduled"]')?.textContent?.trim()).toBe('Runs at 6:00 AM');
      expect(rowTarget.querySelector('button.run-now')).toBeNull();
    } finally {
      await Promise.all([unmount(cardApp), unmount(rowApp)]);
    }
  });

  it('omits the scheduled pill for pull/manual agents, reference lists, and undated cards', async () => {
    for (const setup of [
      () => {
        board.accounts.sc = { ...AGENT, wake: 'manual' };
        return scheduled;
      },
      () => {
        board.accounts.sc = { ...AGENT, wake: 'push' };
        board.lists.L1 = { ...LIST, reference: true };
        return scheduled;
      },
      () => {
        board.lists.L1 = LIST;
        return { ...scheduled, due_at: null };
      },
    ]) {
      const card = setup();
      const target = document.createElement('div');
      const app = mount(RunNow, { target, props: { item: card } });
      try {
        expect(target.querySelector('[data-run-state="scheduled"]')).toBeNull();
      } finally {
        await unmount(app);
      }
    }
  });
});
