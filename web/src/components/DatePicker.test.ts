// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LIST, item } from '../lib/fixtures';

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
import DatePicker from './DatePicker.svelte';

beforeEach(() => {
  board.lists = { L1: LIST };
  // Mon 2026-11-23 10:00 CST (16:00 UTC).
  board.now = new Date('2026-11-23T16:00:00Z');
});

describe('DatePicker and TimePicker', () => {
  it('sets a time in one click with a quick preset and defaults undated cards to today', async () => {
    const onchange = vi.fn();
    const target = document.createElement('div');
    const app = mount(DatePicker, {
      target,
      props: { label: 'Due', value: null, dateOnly: true, onchange },
    });
    try {
      target.querySelector<HTMLButtonElement>('.datepick-value')!.click();
      flushSync();
      const nineAm = [...target.querySelectorAll<HTMLButtonElement>('.time-chip')].find(
        (b) => b.textContent?.trim() === '9 AM',
      );
      expect(nineAm).toBeDefined();
      nineAm!.click();
      flushSync();
      expect(onchange).toHaveBeenCalledWith('2026-11-23T09:00');
    } finally {
      await unmount(app);
    }
  });

  it('lets the user type natural times and pick from the filtered clickable list', async () => {
    const onchange = vi.fn();
    const target = document.createElement('div');
    const app = mount(DatePicker, {
      target,
      props: { label: 'Due', value: '2026-11-23T23:00:00Z', dateOnly: true, onchange },
    });
    try {
      target.querySelector<HTMLButtonElement>('.datepick-value')!.click();
      flushSync();
      const input = target.querySelector<HTMLInputElement>('[aria-label="Due time"]')!;
      input.focus();
      input.value = '2:30p';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();

      const opts = [...target.querySelectorAll<HTMLButtonElement>('[role="option"]')];
      expect(opts.map((o) => o.textContent?.trim())).toEqual(['2:30 PM']);
      opts[0].click();
      flushSync();
      expect(onchange).toHaveBeenCalledWith('2026-11-23T14:30');
    } finally {
      await unmount(app);
    }
  });

  it('clears the time back to a bare date-only value with Clear time', async () => {
    const onchange = vi.fn();
    const target = document.createElement('div');
    const app = mount(DatePicker, {
      target,
      props: { label: 'Due', value: '2026-11-23T15:30:00Z', dateOnly: false, onchange },
    });
    try {
      target.querySelector<HTMLButtonElement>('.datepick-value')!.click();
      flushSync();
      const clear = target.querySelector<HTMLButtonElement>('[aria-label="Clear time"]');
      expect(clear).not.toBeNull();
      clear!.click();
      flushSync();
      expect(onchange).toHaveBeenCalledWith('2026-11-23');
    } finally {
      await unmount(app);
    }
  });

  it('board.setDue updates due_at and due_date_only optimistically', async () => {
    const patchMock = vi.spyOn(board, 'update').mockResolvedValue(true);
    board.items = { I1: item({ id: 'I1', due_at: '2026-11-23T23:00:00Z', due_date_only: true }) };
    await board.setDue('I1', '2026-11-23T09:00');
    expect(board.items.I1.due_date_only).toBe(false);
    expect(board.items.I1.due_at).toBe('2026-11-23T15:00:00.000Z');
    expect(patchMock).toHaveBeenCalledWith('I1', { due_at: '2026-11-23T09:00' });

    await board.setDue('I1', '2026-11-23');
    expect(board.items.I1.due_date_only).toBe(true);
    expect(patchMock).toHaveBeenCalledWith('I1', { due_at: '2026-11-23' });
    patchMock.mockRestore();
  });
});
