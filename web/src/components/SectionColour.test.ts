// @vitest-environment happy-dom
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Section } from '../lib/types';

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>();
  return { ...actual, patch: vi.fn(async () => ({})), get: vi.fn(async () => []) };
});

import { board } from '../lib/store.svelte';
import SectionColour from './SectionColour.svelte';

const SECTION: Section = {
  id: 'S1', name: 'Personal', slug: 'personal', audience: 'personal', position: 0,
  archived_at: null, created_at: '2026-10-20T00:00:00Z', color: '#0090ff',
};

afterEach(() => vi.restoreAllMocks());

function open(section = SECTION) {
  const write = vi.spyOn(board, 'updateSection').mockResolvedValue(undefined);
  const onclose = vi.fn();
  const target = document.createElement('div');
  document.body.appendChild(target);
  const app = mount(SectionColour, { target, props: { section, onclose } });
  flushSync();
  const swatches = [...target.querySelectorAll<HTMLButtonElement>('[data-swatch]')];
  const none = target.querySelector<HTMLButtonElement>('[data-no-colour]')!;
  return {
    write, onclose, swatches, none,
    done: async () => { await unmount(app); target.remove(); },
  };
}

describe('section colour picker', () => {
  it('offers twelve swatches and No colour, marks the current one and starts on it', async () => {
    const p = open();
    expect(p.swatches).toHaveLength(12);
    expect(new Set(p.swatches.map((s) => s.dataset.swatch)).size).toBe(12);
    const marked = p.swatches.filter((s) => s.getAttribute('aria-pressed') === 'true');
    expect(marked.map((s) => s.dataset.swatch)).toEqual(['#0090ff']);
    expect(p.none.getAttribute('aria-pressed')).toBe('false');
    expect(document.activeElement).toBe(marked[0]);
    expect(p.none.textContent?.trim()).toBe('No colour');
    await p.done();
  });

  it('a swatch saves that colour and closes', async () => {
    const p = open();
    p.swatches[0].click();
    expect(p.write).toHaveBeenCalledWith('S1', { color: p.swatches[0].dataset.swatch });
    expect(p.onclose).toHaveBeenCalledOnce();
    await p.done();
  });

  it('No colour sends null; it is the marked one when there is none', async () => {
    const p = open({ ...SECTION, color: null });
    expect(p.none.getAttribute('aria-pressed')).toBe('true');
    expect(p.swatches.some((s) => s.getAttribute('aria-pressed') === 'true')).toBe(false);
    p.none.click();
    expect(p.write).toHaveBeenCalledWith('S1', { color: null });
    expect(p.onclose).toHaveBeenCalledOnce();
    await p.done();
  });

  it('Escape closes without saving', async () => {
    const p = open();
    p.swatches[3].dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(p.onclose).toHaveBeenCalledOnce();
    expect(p.write).not.toHaveBeenCalled();
    await p.done();
  });
});
